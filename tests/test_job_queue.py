from datetime import datetime, timedelta, timezone
from pathlib import Path

from videocreator.job_queue import JobQueue


def test_queue_claims_fifo_and_prevents_duplicate_run_jobs(tmp_path: Path):
    queue = JobQueue(tmp_path / "queue.sqlite3")
    first = queue.enqueue("project", "run-1")
    duplicate = queue.enqueue("project", "run-1")
    queue.enqueue("project", "run-2")

    assert duplicate.id == first.id
    claimed = queue.claim("worker-1", lease_seconds=60)
    assert claimed is not None
    assert claimed.run_id == "run-1"
    assert claimed.status == "leased"
    assert queue.claim("worker-2", lease_seconds=60).run_id == "run-2"


def test_expired_lease_can_be_recovered(tmp_path: Path):
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    queue = JobQueue(tmp_path / "queue.sqlite3", now=lambda: now)
    queue.enqueue("project", "run-1")
    claimed = queue.claim("dead-worker", lease_seconds=10)

    queue.now = lambda: now + timedelta(seconds=11)
    recovered = queue.claim("worker-2", lease_seconds=10)

    assert recovered is not None
    assert recovered.id == claimed.id
    assert recovered.worker_id == "worker-2"
    assert recovered.attempts == 2


def test_waiting_job_is_requeued_and_persists_across_instances(tmp_path: Path):
    path = tmp_path / "queue.sqlite3"
    queue = JobQueue(path)
    queue.enqueue("project", "run-1")
    claimed = queue.claim("worker", lease_seconds=60)
    queue.release_waiting(claimed.id, "worker")

    reopened = JobQueue(path)
    assert reopened.get("project", "run-1").status == "waiting"
    reopened.enqueue("project", "run-1")
    assert reopened.claim("worker-2", lease_seconds=60).run_id == "run-1"


def test_cancellation_is_visible_to_worker_and_terminal_after_completion(tmp_path: Path):
    queue = JobQueue(tmp_path / "queue.sqlite3")
    queue.enqueue("project", "run-1")
    claimed = queue.claim("worker", lease_seconds=60)

    queue.request_cancel("project", "run-1")
    assert queue.is_cancel_requested(claimed.id)
    queue.complete(claimed.id, "worker", status="cancelled")

    assert queue.get("project", "run-1").status == "cancelled"

