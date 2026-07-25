import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from videocreator.job_queue import JobCancelledError, JobQueue


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


def test_input_submission_and_waiting_wakeup_are_one_transaction(tmp_path: Path):
    queue = JobQueue(tmp_path / "queue.sqlite3")
    job = queue.enqueue("project", "run-1")
    claimed = queue.claim("worker", lease_seconds=60)
    queue.release_waiting(claimed.id, "worker")

    accepted = queue.submit_input(
        "project",
        "run-1",
        "question-1",
        "fingerprint-1",
        "answer",
    )

    assert accepted is True
    assert queue.get("project", "run-1").status == "queued"
    pending = queue.pending_inputs(job.id)
    assert len(pending) == 1
    assert pending[0].response == "answer"


def test_input_arriving_during_lease_requeues_at_safe_transition(tmp_path: Path):
    queue = JobQueue(tmp_path / "queue.sqlite3")
    queue.enqueue("project", "run-1")
    claimed = queue.claim("worker-1", lease_seconds=60)

    assert queue.submit_input(
        "project",
        "run-1",
        "question-1",
        "fingerprint-1",
        "answer",
    )
    assert queue.get("project", "run-1").status == "leased"

    queue.release_waiting(claimed.id, "worker-1")

    assert queue.get("project", "run-1").status == "queued"
    resumed = queue.claim("worker-2", lease_seconds=60)
    assert resumed.id == claimed.id


def test_consumed_input_retains_only_hash_tombstone_for_late_retries(tmp_path: Path):
    queue = JobQueue(tmp_path / "queue.sqlite3")
    job = queue.enqueue("project", "run-1")
    assert queue.submit_input(
        "project",
        "run-1",
        "question-1",
        "fingerprint-1",
        "answer",
    )

    queue.acknowledge_input(job.id, "question-1")

    assert queue.pending_inputs(job.id) == ()
    assert queue.submit_input(
        "project",
        "run-1",
        "question-1",
        "fingerprint-1",
        "answer",
    ) is False
    with pytest.raises(ValueError, match="different response"):
        queue.submit_input(
            "project",
            "run-1",
            "question-1",
            "fingerprint-1",
            "changed",
        )
    with sqlite3.connect(queue.database_path) as connection:
        stored = connection.execute(
            "SELECT response, response_sha256, status FROM job_inputs"
        ).fetchone()
    assert stored[0] is None
    assert len(stored[1]) == 64
    assert stored[2] == "consumed"


def test_cancelled_jobs_refuse_submit_and_enqueue_across_races(tmp_path: Path):
    queue = JobQueue(tmp_path / "queue.sqlite3")
    queue.enqueue("project", "run-1")
    queue.request_cancel("project", "run-1")

    with pytest.raises(JobCancelledError):
        queue.submit_input(
            "project",
            "run-1",
            "question-1",
            "fingerprint-1",
            "answer",
        )
    with pytest.raises(JobCancelledError):
        queue.enqueue("project", "run-1")
    assert queue.get("project", "run-1").status == "cancelled"


def test_expired_cancelled_lease_is_finalized_not_requeued(tmp_path: Path):
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    queue = JobQueue(tmp_path / "queue.sqlite3", now=lambda: now)
    queue.enqueue("project", "run-1")
    claimed = queue.claim("dead-worker", lease_seconds=10)
    queue.request_cancel("project", "run-1")

    queue.now = lambda: now + timedelta(seconds=11)
    assert queue.reconcile() == 1

    job = queue.get("project", "run-1")
    assert job.status == "cancelled"
    assert job.worker_id is None
    assert queue.claim("worker-2", lease_seconds=60) is None


def test_release_after_cancel_finishes_lease_as_cancelled(tmp_path: Path):
    queue = JobQueue(tmp_path / "queue.sqlite3")
    queue.enqueue("project", "run-1")
    claimed = queue.claim("worker", lease_seconds=60)
    assert queue.submit_input(
        "project",
        "run-1",
        "question-1",
        "fingerprint-1",
        "answer",
    )
    queue.request_cancel("project", "run-1")

    queue.release_waiting(claimed.id, "worker")

    assert queue.get("project", "run-1").status == "cancelled"
    assert queue.pending_inputs(claimed.id) == ()
    assert queue.retry_input(
        "project",
        "run-1",
        "question-1",
        "answer",
    ) is False


def test_job_input_schema_is_added_to_existing_database(tmp_path: Path):
    database = tmp_path / "queue.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                run_id TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                worker_id TEXT,
                lease_until TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project, run_id)
            )
            """
        )

    queue = JobQueue(database)
    job = queue.enqueue("project", "run-1")
    assert queue.submit_input(
        "project",
        "run-1",
        "question-1",
        "fingerprint-1",
        "answer",
    )
    assert queue.pending_inputs(job.id)[0].interaction_id == "question-1"


@pytest.mark.parametrize("terminal", ["completed", "failed"])
def test_cancel_preserves_completed_and_failed_jobs(
    tmp_path: Path,
    terminal: str,
):
    queue = JobQueue(tmp_path / "queue.sqlite3")
    queue.enqueue("project", "run-1")
    claimed = queue.claim("worker", lease_seconds=60)
    if terminal == "completed":
        queue.complete(claimed.id, "worker")
    else:
        queue.fail(claimed.id, "worker", "original failure")
    before = queue.get("project", "run-1")

    after = queue.request_cancel("project", "run-1")

    assert after.status == terminal
    assert after.error == before.error
    assert after.cancel_requested is False
