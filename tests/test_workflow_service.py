import json
from pathlib import Path

import pytest

from videocreator.job_queue import JobQueue
from videocreator.runtime_config import McpRuntimeConfig
from videocreator.workflow_service import ServiceError, WorkflowService


REPO = Path(__file__).resolve().parents[1]


def build_service(tmp_path: Path) -> WorkflowService:
    config = json.loads((REPO / "workflow.config.json").read_text(encoding="utf-8-sig"))
    config["projects"]["root"] = str(tmp_path / "projects")
    config["templates"]["root"] = str(REPO / "templates")
    config["mcp"]["runtime_dir"] = str(tmp_path / "runtime")
    config_path = tmp_path / "workflow.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    runtime = McpRuntimeConfig.from_workflow(config, REPO)
    return WorkflowService(REPO, config_path, JobQueue(runtime.database_path), runtime)


def test_service_initializes_project_and_starts_async_workflow(tmp_path: Path):
    service = build_service(tmp_path)

    project = service.initialize_project("demo", "chaos-museum", title="Demo")
    started = service.start_workflow("demo", "A topic", run_id="run-1")

    assert project["template_id"] == "chaos-museum"
    assert started["run_id"] == "run-1"
    assert started["status"] == "queued"
    assert service.get_workflow_status("demo", "run-1")["current_stage"] == "prepare"


def test_service_submits_only_current_interaction_and_requeues(tmp_path: Path):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    service.start_workflow("demo", "A topic", run_id="run-1")
    run = tmp_path / "projects/demo/runs/run-1"
    state_path = run / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "waiting_for_input"
    state["pending_interaction"] = {
        "id": "question-1", "key": "approval", "kind": "confirmation",
        "prompt": "Approve?", "choices": ["y", "n"],
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    claimed = service.queue.claim("worker", 60)
    service.queue.release_waiting(claimed.id, "worker")

    with pytest.raises(ServiceError) as stale:
        service.submit_workflow_input("demo", "run-1", "wrong", "y")
    assert stale.value.code == "state_conflict"

    result = service.submit_workflow_input("demo", "run-1", "question-1", "y")
    assert result["status"] == "queued"
    assert service.queue.get("demo", "run-1").status == "queued"


def test_result_returns_text_but_never_media_binary(tmp_path: Path):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    service.start_workflow("demo", "A topic", run_id="run-1")
    run = tmp_path / "projects/demo/runs/run-1"
    script = run / "writing/script.approved.md"
    video = run / "render/final.mp4"
    script.write_text("approved", encoding="utf-8")
    video.write_bytes(b"not-returned")
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"] = {"draft_approved": str(script), "final_video": str(video)}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = service.get_workflow_result("demo", "run-1", include_text=["draft_approved", "final_video"])

    assert result["artifacts"]["draft_approved"]["content"] == "approved"
    assert "content" not in result["artifacts"]["final_video"]
    assert result["artifacts"]["final_video"]["size"] == len(b"not-returned")


def test_queue_failure_is_projected_as_workflow_failure(tmp_path: Path):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    service.start_workflow("demo", "A topic", run_id="run-1")
    job = service.queue.claim("worker", 60)
    service.queue.fail(job.id, "worker", "worker crashed")

    status = service.get_workflow_status("demo", "run-1")

    assert status["status"] == "failed"
    assert status["error"] == "worker crashed"
