#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from videocreator.project_migration import migrate_capital_project


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate the completed capital project to project schema v2")
    parser.add_argument("project")
    parser.add_argument("--template", default="chaos-museum")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = migrate_capital_project(Path(args.project), args.template, dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
