import json
from pathlib import Path

import pytest

import main
from main import make_run_context


def test_new_template_project_run_starts_at_prepare(tmp_path: Path):
    repo = Path(__file__).parents[1]
    config = json.loads((repo / "workflow.config.json").read_text(encoding="utf-8-sig"))
    config["projects"]["root"] = str(tmp_path / "projects")
    config["templates"]["root"] = str(repo / "templates")
    config_path = tmp_path / "workflow.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    context = make_run_context(repo, config_path, "chat", "topic", "run-1", None, "project", "chaos-museum")

    assert context.state["current_stage"] == "prepare"
    assert context.manifest["template"] == {"id": "chaos-museum", "version": 1}
    assert context.run_dir.joinpath("inputs/template.snapshot.json").is_file()


def test_frozen_agent_context_is_consumed_by_prepare_and_draft_prompts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo = Path(__file__).parents[1]
    config = json.loads(
        (repo / "workflow.config.json").read_text(encoding="utf-8-sig")
    )
    config["projects"]["root"] = str(tmp_path / "projects")
    config["templates"]["root"] = str(repo / "templates")
    config_path = tmp_path / "workflow.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    context_text = "Audience constraint: expert researchers"
    context = make_run_context(
        repo,
        config_path,
        "chat",
        "topic",
        "run-context",
        None,
        "project",
        "chaos-museum",
        initial_context=context_text,
    )
    monkeypatch.setenv(context.config["llm"]["api_key_env"], "test-key")
    prompts: list[list[dict[str, str]]] = []
    monkeypatch.setattr(
        main,
        "call_compatible_openai",
        lambda _base, _key, _model, messages: (
            prompts.append(messages) or "generated"
        ),
    )

    main.run_prepare(context)
    session = context.run_dir / "session/conversation.md"
    session.write_text("# Conversation\n", encoding="utf-8")
    context.register_artifact("session_md", session)
    main.generate_draft(context)

    assert context_text in prompts[0][-1]["content"]
    assert context_text in prompts[1][-1]["content"]
    assert context_text not in json.dumps(context.state)
    assert context_text not in json.dumps(context.manifest)


def test_tampered_agent_context_snapshot_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo = Path(__file__).parents[1]
    config = json.loads(
        (repo / "workflow.config.json").read_text(encoding="utf-8-sig")
    )
    config["projects"]["root"] = str(tmp_path / "projects")
    config["templates"]["root"] = str(repo / "templates")
    config_path = tmp_path / "workflow.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    context = make_run_context(
        repo,
        config_path,
        "chat",
        "topic",
        "run-tampered-context",
        None,
        "project",
        "chaos-museum",
        initial_context="original context",
    )
    snapshot = context.run_dir / "inputs/agent-context.md"
    snapshot.write_text("tampered context\n", encoding="utf-8")
    monkeypatch.setenv(context.config["llm"]["api_key_env"], "test-key")

    with pytest.raises(RuntimeError, match="Agent context snapshot"):
        main.run_prepare(context)
