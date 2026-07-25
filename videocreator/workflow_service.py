from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .interactions import DurableInteractionPort
from .job_queue import JobQueue
from .project_layout import initialize_project
from .runtime_config import McpRuntimeConfig
from .templates import discover_templates


TEXT_ARTIFACT_SUFFIXES = {".json", ".jsonl", ".md", ".srt", ".txt"}


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
        path = (self._project(project) / "runs" / run_id).resolve()
        if not (path / "state.json").is_file():
            raise ServiceError("not_found", f"Run not found: {project}/{run_id}")
        return path

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ServiceError("service_unavailable", f"Invalid runtime file: {path.name}") from exc

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
        import main as workflow

        try:
            ctx = workflow.make_run_context(
                self.home, self.config_path, "chat", topic.strip(), run_id, None, project, None
            )
        except FileExistsError as exc:
            raise ServiceError("state_conflict", str(exc)) from exc
        if context:
            context_path = ctx.run_dir / "inputs" / "agent-context.md"
            context_path.write_text(context.strip() + "\n", encoding="utf-8")
            ctx.manifest.setdefault("artifacts", {})["agent_context"] = str(context_path)
            ctx.save_manifest()
        job = self.queue.enqueue(project, ctx.run_id)
        return {
            "project": project,
            "run_id": ctx.run_id,
            "status": job.status,
            "current_stage": ctx.state.get("current_stage"),
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
        if state.get("pending_interaction"):
            status = "waiting_for_input"
        elif job and job.status in {"queued", "leased", "waiting", "completed", "failed", "cancelled"}:
            status = {"leased": "running", "waiting": "waiting_for_input"}.get(job.status, job.status)
        elif state.get("current_stage") == "done" or state.get("status") == "completed":
            status = "completed"
        elif state.get("status") in {"failed", "cancelled"}:
            status = state["status"]
        else:
            status = "running"
        return {
            "project": project,
            "run_id": run_id,
            "status": status,
            "current_stage": state.get("current_stage"),
            "interaction": state.get("pending_interaction"),
            "error": state.get("last_error") or (job.error if job else None),
            "updated_at": state.get("updated_at"),
            "artifacts": sorted((manifest.get("artifacts") or {}).keys()),
        }

    def submit_workflow_input(
        self,
        project: str,
        run_id: str,
        interaction_id: str,
        response: str,
    ) -> dict[str, Any]:
        import main as workflow

        run = self._run(project, run_id)
        ctx = workflow.resume_context(self.home, self.config_path, run)
        port = DurableInteractionPort()
        try:
            accepted = port.submit(ctx, interaction_id, response)
        except ValueError as exc:
            raise ServiceError("state_conflict", str(exc)) from exc
        job = self.queue.enqueue(project, run_id)
        return {"project": project, "run_id": run_id, "status": job.status, "accepted": accepted}

    def resume_workflow(self, project: str, run_id: str) -> dict[str, Any]:
        status = self.get_workflow_status(project, run_id)
        if status["status"] not in {"failed"}:
            raise ServiceError("state_conflict", f"Run cannot be resumed from {status['status']}")
        run = self._run(project, run_id)
        state = self._read_json(run / "state.json")
        state["status"] = "ready"
        state.pop("last_error", None)
        (run / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        job = self.queue.enqueue(project, run_id)
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
            (run / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"project": project, "run_id": run_id, "status": job.status}

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
        artifacts: dict[str, Any] = {}
        for key, raw_path in (manifest.get("artifacts") or {}).items():
            path = Path(raw_path).resolve()
            try:
                path.relative_to(self.projects_root)
            except ValueError:
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
