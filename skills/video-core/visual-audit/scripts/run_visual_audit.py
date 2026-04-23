from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Starter CLI for visual audit.")
    parser.add_argument("--input", required=True, help="Path to visual audit request JSON.")
    parser.add_argument("--output", required=True, help="Path to visual audit report JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    required = ["video_id", "scenes", "frames", "source_evidence", "narration_map"]
    missing = [key for key in required if key not in data]
    if missing:
        raise SystemExit(f"Missing required fields: {', '.join(missing)}")

    template = {
        "video_id": data["video_id"],
        "artifact_type": "visual_audit_report",
        "generated_at": "TODO",
        "status": "stub",
        "scene_findings": [],
        "required_actions": [
            "Implement scene-by-scene cleanliness and evidence matching checks."
        ],
        "gate_summary": {
            "pre_render": "stub",
            "post_render": "stub"
        }
    }
    Path(args.output).write_text(json.dumps(template, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
