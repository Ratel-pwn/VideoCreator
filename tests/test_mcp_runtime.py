import asyncio
from pathlib import Path

from videocreator.mcp_runtime import BearerAuthMiddleware, service_status
from videocreator.runtime_config import AuthConfig, McpRuntimeConfig


def runtime(tmp_path: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        host="127.0.0.1", port=8765, path="/mcp", public_base_url=None,
        runtime_dir=tmp_path, worker_count=1, lease_seconds=60,
        shutdown_grace_seconds=30, allowed_hosts=("localhost",),
        auth=AuthConfig("none", "TOKEN", None),
    )


def test_service_status_reports_stopped_without_metadata(tmp_path: Path):
    assert service_status(runtime(tmp_path))["status"] == "stopped"


def test_bearer_middleware_rejects_missing_token_and_accepts_valid_token():
    calls = []

    async def app(scope, receive, send):
        calls.append(scope)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = BearerAuthMiddleware(app, "secret")

    async def request(headers):
        messages = []
        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        await middleware({"type": "http", "headers": headers}, receive, send)
        return messages

    denied = asyncio.run(request([]))
    allowed = asyncio.run(request([(b"authorization", b"Bearer secret")]))

    assert denied[0]["status"] == 401
    assert allowed[0]["status"] == 204
    assert len(calls) == 1
