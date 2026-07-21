import json
from pathlib import Path

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
