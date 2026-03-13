#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

MAX_SEGMENT_DURATION_MS = 9000
MAX_SEGMENT_TEXT_CHARS = 34
CLAUSE_SPLIT_RE = re.compile(r'([^。！？；，]+[。！？；，]?)')


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


def split_long_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refined: list[dict[str, Any]] = []
    for segment in segments:
        duration_ms = segment['end_ms'] - segment['start_ms']
        text = segment['text'].strip()
        clauses = split_into_clauses(text)
        if len(clauses) <= 1 or (duration_ms <= MAX_SEGMENT_DURATION_MS and len(text) <= MAX_SEGMENT_TEXT_CHARS):
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
        end_ms = parts[-1]['end_ms']
        text = ''.join(part['text'] for part in parts).strip()
        search_queries = scene.get('search_queries') or {}
        generation_prompts = scene.get('generation_prompts') or {}
        normalized.append({
            'segment_id': f'scene-{idx:03d}',
            'subtitle_segment_ids': subtitle_ids,
            'start': parts[0]['start'],
            'end': parts[-1]['end'],
            'start_ms': start_ms,
            'end_ms': end_ms,
            'duration_seconds': round((end_ms - start_ms) / 1000, 3),
            'text': text,
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
        })
        if normalized[-1]['material_type'] not in {'video', 'image', 'subtitle_only'}:
            raise RuntimeError(f"Unsupported material_type: {normalized[-1]['material_type']}")
    return {
        'topic': topic,
        'category': category,
        'source_subtitle_segment_count': len(subtitle_segments),
        'segment_count': len(normalized),
        'segments': normalized,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Generate a semantic visual plan from subtitle timing and article text')
    parser.add_argument('--workflow-config', required=True)
    parser.add_argument('--srt-file', required=True)
    parser.add_argument('--draft-file', default='')
    parser.add_argument('--topic', default='')
    parser.add_argument('--category', default='general')
    parser.add_argument('--output', required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workflow_config = json.loads(Path(args.workflow_config).read_text(encoding='utf-8'))
    llm = workflow_config['llm']
    api_key = os.environ.get(llm['api_key_env'], '')
    if not api_key:
        raise RuntimeError(f"Missing API key env: {llm['api_key_env']}")
    skill_path = Path(args.workflow_config).resolve().parent / workflow_config['visual_plan']['skill_path_project']
    skill_text = skill_path.read_text(encoding='utf-8')
    srt_text = Path(args.srt_file).read_text(encoding='utf-8-sig')
    raw_segments = parse_srt(srt_text)
    subtitle_segments = split_long_segments(raw_segments)
    if not subtitle_segments:
        raise RuntimeError('No segments found in SRT file.')
    draft_text = Path(args.draft_file).read_text(encoding='utf-8') if args.draft_file else ''
    draft_paragraphs = [item.strip() for item in re.split(r'\n\s*\n', draft_text) if item.strip()]
    prompt = {
        'topic': args.topic,
        'category': args.category,
        'draft_excerpt': draft_text[:6000],
        'draft_paragraphs': draft_paragraphs,
        'subtitle_segments': subtitle_segments,
        'required_output': {
            'topic': args.topic,
            'category': args.category,
            'segment_count': 'number of semantic scenes, not subtitle count',
            'scene_granularity_rule': 'prefer one semantic beat per scene; if adjacent clauses need different visuals, split them',
            'segments': [
                {
                    'subtitle_segment_ids': ['sub-001', 'sub-002'],
                    'brief': 'scene brief in Chinese',
                    'material_type': 'video|image|subtitle_only',
                    'asset_strategy': 'search_first|generate_only|subtitle_only',
                    'visual_role': 'evidential|illustrative|abstract|atmospheric',
                    'search_queries': {'image': ['...'], 'video': ['...']},
                    'generation_prompts': {'image': '...', 'video': '...'},
                    'transition': 'cut|dissolve|hold',
                    'notes': 'short reason'
                }
            ]
        }
    }
    messages = [
        {'role': 'system', 'content': skill_text},
        {'role': 'user', 'content': json.dumps(prompt, ensure_ascii=False, indent=2)}
    ]
    response = call_compatible_openai(llm['base_url'], api_key, llm['model'], messages)
    plan = extract_json(response)
    normalized = normalize_plan(subtitle_segments, plan, args.topic, args.category)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(output_path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
