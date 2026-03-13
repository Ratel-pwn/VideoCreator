#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "https://openspeech.bytedance.com/api/v3/tts/voice_clone"
LANGUAGE_MAP = {
    "cn": 0,
    "zh-cn": 0,
    "en": 1,
    "ja": 2,
    "es": 3,
    "id": 4,
    "pt": 5,
    "de": 6,
    "fr": 7,
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
    parser = argparse.ArgumentParser(description="Clone a voice with Volcengine voice_clone V3 API")
    parser.add_argument("--config", default=default_config, type=Path)
    parser.add_argument("--audio", required=True, type=Path, help="Path to source audio file.")
    parser.add_argument("--speaker-id", default=None, help="Target speaker_id. Defaults to config speaker_id.")
    parser.add_argument("--language", default=None, help="Language like zh-cn, cn, en. Defaults to config explicit_language or zh-cn.")
    parser.add_argument("--model-type", action="append", type=int, dest="model_types", help="Repeatable model type. Example: --model-type 4")
    parser.add_argument("--demo-text", default=None, help="Optional demo text matching the source audio.")
    parser.add_argument("--json", action="store_true", help="Print raw JSON response only.")
    return parser.parse_args()


def normalize_language(value: str | None) -> int:
    key = (value or "zh-cn").strip().lower()
    return LANGUAGE_MAP.get(key, 0)


def infer_format(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if suffix in {"mp3", "wav", "ogg", "m4a", "aac", "pcm"}:
        return suffix
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed == "audio/mpeg":
        return "mp3"
    return "wav"


def coalesce_settings(cli: argparse.Namespace, cfg: dict[str, Any]) -> dict[str, Any]:
    appid = str(cfg.get("appid") or "").strip()
    access_token = str(cfg.get("access_token") or "").strip()
    speaker_id = str(cli.speaker_id or cfg.get("clone_speaker_id") or cfg.get("speaker_id") or "").strip()
    language = normalize_language(cli.language or cfg.get("explicit_language") or "zh-cn")
    model_types = cli.model_types if cli.model_types else [4]
    missing = [name for name, value in (("appid", appid), ("access_token", access_token), ("speaker_id", speaker_id)) if not value]
    if missing:
        raise ValueError(f"missing required config fields: {', '.join(missing)}")
    if not cli.audio.exists():
        raise ValueError(f"audio file not found: {cli.audio}")
    return {
        "appid": appid,
        "access_token": access_token,
        "speaker_id": speaker_id,
        "audio_path": cli.audio,
        "audio_format": infer_format(cli.audio),
        "language": language,
        "model_types": model_types,
        "demo_text": cli.demo_text,
    }


def build_payload(settings: dict[str, Any]) -> dict[str, Any]:
    audio_bytes = settings["audio_path"].read_bytes()
    payload: dict[str, Any] = {
        "speaker_id": settings["speaker_id"],
        "audio": {
            "data": base64.b64encode(audio_bytes).decode("ascii"),
            "format": settings["audio_format"],
        },
        "language": settings["language"],
        "model_types": settings["model_types"],
        "extra_params": {
            "voice_clone_denoise_model_id": "",
        },
    }
    if settings.get("demo_text"):
        payload["audio"]["text"] = settings["demo_text"]
    return payload


def post_clone(settings: dict[str, Any]) -> tuple[int, dict[str, str], Any]:
    body = json.dumps(build_payload(settings), ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Api-App-Key": settings["appid"],
        "X-Api-App-Id": settings["appid"],
        "X-Api-Access-Key": settings["access_token"],
        "X-Api-Request-Id": str(uuid.uuid4()),
    }
    request = Request(API_URL, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=120) as response:
            text = response.read().decode("utf-8", errors="replace")
            return response.status, dict(response.headers.items()), json.loads(text)
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            parsed: Any = json.loads(text)
        except Exception:
            parsed = {"raw": text}
        return exc.code, dict(exc.headers.items()), parsed
    except URLError as exc:
        raise RuntimeError(f"request failed: {exc}") from exc


def summarize(status_code: int, headers: dict[str, str], data: Any, speaker_id: str) -> int:
    logid = headers.get("X-Tt-Logid") or headers.get("x-tt-logid") or ""
    print(f"HTTP status: {status_code}")
    if logid:
        print(f"X-Tt-Logid: {logid}")
    print(f"speaker_id: {speaker_id}")

    if not isinstance(data, dict):
        print(data)
        return 1

    code = data.get("code")
    message = data.get("message")
    if code is not None:
        print(f"API code: {code}")
    if message:
        print(f"message: {message}")
    if data.get("status") is not None:
        print(f"status: {data.get('status')}")
    if data.get("speaker_status") is not None:
        print("speaker_status:")
        for item in data.get("speaker_status") or []:
            print(json.dumps(item, ensure_ascii=False))
    if data.get("available_training_times") is not None:
        print(f"available_training_times: {data.get('available_training_times')}")
    return 0 if status_code == 200 else 1


def main() -> int:
    cli = parse_args()
    try:
        cfg = load_config(cli.config)
        settings = coalesce_settings(cli, cfg)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        status_code, headers, data = post_clone(settings)
    except Exception as exc:
        print(f"Clone failed: {exc}", file=sys.stderr)
        return 1

    if cli.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if status_code == 200 else 1

    return summarize(status_code, headers, data, settings["speaker_id"])


if __name__ == "__main__":
    raise SystemExit(main())

