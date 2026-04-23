from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Starter CLI for subtitle segmentation.")
    parser.add_argument("--input", required=True, help="Path to aligned segments JSON.")
    parser.add_argument("--output", required=True, help="Path to segmented captions JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    data = json.loads(input_path.read_text(encoding="utf-8"))
    required = ["video_id", "aspect_ratio", "style", "aligned_segments"]
    missing = [key for key in required if key not in data]
    if missing:
        raise SystemExit(f"Missing required fields: {', '.join(missing)}")

    template = {
        "video_id": data["video_id"],
        "artifact_type": "segmented_captions",
        "generated_at": "TODO",
        "segments": [],
        "report": {
            "status": "stub",
            "rule_hits": [],
            "wrap_risks_remaining": [],
            "todo": "Implement semantic split and wrap-risk detection."
        }
    }
    output_path.write_text(json.dumps(template, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
