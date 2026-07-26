from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .bgm_library import BgmLibrarySelection
from .durable_io import atomic_write_json
from .templates import LibrarySelection, TemplateDefinition, snapshot_template


def _write_json(path: Path, data: Any) -> None:
    atomic_write_json(path, data)


@dataclass(frozen=True)
class RunPaths:
    root: Path
    inputs: Path
    session: Path
    writing: Path
    audio: Path
    subtitles: Path
    visual: Path
    render: Path
    review: Path

    @property
    def visual_plan(self) -> Path:
        return self.visual / "visual-plan.json"

    @property
    def state(self) -> Path:
        return self.root / "state.json"

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"


def initialize_project(projects_root: Path, name: str, template: TemplateDefinition, **metadata: Any) -> Path:
    project = projects_root / name
    if project.exists():
        raise FileExistsError(f"project already exists: {project}")
    for relative in ("sources", "library/style", "library/voice", "library/bgm", "media/images", "media/videos", "runs"):
        (project / relative).mkdir(parents=True, exist_ok=True)
    config = {"schema_version": 2, "name": name, "template_id": template.id, **metadata}
    _write_json(project / "project.json", config)
    return project


def _snapshot_library_item(item: LibrarySelection | BgmLibrarySelection) -> dict[str, Any]:
    if isinstance(item, BgmLibrarySelection):
        return {
            "level": item.level,
            "root": str(item.root) if item.root else None,
            "files": [
                {
                    "path": str(track.path),
                    "sha256": track.sha256,
                    "metadata": {
                        "path": str(track.metadata_path),
                        "sha256": track.metadata_sha256,
                    },
                    "provenance": {
                        "creator": track.creator,
                        "source_url": track.source_url,
                        "provider": track.provider,
                        "license": track.license,
                        "rights_status": track.rights_status,
                    },
                }
                for track in item.tracks
            ],
        }
    return {
        "level": item.level,
        "root": str(item.root) if item.root else None,
        "files": [
            {"path": str(file.path), "sha256": file.sha256}
            for file in item.files
        ],
    }


def create_run(
    project_root: Path,
    run_id: str,
    template: TemplateDefinition,
    libraries: Mapping[str, LibrarySelection | BgmLibrarySelection],
) -> RunPaths:
    root = project_root / "runs" / run_id
    if root.exists():
        raise FileExistsError(f"run already exists: {root}")
    names = ("inputs", "session", "writing", "audio", "subtitles", "visual", "render", "review")
    for name in names:
        (root / name).mkdir(parents=True, exist_ok=True)
    paths = RunPaths(root, *(root / name for name in names))
    project = json.loads((project_root / "project.json").read_text(encoding="utf-8"))
    _write_json(paths.inputs / "template.snapshot.json", snapshot_template(template))
    _write_json(paths.inputs / "project.snapshot.json", project)
    _write_json(paths.inputs / "source-selection.json", {"files": []})
    _write_json(paths.inputs / "library.snapshot.json", {
        kind: _snapshot_library_item(item) for kind, item in sorted(libraries.items())
    })
    _write_json(paths.state, {"schema_version": 2, "run_id": run_id, "stages": {}})
    _write_json(paths.manifest, {"schema_version": 2, "run_id": run_id, "project": project["name"], "template": {"id": template.id, "version": template.version}, "artifacts": {}})
    return paths

