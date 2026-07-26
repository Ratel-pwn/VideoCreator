from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .durable_io import (
    atomic_copy_file,
    atomic_write_json,
    atomic_write_text,
    sha256_file,
)
from .run_identity import resolve_run_dir

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


def _narration_text(markdown: str) -> str:
    text = re.sub(r"^#.*$", "", markdown, flags=re.MULTILINE)
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def import_legacy_project(
    project_root: Path,
    run_id: str,
    artifacts: dict[str, Path],
) -> Path:
    root = project_root.resolve()
    runs_root = root / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    run_dir = resolve_run_dir(root, run_id)
    run_dir.mkdir(exist_ok=False)
    for name in (
        "inputs",
        "session",
        "writing",
        "audio",
        "subtitles",
        "visual",
        "render",
        "review",
    ):
        (run_dir / name).mkdir()

    destinations = {
        "draft_approved": run_dir / "writing" / "script.approved.md",
        "voice_audio": (
            run_dir
            / "audio"
            / f"narration.imported{artifacts['voice_audio'].suffix.lower()}"
        ),
        "voice_subtitle": run_dir / "subtitles" / "subtitles.imported.srt",
        "visual_plan": run_dir / "visual" / "visual-plan.json",
    }
    lineage: dict[str, dict[str, str]] = {}
    for key, destination in destinations.items():
        source = artifacts[key].resolve()
        source_hash = sha256_file(source)
        atomic_copy_file(
            source,
            destination,
            expected_sha256=source_hash,
        )
        lineage[key] = {
            "source_path": str(source),
            "source_sha256": source_hash,
            "snapshot_path": str(destination),
            "snapshot_sha256": sha256_file(destination),
        }

    narration_path = run_dir / "audio" / "narration.txt"
    narration = _narration_text(
        destinations["draft_approved"].read_text(encoding="utf-8-sig")
    )
    if not narration:
        raise ValueError("approved draft does not contain narration text")
    atomic_write_text(narration_path, narration + "\n")

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    _write_json(
        run_dir / "state.json",
        {
            "run_id": run_id,
            "project_name": root.name,
            "mode": "legacy-import",
            "current_stage": "subtitle_sync",
            "resume_after_subtitle_sync": "visual_assets",
            "status": "ready",
            "created_at": now,
            "updated_at": now,
            "migrations": {
                "legacy_run_local_inputs": {
                    "from": "legacy-project-layout",
                    "to": "run-local-layout",
                    "migrated_at": now,
                }
            },
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
            "artifacts": {
                **{
                    key: str(destination)
                    for key, destination in destinations.items()
                },
                "narration_text": str(narration_path),
                "subtitle_alignment_timing": str(
                    run_dir / "subtitles" / "alignment-timing.json"
                ),
                "subtitle_alignment_report": str(
                    run_dir / "subtitles" / "alignment-report.json"
                ),
            },
            "lineage": {"legacy_import": lineage},
        },
    )
    return run_dir
