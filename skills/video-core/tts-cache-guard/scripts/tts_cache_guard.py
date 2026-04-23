from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Starter CLI for TTS cache guard.")
    parser.add_argument("--input", required=True, help="Path to TTS cache request JSON.")
    parser.add_argument("--output", required=True, help="Path to TTS cache decision JSON.")
    return parser.parse_args()


def fingerprint(script_text: str, voice_profile: dict) -> str:
    payload = json.dumps(
        {"script_text": script_text, "voice_profile": voice_profile},
        sort_keys=True,
        ensure_ascii=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    args = parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    required = ["video_id", "script_text", "voice_profile", "output_audio_path"]
    missing = [key for key in required if key not in data]
    if missing:
        raise SystemExit(f"Missing required fields: {', '.join(missing)}")

    fp = fingerprint(data["script_text"], data["voice_profile"])
    template = {
        "video_id": data["video_id"],
        "artifact_type": "tts_cache_decision",
        "generated_at": "TODO",
        "decision": "stub",
        "fingerprint": fp,
        "reasoning": [
            "Implement existing asset provenance validation and reuse policy."
        ],
        "provenance_record": data.get("existing_asset", {})
    }
    Path(args.output).write_text(json.dumps(template, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
