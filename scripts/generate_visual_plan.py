#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from videocreator.visual_plan import audit_visual_plan

MAX_SEGMENT_DURATION_MS = 9000
MAX_SEGMENT_TEXT_CHARS = 34
CLAUSE_SPLIT_RE = re.compile(r'([^。！？；，]+[。！？；，]?)')
SEMANTIC_CLAUSE_SPLIT_RE = re.compile(r'([^。！？；]+[。！？；]?)')


def parse_srt_timestamp(value: str) -> int:
    hh, mm, rest = value.split(':')
    ss, ms = re.split(r'[,.]', rest)
    return (int(hh) * 3600 + int(mm) * 60 + int(ss)) * 1000 + int(ms)


def format_srt_timestamp(ms: int) -> str:
    if ms < 0:
        ms = 0
    hh = ms // 3600000
    mm = (ms % 3600000) // 60000
    ss = (ms % 60000) // 1000
    msec = ms % 1000
    return f'{hh:02d}:{mm:02d}:{ss:02d},{msec:03d}'


def parse_srt(text: str) -> list[dict[str, Any]]:
    blocks = re.split(r'\n\s*\n', text.strip(), flags=re.MULTILINE)
    segments = []
    for block in blocks:
        lines = [line.strip('\ufeff') for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        if re.fullmatch(r'\d+', lines[0]):
            lines = lines[1:]
        if not lines or '-->' not in lines[0]:
            continue
        start, end = [item.strip() for item in lines[0].split('-->')]
        content = ' '.join(line.strip() for line in lines[1:]).strip()
        if not content:
            continue
        start_ms = parse_srt_timestamp(start)
        end_ms = parse_srt_timestamp(end)
        segments.append({
            'segment_id': f'sub-{len(segments) + 1:03d}',
            'start': start,
            'end': end,
            'start_ms': start_ms,
            'end_ms': end_ms,
            'duration_seconds': round((end_ms - start_ms) / 1000, 3),
            'text': content,
        })
    return segments


def split_into_clauses(text: str) -> list[str]:
    parts = [item.strip() for item in CLAUSE_SPLIT_RE.findall(text) if item.strip()]
    return parts or [text.strip()]


def split_into_semantic_clauses(text: str) -> list[str]:
    parts = [item.strip() for item in SEMANTIC_CLAUSE_SPLIT_RE.findall(text) if item.strip()]
    return parts or [text.strip()]


def split_long_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refined: list[dict[str, Any]] = []
    for segment in segments:
        duration_ms = segment['end_ms'] - segment['start_ms']
        text = segment['text'].strip()
        semantic_clauses = split_into_semantic_clauses(text)
        is_long = duration_ms > MAX_SEGMENT_DURATION_MS or len(text) > MAX_SEGMENT_TEXT_CHARS
        clauses = semantic_clauses if len(semantic_clauses) > 1 else split_into_clauses(text)
        if len(clauses) <= 1 or (not is_long and len(semantic_clauses) <= 1):
            refined.append(segment)
            continue
        total_weight = sum(max(len(clause), 1) for clause in clauses)
        cursor = segment['start_ms']
        for idx, clause in enumerate(clauses):
            weight = max(len(clause), 1)
            if idx == len(clauses) - 1:
                end_ms = segment['end_ms']
            else:
                share = round(duration_ms * weight / total_weight)
                end_ms = min(segment['end_ms'], cursor + max(share, 600))
            refined.append({
                'segment_id': 'pending',
                'start_ms': cursor,
                'end_ms': end_ms,
                'start': format_srt_timestamp(cursor),
                'end': format_srt_timestamp(end_ms),
                'duration_seconds': round((end_ms - cursor) / 1000, 3),
                'text': clause,
                'source_segment_id': segment['segment_id'],
            })
            cursor = end_ms
    for idx, segment in enumerate(refined, 1):
        segment['segment_id'] = f'sub-{idx:03d}'
    return refined


def call_compatible_openai(base_url: str, api_key: str, model: str, messages: list[dict[str, str]]) -> str:
    endpoint = base_url.rstrip('/')
    if not endpoint.endswith('/chat/completions'):
        endpoint = endpoint + '/chat/completions'
    payload = json.dumps({'model': model, 'messages': messages, 'temperature': 0.3}).encode('utf-8')
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(exc.read().decode('utf-8', errors='replace')) from exc
    choices = body.get('choices') or []
    if not choices:
        raise RuntimeError(f'No choices in LLM response: {body}')
    message = choices[0].get('message') or {}
    content = message.get('content', '')
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return '\n'.join(item.get('text', '') for item in content if isinstance(item, dict)).strip()
    raise RuntimeError('Unsupported LLM content format')


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'(\{[\s\S]*\})', text)
        if not match:
            raise
        return json.loads(match.group(1))


