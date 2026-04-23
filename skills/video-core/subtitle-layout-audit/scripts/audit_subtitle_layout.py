from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Starter CLI for subtitle layout audit.")
    parser.add_argument("--input", required=True, help="Path to audit request JSON.")
    parser.add_argument("--output", required=True, help="Path to audit report JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    required = ["video_id", "aspect_ratio", "subtitle_box", "safe_area", "preview_frames"]
    missing = [key for key in required if key not in data]
    if missing:
        raise SystemExit(f"Missing required fields: {', '.join(missing)}")

    template = {
        "video_id": data["video_id"],
        "artifact_type": "subtitle_layout_audit_report",
        "generated_at": "TODO",
        "status": "stub",
        "findings": [],
        "required_actions": [
            "Implement ratio-aware safe-zone and font-size checks."
        ],
        "checked_inputs": data["preview_frames"]
    }
    Path(args.output).write_text(json.dumps(template, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
