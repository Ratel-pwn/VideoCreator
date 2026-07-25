from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .durable_io import atomic_write_json

ARTIFACT_PATTERNS = {
    "draft_approved": ("drafts", "*.md"),
    "voice_audio": ("audio", "*.mp3"),
    "voice_subtitle": ("audio", "*.srt"),
    "visual_plan": ("drafts", "visual-plan.json"),
}


def _exactly_one(project_root: Path, key: str, folder: str, pattern: str) -> Path:
    matches = sorted((project_root / folder).glob(pattern))
    if not matches:
        raise ValueError(f"missing {key}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous {key}: {len(matches)} candidates")
    return matches[0].resolve()


def discover_legacy_artifacts(project_root: Path) -> dict[str, Path]:
    root = project_root.resolve()
    return {
        key: _exactly_one(root, key, folder, pattern)
        for key, (folder, pattern) in ARTIFACT_PATTERNS.items()
    }


def _write_json(path: Path, value: dict) -> None:
    atomic_write_json(path, value)


def import_legacy_project(
    project_root: Path,
    run_id: str,
    artifacts: dict[str, Path],
) -> Path:
    root = project_root.resolve()
    run_dir = root / "runs" / run_id
    if run_dir.exists():
        raise FileExistsError(f"Run already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    _write_json(
        run_dir / "state.json",
        {
            "run_id": run_id,
            "project_name": root.name,
            "mode": "legacy-import",
            "current_stage": "visual_assets",
            "status": "ready",
            "created_at": now,
            "updated_at": now,
        },
    )
    _write_json(
        run_dir / "manifest.json",
        {
            "run_id": run_id,
            "project_name": root.name,
            "mode": "legacy-import",
            "topic": root.name,
            "created_at": now,
            "artifacts": {key: str(path.resolve()) for key, path in artifacts.items()},
        },
    )
    return run_dir
