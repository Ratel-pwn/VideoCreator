import json
from pathlib import Path

from main import load_json, resume_context


def test_load_json_accepts_utf8_bom(tmp_path):
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps({"renderer": "remotion"}), encoding="utf-8-sig")

    assert load_json(path) == {"renderer": "remotion"}


def test_resume_context_uses_project_containing_the_run(tmp_path: Path):
    repo_root = tmp_path / "isolated-code"
    project_root = tmp_path / "real-project"
    run_dir = project_root / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    repo_root.mkdir()
    config_path = repo_root / "workflow.config.json"
    config_path.write_text(
        json.dumps({"projects": {"root": "projects"}}), encoding="utf-8"
    )
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "project_name": "real-project",
                "current_stage": "video_render",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps({"project_name": "real-project", "artifacts": {}}),
        encoding="utf-8",
    )

    context = resume_context(repo_root, config_path, run_dir)

    assert context.project_root == project_root
