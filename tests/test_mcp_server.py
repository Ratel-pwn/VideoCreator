import asyncio
import json

from videocreator.mcp_server import create_mcp_server


class FakeService:
    def list_templates(self): return [{"id": "demo"}]
    def list_projects(self): return []
    def initialize_project(self, **kwargs): return kwargs
    def start_workflow(self, **kwargs): return {**kwargs, "status": "queued"}
    def list_workflows(self, **kwargs): return []
    def get_workflow_status(self, **kwargs): return kwargs
    def submit_workflow_input(self, **kwargs): return {**kwargs, "status": "queued"}
    def resume_workflow(self, **kwargs): return {**kwargs, "status": "queued"}
    def cancel_workflow(self, **kwargs): return {**kwargs, "status": "cancelled"}
    def get_workflow_result(self, **kwargs): return kwargs


def test_mcp_server_exposes_exact_high_level_tool_contract():
    server = create_mcp_server(FakeService())
    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == {
        "list_templates", "list_projects", "initialize_project", "start_workflow",
        "list_workflows", "get_workflow_status", "submit_workflow_input",
        "resume_workflow", "cancel_workflow", "get_workflow_result",
    }


def test_mcp_tool_delegates_and_returns_structured_data():
    server = create_mcp_server(FakeService())
    result = asyncio.run(server.call_tool("list_templates", {}))

    assert json.loads(result[0].text)["result"] == [{"id": "demo"}]
