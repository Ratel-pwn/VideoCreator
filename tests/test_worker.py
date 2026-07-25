import json
from pathlib import Path

from videocreator.interactions import WorkflowOutcome
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

