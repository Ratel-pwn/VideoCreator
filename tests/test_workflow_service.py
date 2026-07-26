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


def test_start_workflow_merges_dispatch_into_latest_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    original_enqueue = service.queue.enqueue
    run = tmp_path / "projects/demo/runs/run-1"
    state_path = run / "state.json"

    def enqueue_after_state_advance(project, run_id):
        latest = json.loads(state_path.read_text(encoding="utf-8"))
        latest["current_stage"] = "draft"
        latest["status"] = "running"
        state_path.write_text(json.dumps(latest), encoding="utf-8")
        return original_enqueue(project, run_id)

    monkeypatch.setattr(
        service.queue,
        "enqueue",
        enqueue_after_state_advance,
    )

    service.start_workflow("demo", "A topic", run_id="run-1")

    durable = json.loads(state_path.read_text(encoding="utf-8"))
    assert durable["current_stage"] == "draft"
    assert durable["status"] == "running"
    assert durable["queue_outbox"]["status"] == "dispatched"


@pytest.mark.parametrize(
    "run_id",
    [
        "../escape",
        r"..\escape",
        "nested/run",
        r"nested\run",
        r"C:\escape",
        "C:relative",
        r"\\server\share\run",
        ".",
        "..",
    ],
)
def test_service_rejects_run_ids_that_are_not_one_safe_component(
    tmp_path: Path,
    run_id: str,
):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")

    with pytest.raises(ServiceError) as rejected:
        service.start_workflow("demo", "A topic", run_id=run_id)

    assert rejected.value.code == "invalid_argument"
    assert list((tmp_path / "projects/demo/runs").iterdir()) == []


def test_service_refuses_to_reopen_or_overwrite_an_existing_run(tmp_path: Path):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    service.start_workflow("demo", "Original topic", run_id="run-1")
    manifest_path = tmp_path / "projects/demo/runs/run-1/manifest.json"
    original = manifest_path.read_bytes()

    with pytest.raises(ServiceError) as duplicate:
        service.start_workflow("demo", "Replacement topic", run_id="run-1")

    assert duplicate.value.code == "state_conflict"
    assert manifest_path.read_bytes() == original


def test_service_recovers_run_created_before_queue_enqueue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")

    monkeypatch.setattr(
        service.queue,
        "enqueue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated queue outage")
        ),
    )
    with pytest.raises(ServiceError, match="queue"):
        service.start_workflow("demo", "A topic", run_id="run-1")

    state_path = tmp_path / "projects/demo/runs/run-1/state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["queue_outbox"]["status"] == "pending"

    recovered = build_service(tmp_path)

    job = recovered.queue.get("demo", "run-1")
    assert job is not None
    assert job.status == "queued"
    recovered_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert recovered_state["queue_outbox"]["status"] == "dispatched"
    assert recovered_state["queue_outbox"]["job_id"] == job.id


def test_published_run_has_outbox_before_context_construction_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import main

    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    monkeypatch.setattr(
        main,
        "WorkflowContext",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("context construction crash")
        ),
    )

    with pytest.raises(RuntimeError, match="context construction crash"):
        service.start_workflow(
            "demo",
            "A topic",
            context="immutable context",
            run_id="run-1",
        )

    run = tmp_path / "projects/demo/runs/run-1"
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert state["execution_owner"] == "mcp"
    assert state["queue_outbox"]["status"] == "pending"
    assert (run / "inputs/agent-context.md").read_text(
        encoding="utf-8"
    ).strip() == "immutable context"
    assert manifest["lineage"]["agent_context"]["sha256"]

    recovered = build_service(tmp_path)
    assert recovered.queue.get("demo", "run-1").status == "queued"


def test_service_recovers_resume_requested_before_queue_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    service.start_workflow("demo", "A topic", run_id="run-1")
    claimed = service.queue.claim("worker", 60)
    service.queue.fail(
        claimed.id,
        "worker",
        "failed before resume",
        lease_generation=claimed.lease_generation,
    )
    run = tmp_path / "projects/demo/runs/run-1"
    state_path = run / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "failed"
    state["last_error"] = "failed before resume"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    monkeypatch.setattr(
        service.queue,
        "resume_failed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated queue outage")
        ),
    )
    with pytest.raises(ServiceError, match="resume"):
        service.resume_workflow("demo", "run-1")

    pending = json.loads(state_path.read_text(encoding="utf-8"))
    assert pending["status"] == "ready"
    assert pending["queue_outbox"]["action"] == "resume"
    assert pending["queue_outbox"]["status"] == "pending"

    recovered = build_service(tmp_path)

    job = recovered.queue.get("demo", "run-1")
    assert job.status == "queued"
    durable = json.loads(state_path.read_text(encoding="utf-8"))
    assert durable["queue_outbox"]["status"] == "dispatched"
    assert durable["queue_outbox"]["job_id"] == job.id


