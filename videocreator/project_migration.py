from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from .durable_io import atomic_write_json


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value)


def _rewrite_paths(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _rewrite_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_paths(item) for item in value]
    if isinstance(value, str):
        normalized = value.replace("\\", "/")
        normalized = normalized.replace("assets/video/", "media/videos/")
        normalized = normalized.replace("assets/", "media/images/")
        return normalized
    return value


def migrate_capital_project(project_root: Path, template_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    project_root = project_root.resolve()
    if not (project_root / "project.json").is_file():
        raise FileNotFoundError(f"not a project: {project_root}")
    runs = sorted(path for path in (project_root / "runs").iterdir() if path.is_dir())
    if len(runs) != 2:
        raise ValueError(f"capital migration requires exactly two historical runs, found {len(runs)}")
    plans = sorted((project_root / "drafts").glob("visual-plan*.json"), key=lambda p: ("v2" in p.name, p.name))
    scripts = [p for p in (project_root / "drafts").glob("*.md")]
    audio = [p for p in (project_root / "audio").glob("*.mp3") if "cleaned" not in p.name]
    subtitles = [p for p in (project_root / "audio").glob("*.srt") if "cleaned" not in p.name]
    cleaned_audio = project_root / "audio" / "voice.cleaned.mp3"
    cleaned_srt = project_root / "audio" / "voice.cleaned.srt"
    required = [*plans, *scripts, *audio, *subtitles, cleaned_audio, cleaned_srt]
    if len(plans) != 2 or len(scripts) != 1 or len(audio) != 1 or len(subtitles) != 1 or any(not p.is_file() for p in required):
        raise ValueError("legacy project does not match the expected two-run artifact set")

    mappings: list[tuple[Path, Path, bool]] = []
    for image in sorted(p for p in (project_root / "assets").glob("*") if p.is_file()):
        mappings.append((image, project_root / "media" / "images" / image.name, True))
    for video in sorted(p for p in (project_root / "assets" / "video").glob("*") if p.is_file()):
        mappings.append((video, project_root / "media" / "videos" / video.name, True))
    session_files = sorted((project_root / "sessions").glob("*"))
    for index, run in enumerate(runs):
        mappings.extend([
            (scripts[0], run / "writing" / "script.approved.md", True),
            (audio[0], run / "audio" / "narration.generated.mp3", True),
            (cleaned_audio, run / "audio" / "narration.render.mp3", True),
            (subtitles[0], run / "subtitles" / "subtitles.aligned.srt", True),
            (cleaned_srt, run / "subtitles" / "subtitles.render.srt", True),
            (plans[index], run / "visual" / "visual-plan.json", True),
        ])
        for name in ("asset-manifest.json", "asset-audit.json"):
            if (run / name).is_file():
                mappings.append((run / name, run / "visual" / name, True))
        for name in ("render-input.json", "render-report.json", "render.log", "final.mp4"):
            if (run / name).is_file():
                mappings.append((run / name, run / "render" / name, True))
        if session_files:
            mappings.append((session_files[0], run / "session" / "conversation.md", True))
        asset_request = project_root / "drafts" / "asset-request.json"
        if asset_request.is_file():
            mappings.append((asset_request, run / "visual" / "asset-request.json", True))

    report = {"ok": True, "project": str(project_root), "template_id": template_id, "dry_run": dry_run, "files": []}
    if dry_run:
        report["files"] = [{"source": str(src), "destination": str(dst), "move": move} for src, dst, move in mappings]
        return report

    for source, destination, move in mappings:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        source_hash = _hash(source)
        destination_hash = _hash(destination)
        if source_hash != destination_hash:
            raise RuntimeError(f"migration hash mismatch: {source} -> {destination}")
        if destination.suffix == ".json":
            try:
                rewritten = _rewrite_paths(json.loads(destination.read_text(encoding="utf-8-sig")))
                _write_json(destination, rewritten)
                destination_hash = _hash(destination)
            except json.JSONDecodeError:
                pass
        report["files"].append({"source": str(source), "destination": str(destination), "source_sha256": source_hash, "destination_sha256": destination_hash, "move": move})

    project = json.loads((project_root / "project.json").read_text(encoding="utf-8-sig"))
    presentation = project.pop("presentation", {})
    project.pop("style_library_dir", None)
    project.pop("voice_source_file", None)
    project.update({"schema_version": 2, "name": project_root.name, "template_id": template_id})
    if presentation.get("video_title"):
        project["title"] = presentation["video_title"]
    if presentation.get("publication_date"):
        project["publication_date"] = presentation["publication_date"]
    _write_json(project_root / "project.json", project)
    for run in runs:
        for name in ("inputs", "session", "writing", "audio", "subtitles", "visual", "render", "review"):
            (run / name).mkdir(parents=True, exist_ok=True)
        _write_json(run / "inputs" / "template.snapshot.json", {"id": template_id, "version": 1, "migrated": True})
        _write_json(run / "inputs" / "project.snapshot.json", project)
        _write_json(run / "inputs" / "source-selection.json", {"files": []})
        _write_json(run / "inputs" / "library.snapshot.json", {"style": {"level": "project"}, "voice": {"level": "project"}})
        old_manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8-sig"))
        old_manifest.update({"schema_version": 2, "project": project_root.name, "template": {"id": template_id, "version": 1}})
        old_manifest["artifacts"] = {
            "draft_approved": "writing/script.approved.md", "voice_audio": "audio/narration.generated.mp3",
            "voice_audio_cleaned": "audio/narration.render.mp3", "voice_subtitle": "subtitles/subtitles.aligned.srt",
            "voice_subtitle_cleaned": "subtitles/subtitles.render.srt", "visual_plan": "visual/visual-plan.json",
            "asset_manifest": "visual/asset-manifest.json", "render_input": "render/render-input.json",
            "render_report": "render/render-report.json", "final_video": "render/final.mp4",
        }
        _write_json(run / "manifest.json", old_manifest)

    for source, _, move in mappings:
        if move and source.exists():
            source.unlink()
    for folder in (project_root / "assets" / "video", project_root / "assets", project_root / "drafts", project_root / "audio", project_root / "sessions"):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    _write_json(project_root / "migration-report.json", report)
    return report
