#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from volc_visual_client import download_binary, load_config, poll_task, submit_task


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Generate a video with Jimeng and save it locally')
    parser.add_argument('--config', required=True)
    parser.add_argument('--prompt', required=True)
    parser.add_argument('--output', required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    video_cfg = config.video
    body = {
        'req_key': video_cfg.get('req_key', 'jimeng_t2v_v30_1080p'),
        'prompt': args.prompt,
        'frames': video_cfg.get('frames', 121),
        'aspect_ratio': video_cfg.get('aspect_ratio', '16:9'),
        'seed': -1,
    }
    task_id = submit_task(config, body)
    query_body = {
        'req_key': video_cfg.get('req_key', 'jimeng_t2v_v30_1080p'),
        'task_id': task_id,
    }
    result = poll_task(config, query_body)
    data = result.get('data') or {}
    video_url = data.get('video_url')
    if not video_url:
        raise RuntimeError(f'No video_url found: {result}')
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(download_binary(video_url))
    print(output_path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