def validate_scene_ranges(scene_ranges: list[list[str]], ordered_ids: list[str]) -> list[list[str]]:
    id_to_index = {segment_id: idx for idx, segment_id in enumerate(ordered_ids)}
    covered: list[str] = []
    normalized: list[list[str]] = []
    for ids in scene_ranges:
        if not ids:
            raise RuntimeError('Scene is missing subtitle_segment_ids')
        indices = [id_to_index[item] for item in ids if item in id_to_index]
        if len(indices) != len(ids):
            raise RuntimeError(f'Unknown subtitle segment id in scene: {ids}')
        expected = list(range(indices[0], indices[-1] + 1))
        if indices != expected:
            raise RuntimeError(f'Scene must reference contiguous subtitle segments: {ids}')
        normalized.append(ids)
        covered.extend(ids)
    if covered != ordered_ids:
        raise RuntimeError('Scenes must cover all subtitle segments exactly once and in order')
    return normalized


def normalize_plan(subtitle_segments: list[dict[str, Any]], plan: dict[str, Any], topic: str, category: str) -> dict[str, Any]:
    scenes = plan.get('scenes') or plan.get('segments') or []
    if not scenes:
        raise RuntimeError('Plan did not return scenes')
    ordered_ids = [segment['segment_id'] for segment in subtitle_segments]
    scene_ranges = validate_scene_ranges([list(scene.get('subtitle_segment_ids') or []) for scene in scenes], ordered_ids)
    by_id = {segment['segment_id']: segment for segment in subtitle_segments}
    normalized = []
    for idx, (scene, subtitle_ids) in enumerate(zip(scenes, scene_ranges), 1):
        parts = [by_id[item] for item in subtitle_ids]
        start_ms = parts[0]['start_ms']
        end_ms = (
            by_id[scene_ranges[idx][0]]['start_ms']
            if idx < len(scene_ranges)
            else parts[-1]['end_ms']
        )
        text = ''.join(part['text'] for part in parts).strip()
        search_queries = scene.get('search_queries') or {}
        generation_prompts = scene.get('generation_prompts') or {}
        material_type = scene.get('material_type', 'image')
        presentation_mode = scene.get('presentation_mode') or {
            'video': 'footage', 'image': 'still', 'subtitle_only': 'subtitle_only'
        }.get(material_type, 'still')
        slots = list(scene.get('slots') or [])
        if not slots and presentation_mode in {'footage', 'still'}:
            slots = [{'role': 'primary', 'required_type': 'video' if presentation_mode == 'footage' else 'image'}]
        normalized.append({
            'segment_id': f'scene-{idx:03d}',
            'subtitle_segment_ids': subtitle_ids,
            'start': parts[0]['start'],
            'end': format_srt_timestamp(end_ms),
            'start_ms': start_ms,
            'end_ms': end_ms,
            'duration_seconds': round((end_ms - start_ms) / 1000, 3),
            'text': text,
            'subtitle_text': text.rstrip('。！？!?；;，,：:'),
            'subtitle_blocks': len(subtitle_ids),
            'brief': scene.get('brief') or text[:18],
            'material_type': scene.get('material_type', 'image'),
            'asset_strategy': scene.get('asset_strategy', 'search_first'),
            'visual_role': scene.get('visual_role', 'illustrative'),
            'search_queries': {
                'image': list(search_queries.get('image') or []),
                'video': list(search_queries.get('video') or []),
            },
            'generation_prompts': {
                'image': generation_prompts.get('image', ''),
                'video': generation_prompts.get('video', ''),
            },
            'transition': scene.get('transition', 'cut'),
            'notes': scene.get('notes', ''),
            'presentation_mode': presentation_mode,
            'slots': slots,
            'entity': scene.get('entity'),
            'explainer': scene.get('explainer'),
            'long_hold_reason': scene.get('long_hold_reason', ''),
        })
        if normalized[-1]['material_type'] not in {'video', 'image', 'subtitle_only'}:
            raise RuntimeError(f"Unsupported material_type: {normalized[-1]['material_type']}")
    return {
        'schema_version': 2,
        'topic': topic,
        'category': category,
        'source_subtitle_segment_count': len(subtitle_segments),
        'segment_count': len(normalized),
        'segments': normalized,
    }


