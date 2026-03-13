#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from volc_visual_client import download_binary, load_config, poll_task, submit_task


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Generate an image with Jimeng and save it locally')
    parser.add_argument('--config', required=True)
    parser.add_argument('--prompt', required=True)
    parser.add_argument('--output', required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    image_cfg = config.image
    body = {
        'req_key': image_cfg.get('req_key', 'jimeng_t2i_v40'),
        'prompt': args.prompt,
        'width': image_cfg.get('width'),
        'height': image_cfg.get('height'),
        'force_single': image_cfg.get('force_single', True),
    }
    body = {key: value for key, value in body.items() if value is not None}
    task_id = submit_task(config, body)
    query_body = {
        'req_key': image_cfg.get('req_key', 'jimeng_t2i_v40'),
        'task_id': task_id,
        'req_json': json.dumps({'return_url': image_cfg.get('return_url', True)}, ensure_ascii=False),
    }
    result = poll_task(config, query_body)
    data = result.get('data') or {}
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if data.get('image_urls'):
        blob = download_binary(data['image_urls'][0])
    elif data.get('binary_data_base64'):
        blob = base64.b64decode(data['binary_data_base64'][0])
    else:
        raise RuntimeError(f'No image payload found: {result}')
    output_path.write_bytes(blob)
    print(output_path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
