#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "https://openspeech.bytedance.com/api/v3/tts/get_voice"
STATUS_LABELS = {
    0: "NotFound",
    1: "Training",
    2: "Success",
    3: "Failed",
    4: "Active",
}
MODEL_TYPE_LABELS = {
    1: "ICL1.0",
    2: "DiT Standard",
    3: "DiT Restored",
    4: "ICL2.0",
}


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"failed to parse config file: {path} ({exc})") from exc


def parse_args() -> argparse.Namespace:
    default_config = Path(__file__).resolve().with_name("volc_tts_ws.config.json")
    parser = argparse.ArgumentParser(description="Validate a Volcengine cloned speaker_id via get_voice API")
    parser.add_argument("--config", default=default_config, type=Path)
    parser.add_argument("--speaker-id", default=None, help="Speaker ID to validate. Overrides config speaker_id.")
    parser.add_argument("--json", action="store_true", help="Print raw JSON response.")
    return parser.parse_args()


def coalesce_settings(cli: argparse.Namespace, cfg: dict[str, Any]) -> dict[str, str]:
    appid = str(cfg.get("appid") or "").strip()
    access_token = str(cfg.get("access_token") or "").strip()
    speaker_id = str(cli.speaker_id or cfg.get("clone_speaker_id") or cfg.get("speaker_id") or "").strip()
    missing = [name for name, value in (("appid", appid), ("access_token", access_token), ("speaker_id", speaker_id)) if not value]
    if missing:
        raise ValueError(f"missing required config fields: {', '.join(missing)}")
    return {
        "appid": appid,
        "access_token": access_token,
        "speaker_id": speaker_id,
    }


def post_get_voice(settings: dict[str, str]) -> tuple[int, dict[str, str], Any]:
    payload = json.dumps({"speaker_id": settings["speaker_id"]}, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Api-App-Key": settings["appid"],
        "X-Api-App-Id": settings["appid"],
        "X-Api-Access-Key": settings["access_token"],
        "X-Api-Request-Id": str(uuid.uuid4()),
    }
    request = Request(API_URL, data=payload, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, dict(response.headers.items()), json.loads(body)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        parsed: Any
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"raw": body}
        return exc.code, dict(exc.headers.items()), parsed
    except URLError as exc:
        raise RuntimeError(f"request failed: {exc}") from exc


def print_summary(status_code: int, headers: dict[str, str], data: Any, speaker_id: str) -> int:
    logid = headers.get("X-Tt-Logid") or headers.get("x-tt-logid") or ""
    print(f"HTTP status: {status_code}")
    if logid:
        print(f"X-Tt-Logid: {logid}")
    print(f"speaker_id: {speaker_id}")

    if not isinstance(data, dict):
        print("Unexpected response:")
        print(data)
        return 1

    if data.get("code") not in (None, 0):
        print(f"API code: {data.get('code')}")
    if data.get("message"):
        print(f"message: {data.get('message')}")

    status = data.get("status")
    status_label = STATUS_LABELS.get(status, "Unknown")
    print(f"voice status: {status} ({status_label})")
    print(f"language: {data.get('language')}")
    print(f"available_training_times: {data.get('available_training_times')}")

    speaker_status = data.get("speaker_status")
    has_icl2 = False
    if isinstance(speaker_status, list) and speaker_status:
        print("speaker_status:")
        for item in speaker_status:
            if not isinstance(item, dict):
                continue
            model_type = item.get("model_type")
            model_label = MODEL_TYPE_LABELS.get(model_type, "Unknown")
            demo_audio = item.get("demo_audio")
            print(f"  - model_type={model_type} ({model_label})")
            if demo_audio:
                print(f"    demo_audio={demo_audio}")
            if model_type == 4:
                has_icl2 = True
    else:
        print("speaker_status: <empty>")

    can_use_tts = status in (2, 4)
    print(f"tts_usable: {'yes' if can_use_tts else 'no'}")
    print(f"icl2_usable: {'yes' if has_icl2 else 'no'}")

    if can_use_tts and has_icl2:
        print("next_step: this speaker_id looks usable for WebSocket V3 with resource_id=seed-icl-2.0")
        return 0
    if can_use_tts:
        print("next_step: speaker exists, but current response does not show model_type=4. Do not assume ICL2.0 is available.")
        return 2
    print("next_step: this speaker_id is not ready for TTS yet. Query/upgrade/retrain before testing WebSocket V3.")
    return 3


def main() -> int:
    cli = parse_args()
    try:
        cfg = load_config(cli.config)
        settings = coalesce_settings(cli, cfg)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        status_code, headers, data = post_get_voice(settings)
    except Exception as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 1

    if cli.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if status_code == 200 else 1

    return print_summary(status_code, headers, data, settings["speaker_id"])


if __name__ == "__main__":
    raise SystemExit(main())