def test_resume_workflow_merges_dispatch_into_latest_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    service.start_workflow("demo", "A topic", run_id="run-1")
    claimed = service.queue.claim("worker", 60)
    service.queue.fail(
        claimed.id,
        "worker",
        "failed before resume",
        lease_generation=claimed.lease_generation,
    )
    run = tmp_path / "projects/demo/runs/run-1"
    state_path = run / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "failed"
    state["last_error"] = "failed before resume"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    original_resume = service.queue.resume_failed

    def resume_after_state_advance(project, run_id):
        latest = json.loads(state_path.read_text(encoding="utf-8"))
        latest["current_stage"] = "draft"
        latest["status"] = "running"
        state_path.write_text(json.dumps(latest), encoding="utf-8")
        return original_resume(project, run_id)

    monkeypatch.setattr(
        service.queue,
        "resume_failed",
        resume_after_state_advance,
    )

    service.resume_workflow("demo", "run-1")

    durable = json.loads(state_path.read_text(encoding="utf-8"))
    assert durable["current_stage"] == "draft"
    assert durable["status"] == "running"
    assert durable["queue_outbox"]["status"] == "dispatched"


def test_outbox_reconciliation_merges_only_outbox_into_latest_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    service.start_workflow("demo", "A topic", run_id="run-1")
    run = tmp_path / "projects/demo/runs/run-1"
    state_path = run / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["queue_outbox"] = {
        "schema_version": 1,
        "generation": 7,
        "action": "advance",
        "status": "pending",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with sqlite3.connect(service.queue.database_path) as connection:
        connection.execute(
            "DELETE FROM jobs WHERE project = ? AND run_id = ?",
            ("demo", "run-1"),
        )
    original_enqueue = service.queue.enqueue

    def enqueue_with_concurrent_state_advance(project, run_id):
        latest = json.loads(state_path.read_text(encoding="utf-8"))
        latest["current_stage"] = "draft"
        latest["status"] = "running"
        state_path.write_text(json.dumps(latest), encoding="utf-8")
        return original_enqueue(project, run_id)

    monkeypatch.setattr(
        service.queue,
        "enqueue",
        enqueue_with_concurrent_state_advance,
    )

    assert service.reconcile_workflow_outbox() == 1

    durable = json.loads(state_path.read_text(encoding="utf-8"))
    job = service.queue.get("demo", "run-1")
    assert durable["current_stage"] == "draft"
    assert durable["status"] == "running"
    assert durable["queue_outbox"]["generation"] == 7
    assert durable["queue_outbox"]["status"] == "dispatched"
    assert durable["queue_outbox"]["job_id"] == job.id
    assert durable["queue_outbox"]["job_lease_generation"] == (
        job.lease_generation
    )


def test_outbox_reconciliation_skips_run_owned_by_worker(
    tmp_path: Path,
):
    from videocreator.execution_fence import RunMutationLock

    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    service.start_workflow("demo", "A topic", run_id="run-1")
    run = tmp_path / "projects/demo/runs/run-1"
    lock = RunMutationLock(run / ".worker.lock")
    assert lock.acquire()
    try:
        assert service.reconcile_workflow_outbox() == 0
    finally:
        lock.release()


def test_service_freezes_context_without_exposing_it_in_status_state_or_manifest(
    tmp_path: Path,
):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    secret_context = "Audience: researchers\nINTERNAL-CONTEXT-SENTINEL"

    service.start_workflow(
        "demo",
        "A topic",
        context=secret_context,
        run_id="run-1",
    )

    run = tmp_path / "projects/demo/runs/run-1"
    snapshot = run / "inputs/agent-context.md"
    state_text = (run / "state.json").read_text(encoding="utf-8")
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    status = service.get_workflow_status("demo", "run-1")
    assert snapshot.read_text(encoding="utf-8").strip() == secret_context
    assert manifest["lineage"]["agent_context"]["sha256"]
    assert manifest["private_artifacts"]["agent_context"] == str(snapshot)
    assert "agent_context" not in manifest["artifacts"]
    assert "INTERNAL-CONTEXT-SENTINEL" not in state_text
    assert "INTERNAL-CONTEXT-SENTINEL" not in json.dumps(manifest)
    assert "INTERNAL-CONTEXT-SENTINEL" not in json.dumps(status)


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


def test_result_uses_strict_public_allowlist_and_current_run_containment(
    tmp_path: Path,
):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    service.start_workflow(
        "demo",
        "A topic",
        context="PRIVATE-CONTEXT",
        run_id="run-1",
    )
    run = tmp_path / "projects/demo/runs/run-1"
    local_config = tmp_path / "workflow.local.json"
    local_config.write_text("LOCAL-CONFIG-SECRET", encoding="utf-8")
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"].update(
        {
            "agent_context": str(run / "inputs/agent-context.md"),
            "session_json": str(run / "inputs/agent-context.md"),
            "draft_approved": str(run / "inputs/agent-context.md"),
            "final_video": str(local_config),
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = service.get_workflow_result("demo", "run-1")

    assert result["artifacts"] == {}
    assert "agent_context" not in result["artifacts"]
    assert "session_json" not in service.get_workflow_status(
        "demo",
        "run-1",
    )["artifacts"]


@pytest.mark.parametrize(
    "name",
    [
        "agent_context",
        "session_json",
        "../inputs/agent-context.md",
        r"..\inputs\agent-context.md",
        "workflow.local.json",
    ],
)
def test_result_rejects_private_or_traversal_text_requests(
    tmp_path: Path,
    name: str,
):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    service.start_workflow("demo", "A topic", run_id="run-1")

    with pytest.raises(ServiceError) as rejected:
        service.get_workflow_result(
            "demo",
            "run-1",
            include_text=[name],
        )

    assert rejected.value.code == "invalid_argument"


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
