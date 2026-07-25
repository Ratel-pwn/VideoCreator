import json
from pathlib import Path

import pytest

from videocreator.templates import TemplateError, discover_templates, load_template, resolve_library
from videocreator.bgm_policy import load_bgm_policy


def test_repository_exposes_declarative_templates():
    root = Path(__file__).parents[1]
    templates = discover_templates(root / "templates")
    assert set(templates) == {
        "chaos-museum",
        "product-intro",
        "science-explainer",
        "ai-daily",
        "infinite-game-manifesto",
    }
    assert all(not list(item.root.rglob("*.py")) for item in templates.values())
    assert all(not list(item.root.rglob("*.ts")) for item in templates.values())
    assert all("bgm" in item.capabilities and "bgm" in item.paths for item in templates.values())
    assert all(load_bgm_policy(item).enabled for item in templates.values())


def test_template_rejects_path_traversal(tmp_path):
    template = tmp_path / "bad"
    template.mkdir()
    for name in ("prepare.md", "visual-planning.md", "pacing.json", "subtitle.json", "composition.json"):
        (template / name).write_text("{}", encoding="utf-8")
    (template / "template.json").write_text(json.dumps({
        "id": "bad", "version": 1, "capabilities": ["writing"],
        "paths": {
            "prepare": "prepare.md", "writing": "../writing.md",
            "visual_planning": "visual-planning.md", "pacing": "pacing.json",
            "subtitle": "subtitle.json", "composition": "composition.json",
        },
    }), encoding="utf-8")
    with pytest.raises(TemplateError, match="inside template"):
        load_template(tmp_path, "bad")


def test_library_uses_complete_override_and_skips_empty_dirs(tmp_path):
    repo = tmp_path
    template_root = repo / "templates" / "demo"
    project = repo / "projects" / "demo"
    global_style = repo / "library" / "style" / "default"
    template_style = template_root / "library" / "style"
    project_style = project / "library" / "style"
    for folder in (global_style, template_style, project_style):
        folder.mkdir(parents=True)
    (global_style / "global.md").write_text("global", encoding="utf-8")
    (template_style / "template.md").write_text("template", encoding="utf-8")
    definition = type("Template", (), {"root": template_root})()

    selected = resolve_library(repo, project, definition, "style")
    assert selected.level == "template"
    assert [item.path.name for item in selected.files] == ["template.md"]

    (project_style / "project.md").write_text("project", encoding="utf-8")
    selected = resolve_library(repo, project, definition, "style")
    assert selected.level == "project"
    assert [item.path.name for item in selected.files] == ["project.md"]


def test_library_readme_does_not_count_as_an_override(tmp_path):
    global_style = tmp_path / "library/style/default"
    template_style = tmp_path / "templates/demo/library/style"
    project = tmp_path / "projects/demo"
    global_style.mkdir(parents=True)
    template_style.mkdir(parents=True)
    (global_style / "reference.md").write_text("reference", encoding="utf-8")
    (template_style / "README.md").write_text("instructions", encoding="utf-8")
    definition = type("Template", (), {"root": tmp_path / "templates/demo"})()

    selected = resolve_library(tmp_path, project, definition, "style")

    assert selected.level == "global"
