import io
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from videocreator.cli import CliError, list_runs, resolve_home, run_cli, select_run


REPO = Path(__file__).parents[1]


def config_for_projects(tmp_path: Path) -> Path:
    config = json.loads((REPO / "workflow.config.json").read_text(encoding="utf-8-sig"))
    config["projects"]["root"] = str(tmp_path / "projects")
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def write_project_run(project: Path, run_id: str, status: str, stage: str, updated_at: str) -> Path:
    run = project / "runs" / run_id
    run.mkdir(parents=True)
    (run / "state.json").write_text(json.dumps({
        "run_id": run_id, "status": status, "current_stage": stage, "updated_at": updated_at,
    }), encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps({"artifacts": {}}), encoding="utf-8")
    return run


def test_console_script_is_registered():
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"]["vc"] == "videocreator.cli:main"
    assert data["tool"]["setuptools"]["packages"] == ["videocreator"]
    assert data["tool"]["setuptools"]["py-modules"] == ["main"]


def test_home_resolution_prefers_flag_then_environment(tmp_path):
    explicit = tmp_path / "explicit"
    environment = tmp_path / "environment"
    package = tmp_path / "package"
    for root in (explicit, environment, package):
        (root / "templates").mkdir(parents=True)
        (root / "projects").mkdir()
        (root / "workflow.config.json").write_text("{}", encoding="utf-8")

    assert resolve_home(explicit, {"VIDEO_CREATOR_HOME": str(environment)}, package) == explicit.resolve()
    assert resolve_home(None, {"VIDEO_CREATOR_HOME": str(environment)}, package) == environment.resolve()
    assert resolve_home(None, {}, package) == package.resolve()


def test_home_resolution_rejects_invalid_directory(tmp_path):
    with pytest.raises(CliError, match="workflow.config.json"):
        resolve_home(tmp_path, {}, REPO)


def test_templates_support_text_and_json_output():
    text_output = io.StringIO()
    assert run_cli(["--home", str(REPO), "templates"], stdout=text_output) == 0
    assert "chaos-museum\t混乱博物馆\tv1" in text_output.getvalue()

    json_output = io.StringIO()
    assert run_cli(["--home", str(REPO), "templates", "--json"], stdout=json_output) == 0
    values = json.loads(json_output.getvalue())
    assert [item["id"] for item in values] == [
        "ai-daily",
        "chaos-museum",
        "infinite-game-manifesto",
        "product-intro",
        "science-explainer",
    ]


