from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable

from .interactions import DurableInteractionPort, WorkflowOutcome
from .workflow_service import WorkflowService


class WorkflowWorker:
    def __init__(
        self,
        service: WorkflowService,
        worker_id: str | None = None,
        execute: Callable[[object], WorkflowOutcome] | None = None,
        poll_seconds: float = 0.5,
    ):
        self.service = service
        self.queue = service.queue
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        self.execute = execute
        self.poll_seconds = poll_seconds

    def _heartbeat(self, job_id: str, stopped: threading.Event) -> None:
        interval = max(1.0, self.service.runtime.lease_seconds / 3)
        while not stopped.wait(interval):
            if not self.queue.renew(job_id, self.worker_id, self.service.runtime.lease_seconds):
                return

    def run_once(self) -> bool:
        job = self.queue.claim(self.worker_id, self.service.runtime.lease_seconds)
        if job is None:
            return False
        if self.queue.is_cancel_requested(job.id):
            self.queue.complete(job.id, self.worker_id, status="cancelled")
            return True

        import main as workflow

        stopped = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(job.id, stopped), daemon=True)
        heartbeat.start()
        try:
            run = self.service._run(job.project, job.run_id)
            ctx = workflow.resume_context(self.service.home, self.service.config_path, run)
            ctx.interactions = DurableInteractionPort()
            outcome = self.execute(ctx) if self.execute else workflow.execute_until_boundary(ctx)
            if outcome.status == "waiting_for_input":
                self.queue.release_waiting(job.id, self.worker_id)
            elif outcome.status == "completed":
                self.queue.complete(job.id, self.worker_id)
            elif outcome.status == "cancelled":
                self.queue.complete(job.id, self.worker_id, status="cancelled")
            else:
                self.queue.fail(job.id, self.worker_id, outcome.error or "Workflow failed")
        except Exception as exc:
            self.queue.fail(job.id, self.worker_id, str(exc))
        finally:
            stopped.set()
            heartbeat.join(timeout=1)
        return True

    def run(self, stop_event: threading.Event) -> None:
        self.queue.reconcile()
        while not stop_event.is_set():
            if not self.run_once():
                stop_event.wait(self.poll_seconds)

