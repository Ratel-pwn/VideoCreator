from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from .workflow_service import ServiceError, WorkflowService


READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
MUTATING = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)


def create_mcp_server(service: WorkflowService) -> FastMCP:
    server = FastMCP("VideoCreator", stateless_http=True, json_response=True)

    def invoke(method, *args, **kwargs):
        try:
            return {"result": method(*args, **kwargs)}
        except ServiceError as exc:
            raise ToolError(json.dumps(exc.as_dict(), ensure_ascii=False)) from exc

    @server.tool(annotations=READ_ONLY)
    def list_templates() -> dict:
        """List available declarative video templates."""
        return invoke(service.list_templates)

    @server.tool(annotations=READ_ONLY)
    def list_projects() -> dict:
        """List initialized VideoCreator projects and latest workflow state."""
        return invoke(service.list_projects)

    @server.tool(annotations=MUTATING)
    def initialize_project(
        name: str,
        template_id: str,
        title: str | None = None,
        publication_date: str | None = None,
    ) -> dict:
        """Initialize a video project with a declarative template."""
        return invoke(service.initialize_project, name, template_id, title, publication_date)

    @server.tool(annotations=MUTATING)
    def start_workflow(
        project: str,
        topic: str,
        context: str | None = None,
        run_id: str | None = None,
    ) -> dict:
        """Create and asynchronously enqueue a new video workflow run."""
        return invoke(service.start_workflow, project, topic, context, run_id)

    @server.tool(annotations=READ_ONLY)
    def list_workflows(project: str, status: str | None = None) -> dict:
        """List workflow runs for a project."""
        return invoke(service.list_workflows, project, status)

    @server.tool(annotations=READ_ONLY)
    def get_workflow_status(project: str, run_id: str) -> dict:
        """Get normalized status, stage, errors, and pending input for a run."""
        return invoke(service.get_workflow_status, project, run_id)

    @server.tool(annotations=MUTATING)
    def submit_workflow_input(
        project: str,
        run_id: str,
        interaction_id: str,
        response: str,
    ) -> dict:
        """Answer the current workflow interaction and requeue the run."""
        return invoke(service.submit_workflow_input, project, run_id, interaction_id, response)

    @server.tool(annotations=MUTATING)
    def resume_workflow(project: str, run_id: str) -> dict:
        """Resume a failed workflow from its durable current stage."""
        return invoke(service.resume_workflow, project, run_id)

    @server.tool(annotations=DESTRUCTIVE)
    def cancel_workflow(project: str, run_id: str) -> dict:
        """Request cancellation at the next workflow-safe boundary."""
        return invoke(service.cancel_workflow, project, run_id)

    @server.tool(annotations=READ_ONLY)
    def get_workflow_result(
        project: str,
        run_id: str,
        include_text: list[str] | None = None,
    ) -> dict:
        """Get artifact metadata and optionally selected text artifact content."""
        return invoke(service.get_workflow_result, project, run_id, include_text or [])

    return server