def build_planning_prompt(
    subtitle_segments: list[dict[str, Any]],
    *,
    topic: str,
    category: str,
    draft_text: str,
    pacing: dict[str, Any],
    subtitle_policy: dict[str, Any],
    audit_feedback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    duration_ms = subtitle_segments[-1]['end_ms'] - subtitle_segments[0]['start_ms']
    minimum_scene_count = math.ceil(duration_ms * float(pacing['min_shots_per_minute']) / 60000)
    prompt = {
        'topic': topic,
        'category': category,
        'draft_excerpt': draft_text[:6000],
        'draft_paragraphs': [item.strip() for item in re.split(r'\n\s*\n', draft_text) if item.strip()],
        'subtitle_segments': subtitle_segments,
        'planning_contract': {
            'minimum_scene_count': minimum_scene_count,
            'target_duration_ms': pacing['target_duration_ms'],
            'soft_max_duration_ms': pacing['soft_max_duration_ms'],
            'hard_max_duration_ms': pacing['hard_max_duration_ms'],
            'max_subtitle_blocks': pacing['max_subtitle_blocks'],
            'max_chinese_chars': pacing['max_chinese_chars'],
            'min_shots_per_minute': pacing['min_shots_per_minute'],
            'max_semantic_beats_per_scene': pacing.get('max_semantic_beats_per_scene', 1),
            'subtitle_policy': subtitle_policy,
            'rules': [
                'Treat each change of subject, location, action, time, example, or argument as a new semantic beat.',
                'Never keep one scene across semantic beats that require different visual evidence.',
                'Adjacent subtitle segments may be grouped only when they describe the same subject and action.',
                'All numeric limits are mandatory; long_hold_reason does not waive the hard maximum.',
            ],
        },
        'required_output': {
            'topic': topic,
            'category': category,
            'segment_count': f'integer greater than or equal to {minimum_scene_count}',
            'scene_granularity_rule': 'exactly one visual semantic beat per scene',
            'segments': [{
                'subtitle_segment_ids': ['sub-001'],
                'brief': 'scene brief in Chinese',
                'material_type': 'video|image|subtitle_only',
                'presentation_mode': 'footage|still|entity_card|explainer|subtitle_only',
                'slots': [{'role': 'primary|background|display', 'required_type': 'image|video'}],
                'entity': {'primary_label': 'required for entity_card', 'secondary_label': 'optional'},
                'explainer': {'kind': 'formula|process|list|function|score|code|quote', 'items': ['declarative content']},
                'asset_strategy': 'search_first|generate_only|subtitle_only',
                'visual_role': 'evidential|illustrative|abstract|atmospheric',
                'search_queries': {'image': ['...'], 'video': ['...']},
                'generation_prompts': {'image': '...', 'video': '...'},
                'transition': 'cut|dissolve|hold',
                'notes': 'short reason',
            }],
        },
    }
    if audit_feedback:
        prompt['audit_feedback'] = audit_feedback
        prompt['repair_instruction'] = 'Return a complete replacement plan that fixes every audit error.'
    return prompt


def plan_visual_scenes(
    subtitle_segments: list[dict[str, Any]],
    *,
    topic: str,
    category: str,
    draft_text: str,
    skill_text: str,
    pacing: dict[str, Any],
    subtitle_policy: dict[str, Any],
    invoke: Any,
    max_attempts: int = 3,
) -> dict[str, Any]:
    audit_feedback = None
    for _attempt in range(max_attempts):
        prompt = build_planning_prompt(
            subtitle_segments,
            topic=topic,
            category=category,
            draft_text=draft_text,
            pacing=pacing,
            subtitle_policy=subtitle_policy,
            audit_feedback=audit_feedback,
        )
        messages = [
            {'role': 'system', 'content': skill_text},
            {'role': 'user', 'content': json.dumps(prompt, ensure_ascii=False, indent=2)},
        ]
        normalized = normalize_plan(subtitle_segments, extract_json(invoke(messages)), topic, category)
        audit_feedback = audit_visual_plan(normalized, pacing, subtitle_policy)
        if audit_feedback['ok']:
            return normalized
    raise RuntimeError(
        f"Visual plan failed audit after {max_attempts} attempts: "
        f"{json.dumps(audit_feedback['errors'], ensure_ascii=False)}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Generate a semantic visual plan from subtitle timing and article text')
    parser.add_argument('--workflow-config', required=True)
    parser.add_argument('--srt-file', required=True)
    parser.add_argument('--draft-file', default='')
    parser.add_argument('--topic', default='')
    parser.add_argument('--category', default='general')
    parser.add_argument('--output', required=True)
    parser.add_argument('--skill-file', default='')
    parser.add_argument('--pacing-file', default='')
    parser.add_argument('--subtitle-policy-file', default='')
    parser.add_argument('--max-attempts', type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workflow_config = json.loads(Path(args.workflow_config).read_text(encoding='utf-8'))
    llm = workflow_config['llm']
    api_key = os.environ.get(llm['api_key_env'], '')
    if not api_key:
        raise RuntimeError(f"Missing API key env: {llm['api_key_env']}")
    skill_path = Path(args.skill_file) if args.skill_file else Path(args.workflow_config).resolve().parent / workflow_config['visual_plan']['skill_path_project']
    skill_text = skill_path.read_text(encoding='utf-8')
    srt_text = Path(args.srt_file).read_text(encoding='utf-8-sig')
    raw_segments = parse_srt(srt_text)
    subtitle_segments = split_long_segments(raw_segments)
    if not subtitle_segments:
        raise RuntimeError('No segments found in SRT file.')
    draft_text = Path(args.draft_file).read_text(encoding='utf-8') if args.draft_file else ''
    if not args.pacing_file or not args.subtitle_policy_file:
        raise RuntimeError('Both --pacing-file and --subtitle-policy-file are required')
    pacing = json.loads(Path(args.pacing_file).read_text(encoding='utf-8'))
    subtitle_policy = json.loads(Path(args.subtitle_policy_file).read_text(encoding='utf-8'))
    normalized = plan_visual_scenes(
        subtitle_segments,
        topic=args.topic,
        category=args.category,
        draft_text=draft_text,
        skill_text=skill_text,
        pacing=pacing,
        subtitle_policy=subtitle_policy,
        invoke=lambda messages: call_compatible_openai(llm['base_url'], api_key, llm['model'], messages),
        max_attempts=args.max_attempts,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(output_path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
