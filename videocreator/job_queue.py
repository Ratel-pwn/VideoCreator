from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Job:
    id: str
    project: str
    run_id: str
    action: str
    status: str
    worker_id: str | None
    lease_until: str | None
    attempts: int
    cancel_requested: bool
    error: str | None
    created_at: str
    updated_at: str


class JobQueue:
    def __init__(self, database_path: Path, now: Callable[[], datetime] = _utc_now):
        self.database_path = Path(database_path)
        self.now = now
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
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
            connection.execute("CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_at)")

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        value = dict(row)
        value["cancel_requested"] = bool(value["cancel_requested"])
        return Job(**value)

    def get(self, project: str, run_id: str) -> Job | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE project = ? AND run_id = ?", (project, run_id)
            ).fetchone()
        return self._job(row) if row else None

    def enqueue(self, project: str, run_id: str, action: str = "advance") -> Job:
        stamp = self.now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE project = ? AND run_id = ?", (project, run_id)
            ).fetchone()
            if row and row["status"] in {"queued", "leased"}:
                connection.commit()
                return self._job(row)
            if row:
                connection.execute(
                    """
                    UPDATE jobs SET action = ?, status = 'queued', worker_id = NULL,
                        lease_until = NULL, cancel_requested = 0, error = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (action, stamp, row["id"]),
                )
                job_id = row["id"]
            else:
                job_id = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO jobs
                    (id, project, run_id, action, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (job_id, project, run_id, action, stamp, stamp),
                )
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            connection.commit()
        return self._job(row)

    def claim(self, worker_id: str, lease_seconds: int) -> Job | None:
        now = self.now()
        stamp = now.isoformat()
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE jobs SET status = 'queued', worker_id = NULL, lease_until = NULL, updated_at = ?
                WHERE status = 'leased' AND lease_until < ?
                """,
                (stamp, stamp),
            )
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued' AND cancel_requested = 0
                ORDER BY created_at, id LIMIT 1
                """
            ).fetchone()
            if not row:
                connection.commit()
                return None
            connection.execute(
                """
                UPDATE jobs SET status = 'leased', worker_id = ?, lease_until = ?,
                    attempts = attempts + 1, updated_at = ? WHERE id = ?
                """,
                (worker_id, lease_until, stamp, row["id"]),
            )
            claimed = connection.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
            connection.commit()
        return self._job(claimed)

    def renew(self, job_id: str, worker_id: str, lease_seconds: int) -> bool:
        lease_until = (self.now() + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET lease_until = ?, updated_at = ?
                WHERE id = ? AND status = 'leased' AND worker_id = ?
                """,
                (lease_until, self.now().isoformat(), job_id, worker_id),
            )
        return cursor.rowcount == 1

    def release_waiting(self, job_id: str, worker_id: str) -> None:
        self._finish(job_id, worker_id, "waiting", None)

    def complete(self, job_id: str, worker_id: str, status: str = "completed") -> None:
        if status not in {"completed", "cancelled"}:
            raise ValueError(f"Unsupported terminal status: {status}")
        self._finish(job_id, worker_id, status, None)

    def fail(self, job_id: str, worker_id: str, error: str) -> None:
        self._finish(job_id, worker_id, "failed", error)

    def _finish(self, job_id: str, worker_id: str, status: str, error: str | None) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status = ?, worker_id = NULL, lease_until = NULL,
                    error = ?, updated_at = ?
                WHERE id = ? AND status = 'leased' AND worker_id = ?
                """,
                (status, error, self.now().isoformat(), job_id, worker_id),
            )
        if cursor.rowcount != 1:
            raise RuntimeError(f"Job lease is not owned by {worker_id}: {job_id}")

    def request_cancel(self, project: str, run_id: str) -> Job:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE project = ? AND run_id = ?", (project, run_id)
            ).fetchone()
            if not row:
                connection.rollback()
                raise KeyError(f"Job not found: {project}/{run_id}")
            status = "cancelled" if row["status"] in {"queued", "waiting"} else row["status"]
            connection.execute(
                """
                UPDATE jobs SET status = ?, cancel_requested = 1, updated_at = ? WHERE id = ?
                """,
                (status, self.now().isoformat(), row["id"]),
            )
            updated = connection.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
            connection.commit()
        return self._job(updated)

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return bool(row and row["cancel_requested"])

    def reconcile(self) -> int:
        stamp = self.now().isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status = 'queued', worker_id = NULL, lease_until = NULL, updated_at = ?
                WHERE status = 'leased' AND lease_until < ?
                """,
                (stamp, stamp),
            )
        return cursor.rowcount

