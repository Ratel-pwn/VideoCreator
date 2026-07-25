import json
import sqlite3
from pathlib import Path

from videocreator.interactions import (
    InteractionRequired,
    WorkflowOutcome,
    interaction_fingerprint,
)
from videocreator.worker import WorkflowWorker

from test_workflow_service import build_service


def prepare(tmp_path: Path):
    service = build_service(tmp_path)
    service.initialize_project("demo", "chaos-museum")
    service.start_workflow("demo", "A topic", run_id="run-1")
    return service


def test_worker_releases_waiting_job_without_occupying_lease(tmp_path: Path):
    service = prepare(tmp_path)
    worker = WorkflowWorker(
        service,
        worker_id="worker-1",
        execute=lambda ctx: WorkflowOutcome("waiting_for_input", {"id": "question"}),
    )

    assert worker.run_once()
    assert service.queue.get("demo", "run-1").status == "waiting"
    assert service.queue.get("demo", "run-1").worker_id is None


def test_worker_completes_or_records_failure(tmp_path: Path):
    service = prepare(tmp_path)
    completed = WorkflowWorker(service, execute=lambda ctx: WorkflowOutcome("completed"))
    assert completed.run_once()
    assert service.queue.get("demo", "run-1").status == "completed"

    service.queue.enqueue("demo", "run-1")
    failed = WorkflowWorker(service, execute=lambda ctx: WorkflowOutcome("failed", error="boom"))
    assert failed.run_once()
    assert service.queue.get("demo", "run-1").status == "failed"
    assert service.queue.get("demo", "run-1").error == "boom"


def test_worker_recovers_answer_persisted_only_in_outbox(tmp_path: Path):
    service = prepare(tmp_path)
    run = tmp_path / "projects/demo/runs/run-1"
    state_path = run / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    payload = {"schema_version": 1, "query": {"subjects": ["economics"]}}
    fingerprint = interaction_fingerprint("bgm_candidates", payload)
    state["status"] = "waiting_for_input"
    state["pending_interaction"] = {
        "id": "bgm-1",
        "key": "bgm-online-candidates",
        "kind": "bgm_candidates",
        "prompt": "Find BGM",
        "choices": [],
        "payload": payload,
        "fingerprint": fingerprint,
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    claimed = service.queue.claim("setup", 60)
    service.queue.release_waiting(claimed.id, "setup")

    # This is the crash boundary: SQLite has the response and wakeup, state.json does not.
    assert service.queue.submit_input(
        "demo",
        "run-1",
        "bgm-1",
        fingerprint,
        "answer",
    )
    assert "response" not in json.loads(state_path.read_text(encoding="utf-8"))[
        "pending_interaction"
    ]

    observed = {}

    def execute(ctx):
        observed["answer"] = ctx.interactions.ask(
            ctx,
            "bgm-online-candidates",
            "Find BGM",
            "bgm_candidates",
            payload=payload,
        )
        return WorkflowOutcome("completed")

    worker = WorkflowWorker(service, worker_id="worker-2", execute=execute)
    assert worker.run_once()
    assert observed["answer"] == "answer"
    assert service.queue.pending_inputs(claimed.id) == ()
    late = service.submit_workflow_input(
        "demo",
        "run-1",
        "bgm-1",
        "answer",
    )
    assert late["accepted"] is False
    assert late["status"] == "completed"


def test_worker_does_not_strand_answer_arriving_while_leased(tmp_path: Path):
    service = prepare(tmp_path)
    run = tmp_path / "projects/demo/runs/run-1"
    state_path = run / "state.json"
    payload = {"query": {"subjects": ["economics"]}}
    fingerprint = interaction_fingerprint("bgm_candidates", payload)
    worker = WorkflowWorker(
        service,
        worker_id="worker-1",
        execute=lambda _ctx: (
            service.queue.submit_input(
                "demo",
                "run-1",
                "bgm-1",
                fingerprint,
                "answer",
            ),
            WorkflowOutcome("waiting_for_input"),
        )[1],
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["pending_interaction"] = {
        "id": "bgm-1",
        "key": "bgm-online-candidates",
        "kind": "bgm_candidates",
        "prompt": "Find BGM",
        "choices": [],
        "payload": payload,
        "fingerprint": fingerprint,
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    assert worker.run_once()

    assert service.queue.get("demo", "run-1").status == "queued"


def test_worker_tolerates_cancelled_lease_reconciled_after_crash_boundary(
    tmp_path: Path,
):
    service = prepare(tmp_path)

    def execute(_ctx):
        service.queue.request_cancel("demo", "run-1")
        with sqlite3.connect(service.queue.database_path) as connection:
            connection.execute(
                "UPDATE jobs SET lease_until = ? WHERE project = ? AND run_id = ?",
                ("2000-01-01T00:00:00+00:00", "demo", "run-1"),
            )
        service.queue.reconcile()
        return WorkflowOutcome("completed")

    worker = WorkflowWorker(service, worker_id="worker-1", execute=execute)

    assert worker.run_once()
    assert service.queue.get("demo", "run-1").status == "cancelled"
