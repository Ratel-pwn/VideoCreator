from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from videocreator.project_import import discover_legacy_artifacts, import_legacy_project


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Register an existing VideoCreator project as a resumable run."
    )
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    artifacts = discover_legacy_artifacts(args.project_root)
    run_dir = import_legacy_project(args.project_root, args.run_id, artifacts)
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
