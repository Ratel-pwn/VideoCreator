from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Starter CLI for TTS alignment.")
    parser.add_argument("--input", required=True, help="Path to TTS align request JSON.")
    parser.add_argument("--output", required=True, help="Path to TTS align result JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    required = ["video_id", "final_audio_path", "approved_text", "alignment_engine"]
    missing = [key for key in required if key not in data]
    if missing:
        raise SystemExit(f"Missing required fields: {', '.join(missing)}")

    template = {
        "video_id": data["video_id"],
        "artifact_type": "tts_align_result",
        "generated_at": "TODO",
        "aligned_srt_path": "TODO",
        "timing_json_path": "TODO",
        "report": {
            "status": "stub",
            "coverage_percent": 0,
            "low_confidence_spans": [],
            "notes": [
                "Implement alignment engine adapter and canonical timing export."
            ]
        }
    }
    Path(args.output).write_text(json.dumps(template, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
