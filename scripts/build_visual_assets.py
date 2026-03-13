#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


USER_AGENT = 'ChaosMuseum/1.0'


def slugify(value: str) -> str:
    value = re.sub(r'\s+', '-', value.strip().lower())
    value = re.sub(r'[^a-z0-9\-\u4e00-\u9fff]+', '-', value)
    value = re.sub(r'-+', '-', value).strip('-')
    return value or 'segment'


def timestamp_slug(value: str) -> str:
    return value.replace(':', '-').replace(',', '-')


def read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def download_to(url: str, output_path: Path) -> None:
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        output_path.write_bytes(resp.read())


def search_wikimedia_image(queries: list[str]) -> dict[str, Any] | None:
    for query in queries:
        params = urllib.parse.urlencode({
            'action': 'query',
            'format': 'json',
            'generator': 'search',
            'gsrsearch': query,
            'gsrnamespace': '6',
            'gsrlimit': '1',
            'prop': 'imageinfo',
            'iiprop': 'url',
        })
        url = f'https://commons.wikimedia.org/w/api.php?{params}'
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode('utf-8'))
        except Exception:
            continue
        pages = ((payload.get('query') or {}).get('pages') or {})
        for page in pages.values():
            imageinfo = page.get('imageinfo') or []
            if not imageinfo:
                continue
            info = imageinfo[0]
            direct_url = info.get('url')
            if direct_url:
                return {'provider': 'wikimedia', 'url': direct_url, 'title': page.get('title', query), 'query': query}
    return None


def search_pexels_video(queries: list[str], api_key: str) -> dict[str, Any] | None:
    if not api_key:
        return None
    for query in queries:
        params = urllib.parse.urlencode({'query': query, 'per_page': 1, 'orientation': 'landscape', 'size': 'medium'})
        url = f'https://api.pexels.com/videos/search?{params}'
        req = urllib.request.Request(url, headers={'Authorization': api_key, 'User-Agent': USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode('utf-8'))
        except Exception:
            continue
        videos = payload.get('videos') or []
        if not videos:
            continue
        files = videos[0].get('video_files') or []
        if not files:
            continue
        selected = sorted(files, key=lambda item: (item.get('width', 0) * item.get('height', 0)), reverse=True)[0]
        return {'provider': 'pexels', 'url': selected.get('link'), 'title': query, 'query': query}
    return None


def run_generator(script_path: Path, config_path: Path, prompt: str, output_path: Path) -> None:
    cmd = [
        os.environ.get('PYTHON_EXECUTABLE', os.sys.executable),
        str(script_path),
        '--config', str(config_path),
        '--prompt', prompt,
        '--output', str(output_path),
    ]
    subprocess.run(cmd, check=True)


def choose_asset_type(segment: dict[str, Any]) -> list[str]:
    material_type = segment.get('material_type', 'image')
    if material_type == 'video':
        return ['video']
    if material_type == 'image':
        return ['image']
    return []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Build visual assets from a visual plan')
    parser.add_argument('--plan-file', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--manifest-file', required=True)
    parser.add_argument('--image-script', required=True)
    parser.add_argument('--video-script', required=True)
    parser.add_argument('--jimeng-config', required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan = read_json(args.plan_file)
    config = read_json(args.config)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pexels_env = ((config.get('search') or {}).get('pexels_api_key_env')) or 'PEXELS_API_KEY'
    pexels_api_key = os.environ.get(pexels_env, '')
    image_script = Path(args.image_script)
    video_script = Path(args.video_script)
    jimeng_config = Path(args.jimeng_config)

    manifest = {'topic': plan.get('topic', ''), 'segment_count': plan.get('segment_count', 0), 'segments': []}

    for segment in plan.get('segments', []):
        start_slug = timestamp_slug(segment['start'])
        brief_slug = slugify(segment.get('brief', segment['segment_id']))[:48]
        record = {
            'segment_id': segment['segment_id'],
            'start': segment['start'],
            'end': segment['end'],
            'text': segment['text'],
            'brief': segment.get('brief', ''),
            'status': 'pending',
            'asset_path': '',
            'asset_type': '',
            'source_type': '',
            'provider': '',
            'source_url': '',
        }
        if segment.get('material_type') == 'subtitle_only' or segment.get('asset_strategy') == 'subtitle_only':
            record['status'] = 'skipped'
            record['asset_type'] = 'subtitle_only'
            record['source_type'] = 'subtitle_only'
            manifest['segments'].append(record)
            continue

        selected = None
        preferred_types = choose_asset_type(segment)
        for asset_type in preferred_types:
            queries = ((segment.get('search_queries') or {}).get(asset_type) or [])
            if segment.get('asset_strategy') != 'generate_only':
                if asset_type == 'image' and (config.get('search') or {}).get('enable_image_search', True):
                    result = search_wikimedia_image(queries)
                    if result:
                        ext = Path(urllib.parse.urlparse(result['url']).path).suffix or '.jpg'
                        output_path = output_dir / f'{start_slug}_{brief_slug}{ext}'
                        download_to(result['url'], output_path)
                        selected = ('image', 'search', result['provider'], result['url'], output_path)
                        break
                if asset_type == 'video' and (config.get('search') or {}).get('enable_video_search', True):
                    result = search_pexels_video(queries, pexels_api_key)
                    if result and result.get('url'):
                        ext = Path(urllib.parse.urlparse(result['url']).path).suffix or '.mp4'
                        output_path = output_dir / f'{start_slug}_{brief_slug}{ext}'
                        download_to(result['url'], output_path)
                        selected = ('video', 'search', result['provider'], result['url'], output_path)
                        break

            prompt = ((segment.get('generation_prompts') or {}).get(asset_type) or '').strip()
            if not prompt:
                continue
            ext = '.png' if asset_type == 'image' else '.mp4'
            output_path = output_dir / f'{start_slug}_{brief_slug}{ext}'
            if asset_type == 'image':
                run_generator(image_script, jimeng_config, prompt, output_path)
            else:
                run_generator(video_script, jimeng_config, prompt, output_path)
            selected = (asset_type, 'generate', 'jimeng', '', output_path)
            break

        if selected is None:
            record['status'] = 'failed'
            manifest['segments'].append(record)
            continue

        asset_type, source_type, provider, source_url, output_path = selected
        record['status'] = 'ready'
        record['asset_type'] = asset_type
        record['source_type'] = source_type
        record['provider'] = provider
        record['source_url'] = source_url
        record['asset_path'] = str(output_path)
        manifest['segments'].append(record)

    manifest_path = Path(args.manifest_file)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(manifest_path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
