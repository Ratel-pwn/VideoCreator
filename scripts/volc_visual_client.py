#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class VolcVisualConfig:
    access_key_id: str
    secret_access_key: str
    security_token: str
    region: str
    service: str
    host: str
    version: str
    poll_interval_seconds: int
    timeout_seconds: int
    image: dict[str, Any]
    video: dict[str, Any]


def load_config(path: str | Path) -> VolcVisualConfig:
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    ak = os.environ.get(data['access_key_id_env'], '')
    sk = os.environ.get(data['secret_access_key_env'], '')
    if not ak or not sk:
        raise RuntimeError('Missing Volcengine visual credentials in environment variables.')
    token_env = data.get('security_token_env', '')
    token = os.environ.get(token_env, '') if token_env else ''
    return VolcVisualConfig(
        access_key_id=ak,
        secret_access_key=sk,
        security_token=token,
        region=data.get('region', 'cn-north-1'),
        service=data.get('service', 'cv'),
        host=data.get('host', 'visual.volcengineapi.com'),
        version=data.get('version', '2022-08-31'),
        poll_interval_seconds=int(data.get('poll_interval_seconds', 5)),
        timeout_seconds=int(data.get('timeout_seconds', 900)),
        image=data.get('image', {}),
        video=data.get('video', {}),
    )


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def hmac_sha256(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode('utf-8'), hashlib.sha256).digest()


def canonical_query(params: dict[str, str]) -> str:
    pairs = []
    for key in sorted(params.keys()):
        pairs.append(
            urllib.parse.quote(str(key), safe='-_.~') + '=' + urllib.parse.quote(str(params[key]), safe='-_.~')
        )
    return '&'.join(pairs)


def build_headers(config: VolcVisualConfig, query: dict[str, str], body: bytes, content_type: str = 'application/json') -> dict[str, str]:
    now = datetime.now(timezone.utc)
    x_date = now.strftime('%Y%m%dT%H%M%SZ')
    short_date = now.strftime('%Y%m%d')
    payload_hash = sha256_hex(body)
    canonical_headers_map = {
        'content-type': content_type,
        'host': config.host,
        'x-content-sha256': payload_hash,
        'x-date': x_date,
    }
    if config.security_token:
        canonical_headers_map['x-security-token'] = config.security_token
    signed_headers = ';'.join(sorted(canonical_headers_map.keys()))
    canonical_headers = ''.join(f'{key}:{canonical_headers_map[key]}\n' for key in sorted(canonical_headers_map.keys()))
    canonical_request = '\n'.join([
        'POST',
        '/',
        canonical_query(query),
        canonical_headers,
        signed_headers,
        payload_hash,
    ])
    credential_scope = f'{short_date}/{config.region}/{config.service}/request'
    string_to_sign = '\n'.join([
        'HMAC-SHA256',
        x_date,
        credential_scope,
        sha256_hex(canonical_request.encode('utf-8')),
    ])
    signing_key = hmac_sha256(
        hmac_sha256(
            hmac_sha256(
                hmac_sha256(config.secret_access_key.encode('utf-8'), short_date),
                config.region,
            ),
            config.service,
        ),
        'request',
    )
    signature = hmac.new(signing_key, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    headers = {
        'Content-Type': content_type,
        'Host': config.host,
        'X-Date': x_date,
        'X-Content-Sha256': payload_hash,
        'Authorization': f'HMAC-SHA256 Credential={config.access_key_id}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}',
    }
    if config.security_token:
        headers['X-Security-Token'] = config.security_token
    return headers


def request_json(config: VolcVisualConfig, action: str, body: dict[str, Any]) -> dict[str, Any]:
    query = {'Action': action, 'Version': config.version}
    body_bytes = json.dumps(body, ensure_ascii=False).encode('utf-8')
    headers = build_headers(config, query, body_bytes)
    url = f'https://{config.host}/?{canonical_query(query)}'
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=config.timeout_seconds) as resp:
        return json.loads(resp.read().decode('utf-8'))


def submit_task(config: VolcVisualConfig, body: dict[str, Any]) -> str:
    payload = request_json(config, 'CVSync2AsyncSubmitTask', body)
    if payload.get('code') != 10000:
        raise RuntimeError(f"Submit failed: {payload.get('code')} {payload.get('message')}")
    task_id = ((payload.get('data') or {}).get('task_id'))
    if not task_id:
        raise RuntimeError(f'Missing task_id in submit response: {payload}')
    return task_id


def poll_task(config: VolcVisualConfig, body: dict[str, Any]) -> dict[str, Any]:
    deadline = time.time() + config.timeout_seconds
    while time.time() < deadline:
        payload = request_json(config, 'CVSync2AsyncGetResult', body)
        if payload.get('code') != 10000:
            raise RuntimeError(f"Query failed: {payload.get('code')} {payload.get('message')}")
        data = payload.get('data') or {}
        status = data.get('status')
        if status == 'done':
            return payload
        if status in {'expired', 'not_found'}:
            raise RuntimeError(f'Task failed with status={status}: {payload}')
        time.sleep(config.poll_interval_seconds)
    raise TimeoutError('Timed out while waiting for Jimeng task result.')


def download_binary(url: str) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': 'ChaosMuseum/1.0'})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()
