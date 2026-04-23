from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Starter CLI for project packaging.")
    parser.add_argument("--input", required=True, help="Path to package request JSON.")
    parser.add_argument("--output", required=True, help="Path to package report JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    required = ["video_id", "project_root", "deliverables", "source_assets", "intermediate_assets", "cleanup_approved"]
    missing = [key for key in required if key not in data]
    if missing:
        raise SystemExit(f"Missing required fields: {', '.join(missing)}")

    template = {
        "video_id": data["video_id"],
        "artifact_type": "package_report",
        "generated_at": "TODO",
        "package_path": f"{data['project_root']}/{data['video_id']}",
        "manifest_path": f"{data['project_root']}/{data['video_id']}/manifest.json",
        "cleanup_report": {
            "status": "stub",
            "removed": [],
            "preserved": data["source_assets"],
            "todo": "Implement copy/move plan and guarded cleanup."
        }
    }
    Path(args.output).write_text(json.dumps(template, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
