from __future__ import annotations

import threading
import uuid
from collections.abc import Callable

from .execution_fence import (
    LeaseLostError,
    RunMutationLock,
    run_cancellable_process,
)
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

    def _heartbeat(
        self,
        job: object,
        stopped: threading.Event,
        lease_lost: threading.Event,
    ) -> None:
        interval = max(1.0, self.service.runtime.lease_seconds / 3)
        while not stopped.wait(interval):
            try:
                renewed = self.queue.renew(
                    job.id,
                    self.worker_id,
                    self.service.runtime.lease_seconds,
                    lease_generation=job.lease_generation,
                )
            except Exception:
                lease_lost.set()
                return
            if not renewed:
                lease_lost.set()
                return

    def run_once(self) -> bool:
        self.service.recover_legacy_inputs()
        job = self.queue.claim(self.worker_id, self.service.runtime.lease_seconds)
        if job is None:
            return False
        if self.queue.is_cancel_requested(job.id):
            self.queue.complete(
                job.id,
                self.worker_id,
                status="cancelled",
                lease_generation=job.lease_generation,
            )
            return True

        import main as workflow

        run = self.service._run(job.project, job.run_id)
        run_lock = RunMutationLock(run / ".worker.lock")
        if not run_lock.acquire():
            self.queue.release_lease(
                job.id,
                self.worker_id,
                job.lease_generation,
            )
            return True
        stopped = threading.Event()
        lease_lost = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat,
            args=(job, stopped, lease_lost),
            daemon=True,
        )
        heartbeat.start()

        def assert_lease() -> None:
            if lease_lost.is_set() or not self.queue.lease_is_owned(
                job.id,
                self.worker_id,
                job.lease_generation,
            ):
                lease_lost.set()
                raise LeaseLostError(
                    f"Job lease was lost: {job.project}/{job.run_id}"
                )

        def process_runner(command: object, **kwargs: object) -> object:
            return run_cancellable_process(
                command,
                cancelled=lambda: (
                    lease_lost.is_set()
                    or self.queue.is_cancel_requested(job.id)
                ),
                **kwargs,
            )

        try:
            ctx = workflow.resume_context(
                self.service.home,
                self.service.config_path,
                run,
                mutation_guard=assert_lease,
                process_runner=process_runner,
            )
            port = DurableInteractionPort()
            ctx.interactions = port
            ctx.should_cancel = lambda: (
                lease_lost.is_set()
                or self.queue.is_cancel_requested(job.id)
            )
            for item in self.queue.pending_inputs(job.id):
                try:
                    port.submit(
                        ctx,
                        item.interaction_id,
                        item.response,
                        fingerprint=item.fingerprint,
                    )
                except ValueError:
                    # The interaction changed after submission; retain no replayable
                    # answer and let the workflow request the current payload.
                    pass
                # The outbox tombstone is safe only after the corresponding
                # state transition (including stale-answer cleanup) is durable.
                ctx.save_state()
                self.queue.acknowledge_input(job.id, item.interaction_id)
            outcome = self.execute(ctx) if self.execute else workflow.execute_until_boundary(ctx)
            assert_lease()
            if outcome.status == "waiting_for_input":
                self.queue.release_waiting(
                    job.id,
                    self.worker_id,
                    lease_generation=job.lease_generation,
                )
            elif outcome.status == "completed":
                self.queue.complete(
                    job.id,
                    self.worker_id,
                    lease_generation=job.lease_generation,
                )
            elif outcome.status == "cancelled":
                self.queue.complete(
                    job.id,
                    self.worker_id,
                    status="cancelled",
                    lease_generation=job.lease_generation,
                )
            else:
                self.queue.fail(
                    job.id,
                    self.worker_id,
                    outcome.error or "Workflow failed",
                    lease_generation=job.lease_generation,
                )
        except LeaseLostError:
            if self.queue.is_cancel_requested(job.id):
                current = self.queue.get(job.project, job.run_id)
                if current is not None and current.status == "leased":
                    self.queue.complete(
                        job.id,
                        self.worker_id,
                        status="cancelled",
                        lease_generation=job.lease_generation,
                    )
            elif self.queue.lease_is_owned(
                job.id,
                self.worker_id,
                job.lease_generation,
            ):
                self.queue.release_lease(
                    job.id,
                    self.worker_id,
                    job.lease_generation,
                )
        except Exception as exc:
            try:
                self.queue.fail(
                    job.id,
                    self.worker_id,
                    str(exc),
                    lease_generation=job.lease_generation,
                )
            except RuntimeError:
                current = self.queue.get(job.project, job.run_id)
                if current is None or current.status not in {
                    "cancelled",
                    "completed",
                    "failed",
                }:
                    raise
        finally:
            stopped.set()
            heartbeat.join(timeout=1)
            run_lock.release()
        return True

    def run(self, stop_event: threading.Event) -> None:
        self.queue.reconcile()
        while not stop_event.is_set():
            if not self.run_once():
                stop_event.wait(self.poll_seconds)
