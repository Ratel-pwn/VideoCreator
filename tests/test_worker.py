import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

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


def test_worker_stops_run_mutation_after_heartbeat_loses_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service = prepare(tmp_path)
    execute_started = threading.Event()
    heartbeat_ran = threading.Event()
    observed: dict[str, bool] = {}

    def execute(ctx):
        execute_started.set()
        assert heartbeat_ran.wait(timeout=2)
        ctx.state["stale_worker_write"] = True
        with pytest.raises(RuntimeError, match="lease"):
            ctx.save_state()
        observed["fenced"] = True
        return WorkflowOutcome("completed")

    worker = WorkflowWorker(
        service,
        worker_id="worker-loses-lease",
        execute=execute,
    )

    def lose_lease(_job, _stopped, lease_lost):
        assert execute_started.wait(timeout=2)
        lease_lost.set()
        heartbeat_ran.set()

    monkeypatch.setattr(worker, "_heartbeat", lose_lease)

    assert worker.run_once()

    state = json.loads(
        (
            tmp_path / "projects/demo/runs/run-1/state.json"
        ).read_text(encoding="utf-8")
    )
    assert observed["fenced"] is True
    assert "stale_worker_write" not in state
    assert service.queue.get("demo", "run-1").status == "queued"


def test_worker_cannot_write_artifacts_after_lease_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service = prepare(tmp_path)
    execute_started = threading.Event()
    heartbeat_ran = threading.Event()

    def execute(ctx):
        import main

        execute_started.set()
        assert heartbeat_ran.wait(timeout=2)
        with pytest.raises(RuntimeError, match="lease"):
            main.persist_chat(
                ctx,
                [{"role": "user", "content": "stale artifact"}],
            )
        return WorkflowOutcome("completed")

    worker = WorkflowWorker(
        service,
        worker_id="worker-artifact-fence",
        execute=execute,
    )

    def lose_lease(_job, _stopped, lease_lost):
        assert execute_started.wait(timeout=2)
        lease_lost.set()
        heartbeat_ran.set()

    monkeypatch.setattr(worker, "_heartbeat", lose_lease)

    assert worker.run_once()

    run = tmp_path / "projects/demo/runs/run-1"
    assert not (run / "session/conversation.json").exists()
    assert not (run / "session/conversation.md").exists()


def test_cancellation_terminates_subprocess_before_output_commit(
    tmp_path: Path,
):
    service = prepare(tmp_path)
    marker = tmp_path / "cancelled-subprocess-output.txt"

    def execute(ctx):
        service.queue.request_cancel("demo", "run-1")
        ctx.run_process(
            [
                sys.executable,
                "-c",
                (
                    "import pathlib,sys,time;"
                    "time.sleep(0.5);"
                    "pathlib.Path(sys.argv[1]).write_text('stale')"
                ),
                str(marker),
            ],
            check=True,
        )
        return WorkflowOutcome("completed")

    worker = WorkflowWorker(
        service,
        worker_id="worker-cancel-subprocess",
        execute=execute,
    )

    assert worker.run_once()

    assert not marker.exists()
    assert service.queue.get("demo", "run-1").status == "cancelled"


def test_heartbeat_database_error_fences_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service = prepare(tmp_path)
    worker = WorkflowWorker(service, worker_id="worker-heartbeat-error")
    job = service.queue.claim(worker.worker_id, 60)
    lease_lost = threading.Event()

    class StopAfterOneWait:
        def wait(self, _interval):
            return False

    monkeypatch.setattr(
        service.queue,
        "renew",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("database unavailable")
        ),
    )

    worker._heartbeat(job, StopAfterOneWait(), lease_lost)

    assert lease_lost.is_set()


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


def test_worker_does_not_acknowledge_input_when_state_save_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service = prepare(tmp_path)
    run = tmp_path / "projects/demo/runs/run-1"
    state_path = run / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    payload = {"query": {"subjects": ["economics"]}}
    fingerprint = interaction_fingerprint("bgm_candidates", payload)
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
    assert service.queue.submit_input(
        "demo", "run-1", "bgm-1", fingerprint, "secret answer"
    )

    import main

    monkeypatch.setattr(
        main,
        "save_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    worker = WorkflowWorker(service, worker_id="worker-save-failure")

    assert worker.run_once()

    job = service.queue.get("demo", "run-1")
    assert job.status == "queued"
    assert service.queue.pending_inputs(job.id)[0].response == "secret answer"


def test_worker_imports_legacy_state_only_answer_and_wakes_waiting_job(
    tmp_path: Path,
):
    service = prepare(tmp_path)
    run = tmp_path / "projects/demo/runs/run-1"
    state_path = run / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    payload = {"query": {"subjects": ["economics"]}}
    fingerprint = interaction_fingerprint("bgm_candidates", payload)
    state["status"] = "waiting_for_input"
    state["pending_interaction"] = {
        "id": "legacy-bgm-1",
        "key": "bgm-online-candidates",
        "kind": "bgm_candidates",
        "prompt": "Find BGM",
        "choices": [],
        "payload": payload,
        "fingerprint": fingerprint,
        "response": "legacy answer",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    claimed = service.queue.claim("setup", 60)
    service.queue.release_waiting(claimed.id, "setup")
    observed: dict[str, str] = {}

    def execute(ctx):
        observed["answer"] = ctx.interactions.ask(
            ctx,
            "bgm-online-candidates",
            "Find BGM",
            "bgm_candidates",
            payload=payload,
        )
        return WorkflowOutcome("completed")

    worker = WorkflowWorker(service, worker_id="worker-legacy", execute=execute)
    assert worker.run_once()

    assert observed["answer"] == "legacy answer"
    assert service.queue.get("demo", "run-1").status == "completed"
