#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from videocreator.subtitle_sync import SyncThresholds, audit_subtitle_sync


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit narration and SRT synchronization")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--srt", type=Path, required=True)
    parser.add_argument("--alignment-report", type=Path, required=True)
    parser.add_argument("--segment-manifest", type=Path)
    parser.add_argument("--thresholds", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    threshold_values = {}
    if args.thresholds:
        threshold_values = json.loads(
            args.thresholds.read_text(encoding="utf-8-sig")
        )
    result = audit_subtitle_sync(
        args.audio,
        args.srt,
        args.alignment_report,
        thresholds=SyncThresholds.from_dict(threshold_values),
        segment_manifest=args.segment_manifest,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"{result['status']}: {args.output}")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
