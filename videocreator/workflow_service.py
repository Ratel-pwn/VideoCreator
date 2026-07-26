from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .interactions import interaction_fingerprint, validate_interaction_response
from .durable_io import atomic_write_json
from .execution_fence import RunMutationLock
from .job_queue import JobCancelledError, JobQueue
from .project_layout import initialize_project
from .run_identity import resolve_run_dir, validate_run_id
from .runtime_config import McpRuntimeConfig
from .templates import discover_templates


TEXT_ARTIFACT_SUFFIXES = {".json", ".jsonl", ".md", ".srt", ".txt"}
PUBLIC_RESULT_ARTIFACTS = frozenset(
    {
        "draft_approved",
        "voice_audio",
        "voice_subtitle",
        "subtitle_sync_audit",
        "visual_plan",
        "visual_plan_audit",
        "asset_manifest",
        "bgm_selection",
        "bgm_mix_report",
        "final_mix",
        "voice_audio_cleaned",
        "voice_subtitle_cleaned",
        "final_video",
        "render_report",
    }
)
PUBLIC_ARTIFACT_PATTERNS = {
    "draft_approved": (r"writing/script\.approved\.md",),
    "voice_audio": (
        r"audio/voice\.(?:mp3|wav|m4a|aac|flac|ogg)",
        r"audio/narration\.generated\.(?:mp3|wav|m4a|aac|flac|ogg)",
        r"audio/narration\.imported\.(?:mp3|wav|m4a|aac|flac|ogg)",
    ),
    "voice_subtitle": (
        r"audio/voice\.srt",
        r"subtitles/subtitles\.aligned\.srt",
        r"subtitles/subtitles\.imported\.srt",
    ),
    "subtitle_sync_audit": (r"review/subtitle-sync-audit\.json",),
    "visual_plan": (r"visual/visual-plan\.json",),
    "visual_plan_audit": (r"visual/visual-plan-audit\.json",),
    "asset_manifest": (r"visual/asset-manifest\.json",),
    "bgm_selection": (r"audio/bgm-selection\.json",),
    "bgm_mix_report": (r"audio/bgm-mix-report\.json",),
    "final_mix": (r"audio/final-mix\.wav",),
    "voice_audio_cleaned": (
        r"audio/final-mix\.wav",
        r"audio/voice\.(?:mp3|wav|m4a|aac|flac|ogg)",
        r"audio/narration\.imported\.(?:mp3|wav|m4a|aac|flac|ogg)",
        r"audio/narration\.render\.(?:mp3|wav|m4a|aac|flac|ogg)",
    ),
    "voice_subtitle_cleaned": (
        r"audio/voice\.srt",
        r"subtitles/subtitles\.imported\.srt",
        r"subtitles/subtitles\.render\.srt",
    ),
    "final_video": (r"render/final\.mp4",),
    "render_report": (r"render/render-report\.json",),
}


class ServiceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class WorkflowService:
    def __init__(
        self,
        home: Path,
        config_path: Path,
        queue: JobQueue,
        runtime: McpRuntimeConfig,
    ):
        self.home = Path(home).resolve()
        self.config_path = Path(config_path).resolve()
        self.config = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        self.queue = queue
        self.runtime = runtime
        self.templates_root = self._resolve(self.config.get("templates", {}).get("root", "templates"))
        self.projects_root = self._resolve(self.config.get("projects", {}).get("root", "projects"))
        self.reconcile_workflow_outbox()

    def _resolve(self, value: str) -> Path:
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else (self.home / path).resolve()

    def _project(self, name: str, require: bool = True) -> Path:
        project = (self.projects_root / name).resolve()
        try:
            project.relative_to(self.projects_root)
        except ValueError:
            raise ServiceError("invalid_argument", f"Project must stay inside projects root: {name}") from None
        if require and not (project / "project.json").is_file():
            raise ServiceError("not_found", f"Project not found: {name}")
        return project

    def _run(self, project: str, run_id: str) -> Path:
        try:
            path = resolve_run_dir(self._project(project), run_id)
        except ValueError as exc:
            raise ServiceError("invalid_argument", str(exc)) from exc
        if not (path / "state.json").is_file():
            raise ServiceError("not_found", f"Run not found: {project}/{run_id}")
        return path

    def reconcile_workflow_outbox(self) -> int:
        if not self.projects_root.is_dir():
            return 0
        recovered = 0
        for project_dir in sorted(self.projects_root.iterdir()):
            if not (project_dir / "project.json").is_file():
                continue
            runs_root = project_dir / "runs"
            if not runs_root.is_dir():
                continue
            for run_dir in sorted(runs_root.iterdir()):
                state_path = run_dir / "state.json"
                if not run_dir.is_dir() or not state_path.is_file():
                    continue
                run_lock = RunMutationLock(run_dir / ".worker.lock")
                if not run_lock.acquire():
                    continue
                try:
                    try:
                        state = json.loads(
                            state_path.read_text(encoding="utf-8-sig")
                        )
                    except (OSError, json.JSONDecodeError):
                        continue
                    if (
                        state.get("execution_owner") != "mcp"
                        or state.get("status")
                        in {"completed", "cancelled", "failed"}
                    ):
                        continue
                    run_id = str(state.get("run_id", ""))
                    try:
                        expected_run = resolve_run_dir(
                            project_dir,
                            run_id,
                        )
                    except ValueError:
                        continue
                    if expected_run != run_dir.resolve():
                        continue
                    outbox = state.get("queue_outbox")
                    if not isinstance(outbox, dict):
                        continue
                    action = str(outbox.get("action", "advance"))
                    try:
                        generation = int(outbox.get("generation", 1))
                    except (TypeError, ValueError):
                        continue
                    job = self.queue.get(project_dir.name, run_id)
                    if job is None:
                        job = self.queue.enqueue(
                            project_dir.name,
                            run_id,
                        )
                        recovered += 1
                    elif (
                        action == "resume"
                        and outbox.get("status") == "pending"
                        and job.status == "failed"
                    ):
                        try:
                            job = self.queue.resume_failed(
                                project_dir.name,
                                run_id,
                            )
                            recovered += 1
                        except ValueError:
                            job = self.queue.get(
                                project_dir.name,
                                run_id,
                            )
                            if job is None or job.status not in {
                                "queued",
                                "leased",
                                "waiting",
                            }:
                                raise
                    latest = json.loads(
                        state_path.read_text(encoding="utf-8-sig")
                    )
                    latest_outbox = latest.get("queue_outbox")
                    if not isinstance(latest_outbox, dict):
                        continue
                    try:
                        latest_generation = int(
                            latest_outbox.get("generation", 1)
                        )
                    except (TypeError, ValueError):
                        continue
                    if (
                        latest_generation != generation
                        or str(latest_outbox.get("action", "advance"))
                        != action
                    ):
                        continue
                    current_job = self.queue.get(
                        project_dir.name,
                        run_id,
                    )
                    if current_job is None or current_job.id != job.id:
                        continue
                    latest_outbox.update(
                        {
                            "schema_version": 1,
                            "generation": generation,
                            "action": current_job.action,
                            "status": "dispatched",
                            "job_id": current_job.id,
                            "job_lease_generation": (
                                current_job.lease_generation
                            ),
                        }
                    )
                    atomic_write_json(state_path, latest)
                finally:
                    run_lock.release()
        return recovered

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ServiceError("service_unavailable", f"Invalid runtime file: {path.name}") from exc

    @staticmethod
    def _public_artifact_path(
        run: Path,
        key: str,
        raw_path: Any,
    ) -> Path | None:
        patterns = PUBLIC_ARTIFACT_PATTERNS.get(key)
        if patterns is None:
            return None
        try:
            path = Path(raw_path).resolve()
            relative = path.relative_to(run.resolve())
        except (OSError, TypeError, ValueError):
            return None
        relative_posix = relative.as_posix()
        if not any(re.fullmatch(pattern, relative_posix) for pattern in patterns):
            return None
        return path

    def list_templates(self) -> list[dict[str, Any]]:
        return [
            {
                "id": item.id,
                "display_name": item.raw.get("display_name", item.id),
                "description": item.raw.get("description", ""),
                "version": item.version,
            }
            for item in discover_templates(self.templates_root).values()
        ]

    def list_projects(self) -> list[dict[str, Any]]:
        if not self.projects_root.is_dir():
            return []
        values = []
        for path in sorted(self.projects_root.iterdir()):
            if not (path / "project.json").is_file():
                continue
            project = self._read_json(path / "project.json")
            runs = self.list_workflows(path.name)
            values.append({
                "name": project.get("name", path.name),
                "template_id": project.get("template_id"),
                "title": project.get("title"),
                "publication_date": project.get("publication_date"),
                "latest_run": runs[0] if runs else None,
            })
        return values

    def initialize_project(
        self,
        name: str,
        template_id: str,
        title: str | None = None,
        publication_date: str | None = None,
    ) -> dict[str, Any]:
        templates = discover_templates(self.templates_root)
        template = templates.get(template_id)
        if template is None:
            raise ServiceError("not_found", f"Template not found: {template_id}")
        project_path = self._project(name, require=False)
        if project_path.exists():
            raise ServiceError("state_conflict", f"Project already exists: {name}")
        metadata = {key: value for key, value in {
            "title": title, "publication_date": publication_date,
        }.items() if value}
        initialize_project(self.projects_root, name, template, **metadata)
        return {"name": name, "template_id": template_id, **metadata, "path": str(project_path)}

    def start_workflow(
        self,
        project: str,
        topic: str,
        context: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        if not topic.strip():
            raise ServiceError("invalid_argument", "Topic cannot be empty")
        self._project(project)
        if run_id is not None:
            try:
                validate_run_id(run_id)
            except ValueError as exc:
                raise ServiceError("invalid_argument", str(exc)) from exc
        import main as workflow

        try:
            ctx = workflow.make_run_context(
                self.home,
                self.config_path,
                "chat",
                topic.strip(),
                run_id,
                None,
                project,
                None,
                execution_owner="mcp",
                initial_context=context,
            )
        except FileExistsError as exc:
            raise ServiceError("state_conflict", str(exc)) from exc
        state_path = ctx.run_dir / "state.json"
        run_lock = RunMutationLock(ctx.run_dir / ".worker.lock")
        if not run_lock.acquire():
            raise ServiceError(
                "service_unavailable",
                "Workflow run is already being dispatched",
            )
        try:
            before = self._read_json(state_path)
            outbox = before.get("queue_outbox")
            if not isinstance(outbox, dict):
                raise ServiceError(
                    "service_unavailable",
                    "Workflow queue outbox is missing",
                )
            try:
                generation = int(outbox["generation"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ServiceError(
                    "service_unavailable",
                    "Workflow queue outbox generation is invalid",
                ) from exc
            action = str(outbox.get("action", "advance"))
            try:
                job = self.queue.enqueue(project, ctx.run_id)
            except Exception as exc:
                raise ServiceError(
                    "service_unavailable",
                    "Workflow run was created but queue dispatch failed: "
                    f"{exc}",
                ) from exc
            latest = self._read_json(state_path)
            latest_outbox = latest.get("queue_outbox")
            if not isinstance(latest_outbox, dict):
                raise ServiceError(
                    "state_conflict",
                    "Workflow queue outbox changed during dispatch",
                )
            try:
                latest_generation = int(latest_outbox["generation"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ServiceError(
                    "state_conflict",
                    "Workflow queue outbox changed during dispatch",
                ) from exc
            if (
                latest_generation != generation
                or str(latest_outbox.get("action", "advance")) != action
            ):
                raise ServiceError(
                    "state_conflict",
                    "Workflow queue outbox changed during dispatch",
                )
            latest_outbox.update(
                {
                    "status": "dispatched",
                    "job_id": job.id,
                    "job_lease_generation": job.lease_generation,
                }
            )
            atomic_write_json(state_path, latest)
            current_stage = latest.get("current_stage")
        finally:
            run_lock.release()
        return {
            "project": project,
            "run_id": ctx.run_id,
            "status": job.status,
            "current_stage": current_stage,
            "created_at": job.created_at,
        }

    def list_workflows(self, project: str, status: str | None = None) -> list[dict[str, Any]]:
        from .cli import list_runs

        values = [self.get_workflow_status(project, item.run_id) for item in list_runs(self._project(project))]
        return [item for item in values if status is None or item["status"] == status]

    def get_workflow_status(self, project: str, run_id: str) -> dict[str, Any]:
        run = self._run(project, run_id)
        state = self._read_json(run / "state.json")
        manifest = self._read_json(run / "manifest.json")
        job = self.queue.get(project, run_id)
        pending = state.get("pending_interaction")
        answered_in_outbox = bool(
            job
            and pending
            and self.queue.has_input(job.id, str(pending.get("id", "")))
        )
        if state.get("status") == "cancelled" or (job and job.status == "cancelled"):
            status = "cancelled"
        elif pending and "response" not in pending and not answered_in_outbox:
            status = "waiting_for_input"
        elif job and job.status in {"queued", "leased", "waiting", "completed", "failed", "cancelled"}:
            status = {"leased": "running", "waiting": "waiting_for_input"}.get(job.status, job.status)
        elif state.get("current_stage") == "done" or state.get("status") == "completed":
            status = "completed"
        elif state.get("status") in {"failed", "cancelled"}:
            status = state["status"]
        else:
            status = "running"
        interaction = pending if status == "waiting_for_input" else None
        return {
            "project": project,
            "run_id": run_id,
            "status": status,
            "current_stage": state.get("current_stage"),
            "interaction": interaction,
            "error": state.get("last_error") or (job.error if job else None),
            "updated_at": state.get("updated_at"),
            "artifacts": sorted(
                key
                for key, raw_path in (
                    manifest.get("artifacts") or {}
                ).items()
                if self._public_artifact_path(
                    run,
                    key,
                    raw_path,
                )
                is not None
            ),
        }

    def submit_workflow_input(
        self,
        project: str,
        run_id: str,
        interaction_id: str,
        response: str,
    ) -> dict[str, Any]:
        run = self._run(project, run_id)
        state = self._read_json(run / "state.json")
        job = self.queue.get(project, run_id)
        if state.get("status") == "cancelled" or (job and job.status == "cancelled"):
            try:
                accepted = self.queue.retry_input(
                    project,
                    run_id,
                    interaction_id,
                    response,
                )
            except KeyError:
                accepted = None
            except ValueError as exc:
                raise ServiceError("state_conflict", str(exc)) from exc
            if accepted is False:
                return {
                    "project": project,
                    "run_id": run_id,
                    "status": "cancelled",
                    "accepted": False,
                }
            raise ServiceError(
                "state_conflict",
                "Cannot submit input to a cancelled workflow",
            )
        pending = state.get("pending_interaction")
        if not pending or pending.get("id") != interaction_id:
            try:
                accepted = self.queue.retry_input(
                    project,
                    run_id,
                    interaction_id,
                    response,
                )
            except (KeyError, ValueError) as exc:
                raise ServiceError("state_conflict", str(exc)) from exc
            current = self.queue.get(project, run_id)
            return {
                "project": project,
                "run_id": run_id,
                "status": current.status,
                "accepted": accepted,
            }
        fingerprint = pending.get("fingerprint") or interaction_fingerprint(
            str(pending.get("kind", "text")),
            pending.get("payload"),
        )
        try:
            validate_interaction_response(pending, response)
            accepted = self.queue.submit_input(
                project,
                run_id,
                interaction_id,
                fingerprint,
                response,
            )
        except (JobCancelledError, KeyError, ValueError) as exc:
            raise ServiceError("state_conflict", str(exc)) from exc
        current = self.queue.get(project, run_id)
        return {
            "project": project,
            "run_id": run_id,
            "status": current.status,
            "accepted": accepted,
        }

    def resume_workflow(self, project: str, run_id: str) -> dict[str, Any]:
        status = self.get_workflow_status(project, run_id)
        if status["status"] not in {"failed"}:
            raise ServiceError("state_conflict", f"Run cannot be resumed from {status['status']}")
        run = self._run(project, run_id)
        state_path = run / "state.json"
        run_lock = RunMutationLock(run / ".worker.lock")
        if not run_lock.acquire():
            raise ServiceError(
                "state_conflict",
                "Workflow run is currently being mutated",
            )
        try:
            state = self._read_json(state_path)
            current_job = self.queue.get(project, run_id)
            if current_job is None or current_job.status != "failed":
                raise ServiceError(
                    "state_conflict",
                    "Run can no longer be resumed from failed",
                )
            state["status"] = "ready"
            state.pop("last_error", None)
            try:
                prior_generation = int(
                    (state.get("queue_outbox") or {}).get(
                        "generation",
                        0,
                    )
                )
            except (TypeError, ValueError) as exc:
                raise ServiceError(
                    "service_unavailable",
                    "Workflow queue outbox generation is invalid",
                ) from exc
            generation = prior_generation + 1
            state["queue_outbox"] = {
                "schema_version": 1,
                "generation": generation,
                "action": "resume",
                "status": "pending",
            }
            atomic_write_json(state_path, state)
            try:
                job = self.queue.resume_failed(project, run_id)
            except Exception as exc:
                current = self.queue.get(project, run_id)
                if current is None or current.status not in {
                    "queued",
                    "leased",
                    "waiting",
                }:
                    raise ServiceError(
                        "service_unavailable",
                        "Workflow resume was recorded but queue resume failed: "
                        f"{exc}",
                    ) from exc
                job = current
            latest = self._read_json(state_path)
            latest_outbox = latest.get("queue_outbox")
            if not isinstance(latest_outbox, dict):
                raise ServiceError(
                    "state_conflict",
                    "Workflow queue outbox changed during dispatch",
                )
            try:
                latest_generation = int(latest_outbox["generation"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ServiceError(
                    "state_conflict",
                    "Workflow queue outbox changed during dispatch",
                ) from exc
            if (
                latest_generation != generation
                or latest_outbox.get("action") != "resume"
            ):
                raise ServiceError(
                    "state_conflict",
                    "Workflow queue outbox changed during dispatch",
                )
            latest_outbox.update(
                {
                    "status": "dispatched",
                    "job_id": job.id,
                    "job_lease_generation": job.lease_generation,
                }
            )
            atomic_write_json(state_path, latest)
        finally:
            run_lock.release()
        return {"project": project, "run_id": run_id, "status": job.status}

    def cancel_workflow(self, project: str, run_id: str) -> dict[str, Any]:
        self._run(project, run_id)
        try:
            job = self.queue.request_cancel(project, run_id)
        except KeyError as exc:
            raise ServiceError("state_conflict", str(exc)) from exc
        if job.status == "cancelled":
            run = self._run(project, run_id)
            state = self._read_json(run / "state.json")
            state["status"] = "cancelled"
            atomic_write_json(run / "state.json", state)
        return {"project": project, "run_id": run_id, "status": job.status}

    def recover_legacy_inputs(self) -> int:
        recovered = 0
        for job in self.queue.waiting_jobs():
            try:
                state = self._read_json(self._run(job.project, job.run_id) / "state.json")
            except ServiceError:
                continue
            pending = state.get("pending_interaction")
            if not isinstance(pending, dict) or "response" not in pending:
                continue
            interaction_id = str(pending.get("id", ""))
            if not interaction_id:
                continue
            fingerprint = pending.get("fingerprint") or interaction_fingerprint(
                str(pending.get("kind", "text")),
                pending.get("payload"),
            )
            try:
                if self.queue.submit_input(
                    job.project,
                    job.run_id,
                    interaction_id,
                    str(fingerprint),
                    str(pending["response"]),
                ):
                    recovered += 1
            except (JobCancelledError, KeyError, ValueError):
                continue
        return recovered

    def get_workflow_result(
        self,
        project: str,
        run_id: str,
        include_text: Iterable[str] = (),
        max_text_bytes: int = 200_000,
    ) -> dict[str, Any]:
        run = self._run(project, run_id)
        manifest = self._read_json(run / "manifest.json")
        requested = set(include_text)
        disallowed = sorted(requested - PUBLIC_RESULT_ARTIFACTS)
        if disallowed:
            raise ServiceError(
                "invalid_argument",
                "Text artifacts are not public results: "
                + ", ".join(disallowed),
            )
        artifacts: dict[str, Any] = {}
        for key, raw_path in (manifest.get("artifacts") or {}).items():
            path = self._public_artifact_path(run, key, raw_path)
            if path is None:
                continue
            item: dict[str, Any] = {
                "path": str(path),
                "exists": path.is_file(),
                "size": path.stat().st_size if path.is_file() else None,
            }
            if self.runtime.public_base_url and path.is_file():
                relative = path.relative_to(self.projects_root).as_posix()
                item["url"] = f"{self.runtime.public_base_url}/artifacts/{relative}"
            if key in requested and path.suffix.lower() in TEXT_ARTIFACT_SUFFIXES and path.is_file():
                if path.stat().st_size > max_text_bytes:
                    raise ServiceError("invalid_argument", f"Text artifact is too large: {key}")
                item["content"] = path.read_text(encoding="utf-8-sig")
            artifacts[key] = item
        return {**self.get_workflow_status(project, run_id), "artifacts": artifacts}
