import json
import subprocess
from pathlib import Path

from videocreator.media import MediaMetadata


def make_bgm_tree(tmp_path: Path):
    from videocreator.templates import load_template

    repo = tmp_path
    project = repo / "projects" / "demo"
    template_root = repo / "templates" / "demo"
    project.mkdir(parents=True)
    template_root.mkdir(parents=True)
    for name in ("prepare.md", "writing.md", "visual-planning.md"):
        (template_root / name).write_text(name, encoding="utf-8")
    for name in ("pacing.json", "subtitle.json", "composition.json"):
        (template_root / name).write_text("{}", encoding="utf-8")
    (template_root / "template.json").write_text(
        json.dumps({
            "id": "demo",
            "version": 1,
            "capabilities": ["prepare", "writing", "visual_planning", "bgm"],
            "paths": {
                "prepare": "prepare.md",
                "writing": "writing.md",
                "visual_planning": "visual-planning.md",
                "pacing": "pacing.json",
                "subtitle": "subtitle.json",
                "composition": "composition.json",
            },
        }),
        encoding="utf-8",
    )
    return repo, project, load_template(repo / "templates", "demo")


def write_track(root: Path, track_id: str, **overrides) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    audio = root / f"{track_id}.mp3"
    audio.write_bytes(b"test-audio")
    metadata = {
        "schema_version": 1,
        "id": track_id,
        "title": track_id.title(),
        "subjects": ["education"],
        "moods": ["calm"],
        "energy": "low-medium",
        "instrumental": True,
    }
    metadata.update(overrides)
    audio.with_suffix(".bgm.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    return audio


def test_project_valid_track_completely_overrides_template_and_global(tmp_path, monkeypatch):
    from videocreator.bgm_library import resolve_bgm_library

    repo, project, template = make_bgm_tree(tmp_path)
    write_track(project / "library/bgm", "project-track", moods=["reflective"])
    write_track(template.root / "library/bgm", "template-track", moods=["calm"])
    write_track(repo / "library/bgm/default", "global-track", moods=["neutral"])
    monkeypatch.setattr(
        "videocreator.bgm_library.probe_media",
        lambda _: MediaMetadata("audio", "mp3", None, None, 1_000),
    )

    selected = resolve_bgm_library(repo, project, template)

    assert selected.level == "project"
    assert [track.id for track in selected.tracks] == ["project-track"]


def test_invalid_project_directory_does_not_mask_valid_template(tmp_path, monkeypatch):
    from videocreator.bgm_library import resolve_bgm_library

    repo, project, template = make_bgm_tree(tmp_path)
    broken = write_track(project / "library/bgm", "broken")
    broken.write_bytes(b"not audio")
    write_track(template.root / "library/bgm", "template-track")
    probed = []

    def probe(path: Path) -> MediaMetadata:
        probed.append(path)
        if path == broken:
            raise subprocess.CalledProcessError(1, ["ffprobe", str(path)])
        return MediaMetadata("audio", "mp3", None, None, 1_000)

    monkeypatch.setattr("videocreator.bgm_library.probe_media", probe)

    selected = resolve_bgm_library(repo, project, template)

    assert selected.level == "template"
    assert selected.warnings
    assert broken in probed


def test_unknown_rights_status_keeps_track_and_appends_warning(tmp_path, monkeypatch):
    from videocreator.bgm_library import resolve_bgm_library

    repo, project, template = make_bgm_tree(tmp_path)
    write_track(
        project / "library/bgm",
        "unknown-rights",
        rights_status="unknown",
    )
    monkeypatch.setattr(
        "videocreator.bgm_library.probe_media",
        lambda _: MediaMetadata("audio", "mp3", None, None, 1_000),
    )

    selected = resolve_bgm_library(repo, project, template)

    assert [track.id for track in selected.tracks] == ["unknown-rights"]
    assert any(
        "unknown-rights" in warning and "rights status is unknown" in warning
        for warning in selected.warnings
    )


def test_duplicate_track_ids_are_rejected_and_lower_level_is_used(
    tmp_path, monkeypatch
):
    from videocreator.bgm_library import resolve_bgm_library

    repo, project, template = make_bgm_tree(tmp_path)
    write_track(project / "library/bgm", "first", id="duplicate")
    write_track(project / "library/bgm", "second", id="duplicate")
    write_track(template.root / "library/bgm", "template-track")
    monkeypatch.setattr(
        "videocreator.bgm_library.probe_media",
        lambda _: MediaMetadata("audio", "mp3", None, None, 1_000),
    )

    selected = resolve_bgm_library(repo, project, template)

    assert selected.level == "template"
    assert [track.id for track in selected.tracks] == ["template-track"]
    assert any(
        "duplicate" in warning
        and "first.mp3" in warning
        and "second.mp3" in warning
        and "ineligible" in warning
        for warning in selected.warnings
    )


def test_track_missing_required_sidecar_fields_is_ineligible(tmp_path, monkeypatch):
    from videocreator.bgm_library import resolve_bgm_library

    repo, project, template = make_bgm_tree(tmp_path)
    write_track(project / "library/bgm", "missing-title", title=None)
    monkeypatch.setattr(
        "videocreator.bgm_library.probe_media",
        lambda _: MediaMetadata("audio", "mp3", None, None, 1_000),
    )

    selected = resolve_bgm_library(repo, project, template)

    assert selected.level == "none"
    assert any("title" in warning for warning in selected.warnings)
