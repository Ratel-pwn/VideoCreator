from __future__ import annotations

import hashlib
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
    lease_generation: int
    attempts: int
    cancel_requested: bool
    error: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class JobInput:
    job_id: str
    interaction_id: str
    fingerprint: str
    response: str
    created_at: str


class JobCancelledError(RuntimeError):
    pass


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
                    lease_generation INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project, run_id)
                )
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(jobs)")
            }
            if "lease_generation" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN lease_generation "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute("CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_at)")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS job_inputs (
                    job_id TEXT NOT NULL,
                    interaction_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    response TEXT,
                    response_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    consumed_at TEXT,
                    PRIMARY KEY (job_id, interaction_id),
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS job_inputs_pending "
                "ON job_inputs(job_id, status, created_at)"
            )

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        value = dict(row)
        value["cancel_requested"] = bool(value["cancel_requested"])
        return Job(**value)

    @staticmethod
    def _lease_is_live(value: object, now: datetime) -> bool:
        if not isinstance(value, str):
            return False
        try:
            return datetime.fromisoformat(value) > now
        except (TypeError, ValueError):
            return False

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
            if row and (row["cancel_requested"] or row["status"] == "cancelled"):
                connection.rollback()
                raise JobCancelledError(f"Job is cancelled: {project}/{run_id}")
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

    def resume_failed(
        self,
        project: str,
        run_id: str,
        action: str = "advance",
    ) -> Job:
        stamp = self.now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE project = ? AND run_id = ?",
                (project, run_id),
            ).fetchone()
            if not row:
                connection.rollback()
                raise KeyError(f"Job not found: {project}/{run_id}")
            if row["status"] != "failed":
                connection.rollback()
                raise ValueError(
                    f"Job can only resume from failed, not {row['status']}"
                )
            connection.execute(
                """
                UPDATE jobs SET action = ?, status = 'queued', worker_id = NULL,
                    lease_until = NULL, cancel_requested = 0, error = NULL,
                    updated_at = ?
                WHERE id = ? AND status = 'failed'
                """,
                (action, stamp, row["id"]),
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (row["id"],),
            ).fetchone()
            connection.commit()
        return self._job(updated)

    def claim(self, worker_id: str, lease_seconds: int) -> Job | None:
        now = self.now()
        stamp = now.isoformat()
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE jobs SET
                    status = CASE WHEN cancel_requested = 1 THEN 'cancelled' ELSE 'queued' END,
                    worker_id = NULL, lease_until = NULL, updated_at = ?
                WHERE status = 'leased' AND lease_until < ?
                """,
                (stamp, stamp),
            )
            connection.execute(
                """
                UPDATE job_inputs SET status = 'cancelled', response = NULL,
                    consumed_at = ?
                WHERE status = 'pending' AND job_id IN (
                    SELECT id FROM jobs
                    WHERE status = 'cancelled' AND cancel_requested = 1
                )
                """,
                (stamp,),
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
                    lease_generation = lease_generation + 1,
                    attempts = attempts + 1, updated_at = ? WHERE id = ?
                """,
                (worker_id, lease_until, stamp, row["id"]),
            )
            claimed = connection.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
            connection.commit()
        return self._job(claimed)

    def renew(
        self,
        job_id: str,
        worker_id: str,
        lease_seconds: int,
        lease_generation: int | None = None,
    ) -> bool:
        now = self.now()
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if (
                not row
                or row["status"] != "leased"
                or row["worker_id"] != worker_id
                or row["cancel_requested"]
                or not self._lease_is_live(row["lease_until"], now)
                or (
                    lease_generation is not None
                    and row["lease_generation"] != lease_generation
                )
            ):
                connection.rollback()
                return False
            cursor = connection.execute(
                """
                UPDATE jobs SET lease_until = ?, updated_at = ?
                WHERE id = ? AND status = 'leased' AND worker_id = ?
                    AND lease_generation = ?
                """,
                (
                    lease_until,
                    now.isoformat(),
                    job_id,
                    worker_id,
                    row["lease_generation"],
                ),
            )
            connection.commit()
        return cursor.rowcount == 1

    def release_waiting(
        self,
        job_id: str,
        worker_id: str,
        lease_generation: int | None = None,
    ) -> None:
        self._finish(
            job_id,
            worker_id,
            "waiting",
            None,
            lease_generation=lease_generation,
        )

    def complete(
        self,
        job_id: str,
        worker_id: str,
        status: str = "completed",
        lease_generation: int | None = None,
    ) -> None:
        if status not in {"completed", "cancelled"}:
            raise ValueError(f"Unsupported terminal status: {status}")
        self._finish(
            job_id,
            worker_id,
            status,
            None,
            lease_generation=lease_generation,
        )

    def fail(
        self,
        job_id: str,
        worker_id: str,
        error: str,
        lease_generation: int | None = None,
    ) -> None:
        self._finish(
            job_id,
            worker_id,
            "failed",
            error,
            lease_generation=lease_generation,
        )

    def _finish(
        self,
        job_id: str,
        worker_id: str,
        status: str,
        error: str | None,
        *,
        lease_generation: int | None = None,
    ) -> None:
        now = self.now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if (
                not row
                or row["status"] != "leased"
                or row["worker_id"] != worker_id
                or not self._lease_is_live(row["lease_until"], now)
                or (
                    lease_generation is not None
                    and row["lease_generation"] != lease_generation
                )
            ):
                connection.rollback()
                raise RuntimeError(
                    f"Job lease is not owned by {worker_id}: {job_id}"
                )
            pending = connection.execute(
                """
                SELECT 1 FROM job_inputs
                WHERE job_id = ? AND status = 'pending'
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            final_status = (
                "cancelled"
                if row["cancel_requested"]
                else "queued"
                if pending
                else status
            )
            connection.execute(
                """
                UPDATE jobs SET status = ?, worker_id = NULL, lease_until = NULL,
                    error = ?, updated_at = ?
                WHERE id = ? AND status = 'leased' AND worker_id = ?
                """,
                (
                    final_status,
                    error if final_status == status else None,
                    now.isoformat(),
                    job_id,
                    worker_id,
                ),
            )
            if final_status == "cancelled":
                connection.execute(
                    """
                    UPDATE job_inputs SET status = 'cancelled', response = NULL,
                        consumed_at = ?
                    WHERE job_id = ? AND status = 'pending'
                    """,
                    (now.isoformat(), job_id),
                )
            connection.commit()

    def release_lease(
        self,
        job_id: str,
        worker_id: str,
        lease_generation: int,
    ) -> None:
        stamp = self.now().isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status = 'queued', worker_id = NULL,
                    lease_until = NULL, updated_at = ?
                WHERE id = ? AND status = 'leased' AND worker_id = ?
                    AND lease_generation = ?
                """,
                (stamp, job_id, worker_id, lease_generation),
            )
        if cursor.rowcount != 1:
            raise RuntimeError(
                f"Job lease is not owned by {worker_id}: {job_id}"
            )

    def lease_is_owned(
        self,
        job_id: str,
        worker_id: str,
        lease_generation: int,
    ) -> bool:
        now = self.now()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT lease_until FROM jobs
                WHERE id = ? AND status = 'leased' AND worker_id = ?
                    AND lease_generation = ? AND cancel_requested = 0
                """,
                (job_id, worker_id, lease_generation),
            ).fetchone()
        return bool(
            row and self._lease_is_live(row["lease_until"], now)
        )

    def request_cancel(self, project: str, run_id: str) -> Job:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE project = ? AND run_id = ?", (project, run_id)
            ).fetchone()
            if not row:
                connection.rollback()
                raise KeyError(f"Job not found: {project}/{run_id}")
            if row["status"] in {"completed", "failed", "cancelled"}:
                connection.commit()
                return self._job(row)
            status = "leased" if row["status"] == "leased" else "cancelled"
            connection.execute(
                """
                UPDATE jobs SET status = ?, cancel_requested = 1, updated_at = ? WHERE id = ?
                """,
                (status, self.now().isoformat(), row["id"]),
            )
            connection.execute(
                """
                UPDATE job_inputs SET status = 'cancelled', response = NULL,
                    consumed_at = ?
                WHERE job_id = ? AND status = 'pending'
                """,
                (self.now().isoformat(), row["id"]),
            )
            updated = connection.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
            connection.commit()
        return self._job(updated)

    def waiting_jobs(self) -> tuple[Job, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE status = 'waiting' ORDER BY created_at, id"
            ).fetchall()
        return tuple(self._job(row) for row in rows)

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return bool(row and row["cancel_requested"])

    def reconcile(self) -> int:
        stamp = self.now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE jobs SET
                    status = CASE WHEN cancel_requested = 1 THEN 'cancelled' ELSE 'queued' END,
                    worker_id = NULL, lease_until = NULL, updated_at = ?
                WHERE status = 'leased' AND lease_until < ?
                """,
                (stamp, stamp),
            )
            connection.execute(
                """
                UPDATE job_inputs SET status = 'cancelled', response = NULL,
                    consumed_at = ?
                WHERE status = 'pending' AND job_id IN (
                    SELECT id FROM jobs
                    WHERE status = 'cancelled' AND cancel_requested = 1
                )
                """,
                (stamp,),
            )
            connection.commit()
        return cursor.rowcount

    def submit_input(
        self,
        project: str,
        run_id: str,
        interaction_id: str,
        fingerprint: str,
        response: str,
    ) -> bool:
        response_sha256 = hashlib.sha256(response.encode("utf-8")).hexdigest()
        stamp = self.now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT * FROM jobs WHERE project = ? AND run_id = ?",
                (project, run_id),
            ).fetchone()
            if not job:
                connection.rollback()
                raise KeyError(f"Job not found: {project}/{run_id}")
            existing = connection.execute(
                """
                SELECT * FROM job_inputs
                WHERE job_id = ? AND interaction_id = ?
                """,
                (job["id"], interaction_id),
            ).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    connection.rollback()
                    raise ValueError("Interaction already has a different fingerprint")
                if existing["response_sha256"] != response_sha256:
                    connection.rollback()
                    raise ValueError("Interaction already has a different response")
                connection.commit()
                return False
            if job["cancel_requested"] or job["status"] == "cancelled":
                connection.rollback()
                raise JobCancelledError(f"Job is cancelled: {project}/{run_id}")
            if job["status"] in {"completed", "failed"}:
                connection.rollback()
                raise ValueError(f"Job cannot accept input from {job['status']}")
            connection.execute(
                """
                INSERT INTO job_inputs
                (job_id, interaction_id, fingerprint, response, response_sha256,
                    status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    job["id"],
                    interaction_id,
                    fingerprint,
                    response,
                    response_sha256,
                    stamp,
                ),
            )
            if job["status"] == "waiting":
                connection.execute(
                    """
                    UPDATE jobs SET status = 'queued', worker_id = NULL,
                        lease_until = NULL, error = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (stamp, job["id"]),
                )
            connection.commit()
        return True

    def pending_inputs(self, job_id: str) -> tuple[JobInput, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT job_id, interaction_id, fingerprint, response, created_at
                FROM job_inputs
                WHERE job_id = ? AND status = 'pending'
                ORDER BY created_at, interaction_id
                """,
                (job_id,),
            ).fetchall()
        return tuple(
            JobInput(
                job_id=row["job_id"],
                interaction_id=row["interaction_id"],
                fingerprint=row["fingerprint"],
                response=row["response"],
                created_at=row["created_at"],
            )
            for row in rows
        )

    def has_input(self, job_id: str, interaction_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM job_inputs
                WHERE job_id = ? AND interaction_id = ?
                """,
                (job_id, interaction_id),
            ).fetchone()
        return row is not None

    def retry_input(
        self,
        project: str,
        run_id: str,
        interaction_id: str,
        response: str,
    ) -> bool:
        response_sha256 = hashlib.sha256(response.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT input.response_sha256
                FROM job_inputs AS input
                JOIN jobs AS job ON job.id = input.job_id
                WHERE job.project = ? AND job.run_id = ?
                    AND input.interaction_id = ?
                """,
                (project, run_id, interaction_id),
            ).fetchone()
        if not row:
            raise KeyError(f"Stale interaction: {interaction_id}")
        if row["response_sha256"] != response_sha256:
            raise ValueError("Interaction already has a different response")
        return False

    def acknowledge_input(self, job_id: str, interaction_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE job_inputs SET status = 'consumed', response = NULL,
                    consumed_at = ?
                WHERE job_id = ? AND interaction_id = ? AND status = 'pending'
                """,
                (self.now().isoformat(), job_id, interaction_id),
            )
        return cursor.rowcount == 1
