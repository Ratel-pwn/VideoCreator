import hashlib
import json

import pytest

from videocreator.bgm_library import BgmLibrarySelection, BgmTrack
from videocreator import project_layout
from videocreator.project_layout import create_run, initialize_project
from videocreator.templates import load_template


def test_initialize_project_and_create_canonical_run(tmp_path):
    template_root = tmp_path / "templates" / "demo"
    template_root.mkdir(parents=True)
    for name in ("prepare.md", "writing.md", "visual-planning.md"):
        (template_root / name).write_text(name, encoding="utf-8")
    for name in ("pacing.json", "subtitle.json", "composition.json"):
        (template_root / name).write_text("{}", encoding="utf-8")
    bgm_policy = {"enabled": True, "preferred_moods": ["reflective"]}
    (template_root / "bgm.json").write_text(json.dumps(bgm_policy), encoding="utf-8")
    (template_root / "template.json").write_text(json.dumps({
        "id": "demo", "version": 1,
        "capabilities": ["prepare", "writing", "visual_planning", "final_assembly"],
        "paths": {"prepare": "prepare.md", "writing": "writing.md", "visual_planning": "visual-planning.md", "pacing": "pacing.json", "subtitle": "subtitle.json", "composition": "composition.json", "bgm": "bgm.json"},
    }), encoding="utf-8")
    template = load_template(tmp_path / "templates", "demo")
    project = initialize_project(tmp_path / "projects", "video", template)
    run = create_run(project, "run-001", template, {})

    assert json.loads((project / "project.json").read_text(encoding="utf-8"))["template_id"] == "demo"
    assert {p.name for p in run.root.iterdir() if p.is_dir()} == {
        "inputs", "session", "writing", "audio", "subtitles", "visual", "render", "review"
    }
    assert run.visual_plan == run.root / "visual" / "visual-plan.json"
    assert (run.inputs / "template.snapshot.json").is_file()
    template_snapshot = json.loads(
        (run.inputs / "template.snapshot.json").read_text(encoding="utf-8")
    )
    assert template_snapshot["bgm_policy"]["content"] == bgm_policy
    assert template_snapshot["bgm_policy"]["source_path"] == "bgm.json"
    assert template_snapshot["bgm_policy"]["source_sha256"] == hashlib.sha256(
        (template_root / "bgm.json").read_bytes()
    ).hexdigest()
    assert len(template_snapshot["bgm_policy"]["content_sha256"]) == 64
    assert (project / "library" / "bgm").is_dir()


def test_run_snapshots_bgm_audio_sidecar_hashes_and_provenance(tmp_path):
    template_root = tmp_path / "templates" / "demo"
    template_root.mkdir(parents=True)
    for name in ("prepare.md", "writing.md", "visual-planning.md"):
        (template_root / name).write_text(name, encoding="utf-8")
    for name in ("pacing.json", "subtitle.json", "composition.json"):
        (template_root / name).write_text("{}", encoding="utf-8")
    bgm_policy = {"enabled": True, "preferred_moods": ["reflective"]}
    (template_root / "bgm.json").write_text(json.dumps(bgm_policy), encoding="utf-8")
    (template_root / "template.json").write_text(json.dumps({
        "id": "demo", "version": 1, "capabilities": ["prepare", "writing", "visual_planning", "bgm"],
        "paths": {"prepare": "prepare.md", "writing": "writing.md", "visual_planning": "visual-planning.md", "pacing": "pacing.json", "subtitle": "subtitle.json", "composition": "composition.json", "bgm": "bgm.json"},
    }), encoding="utf-8")
    template = load_template(tmp_path / "templates", "demo")
    project = initialize_project(tmp_path / "projects", "video", template)
    audio = project / "library" / "bgm" / "calm.mp3"
    metadata = audio.with_suffix(".bgm.json")
    audio.write_bytes(b"audio")
    metadata.write_text('{"source": "fixture"}', encoding="utf-8")
    track = BgmTrack(
        "calm", audio, metadata, "project", "expected-audio-hash", "Calm",
        "Example Composer", "https://example.com/calm", "CC BY 4.0", "verified",
        (), (), "low", None, True, (), (), 0, True,
        hashlib.sha256(metadata.read_bytes()).hexdigest(),
        "wikimedia",
    )

    run = create_run(project, "run-bgm", template, {
        "bgm": BgmLibrarySelection("project", audio.parent, (track,), ())
    })

    snapshot = json.loads((run.inputs / "library.snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["bgm"]["level"] == "project"
    assert snapshot["bgm"]["files"] == [{
        "path": str(audio),
        "sha256": "expected-audio-hash",
        "duration_ms": 0,
        "metadata": {
            "path": str(metadata),
            "sha256": hashlib.sha256(metadata.read_bytes()).hexdigest(),
        },
        "provenance": {
            "creator": "Example Composer",
            "source_url": "https://example.com/calm",
            "provider": "wikimedia",
            "license": "CC BY 4.0",
            "rights_status": "verified",
        },
    }]


def test_initialize_project_never_overwrites(tmp_path):
    project = tmp_path / "projects" / "video"
    project.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        initialize_project(tmp_path / "projects", "video", object())


def test_run_is_not_published_when_snapshot_creation_fails(
    tmp_path,
    monkeypatch,
):
    template_root = tmp_path / "templates" / "demo"
    template_root.mkdir(parents=True)
    for name in ("prepare.md", "writing.md", "visual-planning.md"):
        (template_root / name).write_text(name, encoding="utf-8")
    for name in ("pacing.json", "subtitle.json", "composition.json"):
        (template_root / name).write_text("{}", encoding="utf-8")
    (template_root / "template.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "version": 1,
                "capabilities": ["prepare", "writing", "visual_planning"],
                "paths": {
                    "prepare": "prepare.md",
                    "writing": "writing.md",
                    "visual_planning": "visual-planning.md",
                    "pacing": "pacing.json",
                    "subtitle": "subtitle.json",
                    "composition": "composition.json",
                },
            }
        ),
        encoding="utf-8",
    )
    template = load_template(tmp_path / "templates", "demo")
    project = initialize_project(tmp_path / "projects", "video", template)
    original = project_layout._write_json

    def fail_library_snapshot(path, value):
        if path.name == "library.snapshot.json":
            raise OSError("snapshot write failed")
        original(path, value)

    monkeypatch.setattr(project_layout, "_write_json", fail_library_snapshot)

    with pytest.raises(OSError, match="snapshot write failed"):
        create_run(project, "run-crash", template, {})

    assert not (project / "runs" / "run-crash").exists()
    assert not list((project / "runs").glob(".creating-*"))

