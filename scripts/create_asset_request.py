#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from videocreator.asset_manifest import create_asset_requests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create AI web asset requests")
    parser.add_argument("--visual-plan", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    visual_plan = json.loads(Path(args.visual_plan).read_text(encoding="utf-8-sig"))
    output = create_asset_requests(visual_plan)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
