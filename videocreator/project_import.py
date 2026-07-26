from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .bgm_library import resolve_bgm_library
from .durable_io import (
    atomic_copy_file,
    atomic_write_json,
    atomic_write_text,
    sha256_file,
)
from .project_layout import RunPaths, create_run
from .run_identity import resolve_run_dir
from .templates import load_template, resolve_library

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
    *,
    repo_root: Path,
    templates_root: Path | None = None,
) -> Path:
    root = project_root.resolve()
    home = Path(repo_root).resolve()
    project_path = root / "project.json"
    if not project_path.is_file():
        raise ValueError(
            f"Legacy import requires a valid project.json: {project_path}"
        )
    try:
        project = json.loads(project_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid project.json: {project_path}") from exc
    if not isinstance(project, dict):
        raise ValueError(f"Invalid project.json: {project_path}")
    project_name = project.get("name")
    if not isinstance(project_name, str) or not project_name.strip():
        raise ValueError("project.json must declare name")
    template_id = project.get("template_id")
    if not isinstance(template_id, str) or not template_id:
        raise ValueError("project.json must declare template_id")
    template = load_template(
        Path(templates_root).resolve()
        if templates_root is not None
        else home / "templates",
        template_id,
    )
    libraries = {
        resource_type: resolve_library(
            home,
            root,
            template,
            resource_type,
        )
        for resource_type in ("style", "voice")
    }
    libraries["bgm"] = resolve_bgm_library(home, root, template)
    run_dir = resolve_run_dir(root, run_id)
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
    source_snapshots = {
        key: (
            run_dir
            / "inputs"
            / "legacy-sources"
            / f"{key}{source.suffix.lower()}"
        )
        for key, source in artifacts.items()
    }
    lineage: dict[str, dict[str, str]] = {}
    for key, destination in destinations.items():
        source = artifacts[key].resolve()
        source_hash = sha256_file(source)
        lineage[key] = {
            "source_path": str(source),
            "source_sha256": source_hash,
            "snapshot_path": str(destination),
            "snapshot_sha256": source_hash,
            "input_snapshot_path": str(source_snapshots[key]),
        }

    narration_path = run_dir / "audio" / "narration.txt"
    narration = _narration_text(
        artifacts["draft_approved"].read_text(encoding="utf-8-sig")
    )
    if not narration:
        raise ValueError("approved draft does not contain narration text")

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    state = {
            "run_id": run_id,
            "project_name": project_name,
            "mode": "legacy-import",
            "current_stage": "subtitle_sync",
            "resume_after_subtitle_sync": "visual_plan",
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
        }
    manifest = {
            "run_id": run_id,
            "project_name": project_name,
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
        }

    def populate(paths: RunPaths) -> None:
        source_records = []
        for key, final_destination in destinations.items():
            source = artifacts[key].resolve()
            source_hash = lineage[key]["source_sha256"]
            relative_destination = final_destination.relative_to(run_dir)
            staging_destination = paths.root / relative_destination
            atomic_copy_file(
                source,
                staging_destination,
                expected_sha256=source_hash,
            )
            input_destination = (
                paths.root
                / source_snapshots[key].relative_to(run_dir)
            )
            input_destination.parent.mkdir(parents=True, exist_ok=True)
            atomic_copy_file(
                source,
                input_destination,
                expected_sha256=source_hash,
            )
            source_records.append(
                {
                    "artifact": key,
                    "source_path": str(source),
                    "source_sha256": source_hash,
                    "snapshot_path": str(source_snapshots[key]),
                    "snapshot_sha256": sha256_file(input_destination),
                }
            )
        atomic_write_text(
            paths.audio / "narration.txt",
            narration + "\n",
        )
        _write_json(
            paths.inputs / "source-selection.json",
            {
                "schema_version": 1,
                "files": source_records,
            },
        )

    paths = create_run(
        root,
        run_id,
        template,
        libraries,
        initial_state=state,
        initial_manifest=manifest,
        populate_staging=populate,
    )
    return paths.root