def test_mcp_status_dispatches_to_runtime(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(
        "videocreator.mcp_runtime.service_status",
        lambda runtime: {"status": "running", "url": "http://127.0.0.1:8765/mcp"},
    )

    assert run_cli(["--home", str(REPO), "mcp", "status", "--json"], stdout=output) == 0
    assert json.loads(output.getvalue())["status"] == "running"


def test_init_supports_short_scriptable_arguments(tmp_path):
    config = config_for_projects(tmp_path)
    output = io.StringIO()

    result = run_cli([
        "--home", str(REPO), "--config", str(config), "init", "货币的起源",
        "-t", "chaos-museum", "--title", "货币如何诞生", "--date", "2026.07.22",
        "--non-interactive",
    ], stdout=output)

    project = tmp_path / "projects" / "货币的起源"
    data = json.loads((project / "project.json").read_text(encoding="utf-8"))
    assert result == 0
    assert data == {
        "schema_version": 2, "name": "货币的起源", "template_id": "chaos-museum",
        "title": "货币如何诞生", "publication_date": "2026.07.22",
    }
    assert str(project) in output.getvalue()


def test_init_prompts_for_omitted_values(tmp_path):
    config = config_for_projects(tmp_path)
    answers = iter(["交互项目", "2", "交互标题", "2026.07.22"])

    result = run_cli(
        ["--home", str(REPO), "--config", str(config), "init"],
        input_fn=lambda _: next(answers),
    )

    data = json.loads((tmp_path / "projects/交互项目/project.json").read_text(encoding="utf-8"))
    assert result == 0
    assert data["template_id"] == "chaos-museum"
    assert data["title"] == "交互标题"


def test_non_interactive_init_requires_name_and_template(tmp_path):
    config = config_for_projects(tmp_path)
    with pytest.raises(CliError, match="NAME and --template"):
        run_cli(["--home", str(REPO), "--config", str(config), "init", "--non-interactive"])
    assert not (tmp_path / "projects").exists()


def test_chat_uses_positional_project_and_topic(monkeypatch):
    calls = {}

    class Context:
        pass

    def fake_context(repo, config, mode, topic, run_id, imported, project, template):
        calls["context"] = (repo, config, mode, topic, run_id, imported, project, template)
        return Context()

    monkeypatch.setattr("main.make_run_context", fake_context)
    monkeypatch.setattr("main.execute_from_current_stage", lambda context: calls.setdefault("executed", context))

    assert run_cli(["--home", str(REPO), "chat", "资本主义潘多拉魔盒", "新主题", "--run-id", "run-1"]) == 0
    assert calls["context"][2:] == ("chat", "新主题", "run-1", None, "资本主义潘多拉魔盒", None)
    assert isinstance(calls["executed"], Context)


def test_import_chat_validates_file_before_creating_run(tmp_path, monkeypatch):
    monkeypatch.setattr("main.make_run_context", lambda *args: pytest.fail("run must not be created"))
    with pytest.raises(CliError, match="Conversation file not found"):
        run_cli(["--home", str(REPO), "import-chat", "项目", str(tmp_path / "missing.md")])


def test_select_run_chooses_newest_unfinished_and_excludes_completed(tmp_path):
    project = tmp_path / "project"
    write_project_run(project, "older", "ready", "visual_plan", "2026-07-21T10:00:00+08:00")
    newest = write_project_run(project, "newest", "awaiting_confirmation", "draft_confirm", "2026-07-22T10:00:00+08:00")
    write_project_run(project, "completed", "completed", "done", "2026-07-23T10:00:00+08:00")

    assert select_run(project, unfinished_only=True).path == newest
    assert select_run(project).run_id == "completed"
    assert [item.run_id for item in list_runs(project)] == ["completed", "newest", "older"]


def test_select_run_supports_explicit_id_and_rejects_corrupt_state(tmp_path):
    project = tmp_path / "project"
    selected = write_project_run(project, "run-1", "ready", "tts", "2026-07-22T10:00:00+08:00")
    assert select_run(project, "run-1").path == selected
    (selected / "state.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(CliError, match="Invalid run state"):
        select_run(project, "run-1")


@pytest.mark.parametrize(
    "run_id",
    [
        "../outside",
        r"..\outside",
        r"C:\outside",
        r"\\server\share\run",
    ],
)
def test_select_run_rejects_absolute_traversal_and_unc_ids(
    tmp_path: Path,
    run_id: str,
):
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(CliError, match="safe|inside|run_id"):
        select_run(project, run_id)


def test_resume_project_dispatches_latest_unfinished_run(tmp_path, monkeypatch):
    config = config_for_projects(tmp_path)
    project = tmp_path / "projects" / "project"
    project.mkdir(parents=True)
    (project / "project.json").write_text(json.dumps({"template_id": "chaos-museum"}), encoding="utf-8")
    run = write_project_run(project, "run-1", "ready", "tts", "2026-07-22T10:00:00+08:00")
    calls = {}
    context = object()
    monkeypatch.setattr("main.resume_context", lambda repo, config_path, run_path: calls.setdefault("resume", (repo, config_path, run_path)) and context)
    monkeypatch.setattr("main.execute_from_current_stage", lambda value: calls.setdefault("execute", value))

    assert run_cli(["--home", str(REPO), "--config", str(config), "resume", "project"]) == 0
    assert calls["resume"][2] == run
    assert calls["execute"] is context


def test_status_and_runs_support_json_and_empty_projects(tmp_path):
    config = config_for_projects(tmp_path)
    project = tmp_path / "projects" / "project"
    project.mkdir(parents=True)
    (project / "project.json").write_text(json.dumps({"template_id": "chaos-museum"}), encoding="utf-8")
    empty_output = io.StringIO()
    assert run_cli(["--home", str(REPO), "--config", str(config), "status", "project"], stdout=empty_output) == 0
    assert "暂无 run" in empty_output.getvalue()

    write_project_run(project, "run-1", "ready", "visual_plan", "2026-07-22T10:00:00+08:00")
    output = io.StringIO()
    assert run_cli(["--home", str(REPO), "--config", str(config), "runs", "project", "--json"], stdout=output) == 0
    values = json.loads(output.getvalue())
    assert values[0]["run_id"] == "run-1"
    assert values[0]["current_stage"] == "visual_plan"


@pytest.mark.parametrize(
    ("command", "repair"),
    [("audit", False), ("repair", True)],
)
def test_subtitle_sync_commands_dispatch_selected_run(
    tmp_path, monkeypatch, command, repair
):
    config = config_for_projects(tmp_path)
    project = tmp_path / "projects" / "project"
    project.mkdir(parents=True)
    (project / "project.json").write_text(
        json.dumps({"template_id": "chaos-museum"}),
        encoding="utf-8",
    )
    run = write_project_run(
        project,
        "run-1",
        "ready",
        "subtitle_sync",
        "2026-07-22T10:00:00+08:00",
    )
    context = SimpleNamespace(run_dir=run)
    calls = []
    monkeypatch.setattr(
        "main.resume_context",
        lambda _home, _config, run_path: context if run_path == run else None,
    )
    monkeypatch.setattr(
        "main.audit_subtitles_for_context",
        lambda selected, allow_repair: calls.append((selected, allow_repair))
        or {"status": "passed"},
    )

    assert run_cli([
        "--home",
        str(REPO),
        "--config",
        str(config),
        command,
        "subtitles",
        "project",
        "--run",
        "run-1",
    ]) == 0
    assert calls == [(context, repair)]
