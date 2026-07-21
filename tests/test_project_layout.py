import json

import pytest

from videocreator.project_layout import create_run, initialize_project
from videocreator.templates import load_template


def test_initialize_project_and_create_canonical_run(tmp_path):
    template_root = tmp_path / "templates" / "demo"
    template_root.mkdir(parents=True)
    for name in ("prepare.md", "writing.md", "visual-planning.md"):
        (template_root / name).write_text(name, encoding="utf-8")
    for name in ("pacing.json", "subtitle.json", "composition.json"):
        (template_root / name).write_text("{}", encoding="utf-8")
    (template_root / "template.json").write_text(json.dumps({
        "id": "demo", "version": 1,
        "capabilities": ["prepare", "writing", "visual_planning", "final_assembly"],
        "paths": {"prepare": "prepare.md", "writing": "writing.md", "visual_planning": "visual-planning.md", "pacing": "pacing.json", "subtitle": "subtitle.json", "composition": "composition.json"},
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


def test_initialize_project_never_overwrites(tmp_path):
    project = tmp_path / "projects" / "video"
    project.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        initialize_project(tmp_path / "projects", "video", object())

