import json
import sqlite3
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


def test_bgm_candidate_interaction_round_trips_unchanged(tmp_path: Path):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    service.start_workflow("demo", "A topic", run_id="run-1")
    run = tmp_path / "projects/demo/runs/run-1"
    state_path = run / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    payload = {
        "schema_version": 1,
        "query": {"subjects": ["economics"]},
        "response_schema": {"type": "object"},
    }
    state["status"] = "waiting_for_input"
    state["pending_interaction"] = {
        "id": "bgm-1",
        "key": "bgm-online-candidates",
        "kind": "bgm_candidates",
        "prompt": "Find BGM",
        "choices": [],
        "payload": payload,
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    claimed = service.queue.claim("worker", 60)
    service.queue.release_waiting(claimed.id, "worker")

    status = service.get_workflow_status("demo", "run-1")
    assert status["interaction"]["kind"] == "bgm_candidates"
    assert status["interaction"]["payload"] == payload

    response = json.dumps({"candidates": []})
    submitted = service.submit_workflow_input(
        "demo",
        "run-1",
        "bgm-1",
        response,
    )
    assert submitted["accepted"] is True
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert "response" not in saved["pending_interaction"]
    job = service.queue.get("demo", "run-1")
    assert service.queue.pending_inputs(job.id)[0].response == response
    resumed = service.get_workflow_status("demo", "run-1")
    assert resumed["status"] == "queued"
    assert resumed["interaction"] is None


def test_service_rejects_oversized_bgm_response_before_queue_persistence(
    tmp_path: Path,
):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    service.start_workflow("demo", "A topic", run_id="run-1")
    run = tmp_path / "projects/demo/runs/run-1"
    state_path = run / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "waiting_for_input"
    state["pending_interaction"] = {
        "id": "bgm-1",
        "key": "bgm-online-candidates",
        "kind": "bgm_candidates",
        "prompt": "Find BGM",
        "choices": [],
        "payload": {
            "limits": {"max_response_bytes": 80},
            "response_schema": {
                "properties": {"candidates": {"maxItems": 1}},
            },
        },
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    claimed = service.queue.claim("worker", 60)
    service.queue.release_waiting(claimed.id, "worker")

    response = json.dumps({"candidates": [], "padding": "x" * 100})
    with pytest.raises(ServiceError, match="bytes"):
        service.submit_workflow_input("demo", "run-1", "bgm-1", response)

    job = service.queue.get("demo", "run-1")
    assert service.queue.pending_inputs(job.id) == ()


def test_cancelled_bgm_interaction_cannot_be_submitted_or_requeued(tmp_path: Path):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    service.start_workflow("demo", "A topic", run_id="run-1")
    run = tmp_path / "projects/demo/runs/run-1"
    state_path = run / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "waiting_for_input"
    state["pending_interaction"] = {
        "id": "bgm-1",
        "key": "bgm-online-candidates",
        "kind": "bgm_candidates",
        "prompt": "Find BGM",
        "choices": [],
        "payload": {"schema_version": 1},
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    cancelled = service.cancel_workflow("demo", "run-1")
    assert cancelled["status"] == "cancelled"
    status = service.get_workflow_status("demo", "run-1")
    assert status["status"] == "cancelled"
    assert status["interaction"] is None

    with pytest.raises(ServiceError, match="cancelled"):
        service.submit_workflow_input(
            "demo",
            "run-1",
            "bgm-1",
            json.dumps({"candidates": []}),
        )
    assert service.queue.get("demo", "run-1").status == "cancelled"


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


@pytest.mark.parametrize("terminal", ["completed", "failed"])
def test_service_cancel_preserves_terminal_job_and_run_state(
    tmp_path: Path,
    terminal: str,
):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    service.start_workflow("demo", "A topic", run_id="run-1")
    job = service.queue.claim("worker", 60)
    if terminal == "completed":
        service.queue.complete(job.id, "worker")
    else:
        service.queue.fail(job.id, "worker", "original failure")
    state_path = tmp_path / "projects/demo/runs/run-1/state.json"
    before = state_path.read_bytes()

    cancelled = service.cancel_workflow("demo", "run-1")

    assert cancelled["status"] == terminal
    assert state_path.read_bytes() == before
    current = service.queue.get("demo", "run-1")
    assert current.status == terminal
    assert current.error == (None if terminal == "completed" else "original failure")


def test_service_resumes_legacy_failed_job_with_cancel_requested(
    tmp_path: Path,
):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    service.start_workflow("demo", "A topic", run_id="run-1")
    job = service.queue.claim("worker", 60)
    service.queue.fail(job.id, "worker", "legacy failure")
    with sqlite3.connect(service.queue.database_path) as connection:
        connection.execute(
            """
            UPDATE jobs SET cancel_requested = 1
            WHERE project = 'demo' AND run_id = 'run-1'
            """
        )

    resumed = service.resume_workflow("demo", "run-1")

    assert resumed["status"] == "queued"
    current = service.queue.get("demo", "run-1")
    assert current.cancel_requested is False
    assert current.error is None
