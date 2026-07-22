from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn

from .job_queue import JobQueue
from .mcp_server import create_mcp_server
from .runtime_config import McpRuntimeConfig
from .worker import WorkflowWorker
from .workflow_service import WorkflowService


class BearerAuthMiddleware:
    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            if headers.get(b"authorization") != f"Bearer {self.token}".encode():
                await send({
                    "type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                })
                await send({"type": "http.response.body", "body": b'{"error":"authentication_required"}'})
                return
        await self.app(scope, receive, send)


def _load(home: Path, config_path: Path) -> tuple[McpRuntimeConfig, WorkflowService]:
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    runtime = McpRuntimeConfig.from_workflow(config, home)
    queue = JobQueue(runtime.database_path)
    return runtime, WorkflowService(home, config_path, queue, runtime)


def create_http_app(runtime: McpRuntimeConfig, service: WorkflowService):
    server = create_mcp_server(service)
    server.settings.host = runtime.host
    server.settings.port = runtime.port
    server.settings.streamable_http_path = runtime.path
    server.settings.transport_security.allowed_hosts = [
        value if ":" in value else f"{value}:*" for value in runtime.allowed_hosts
    ]
    app = server.streamable_http_app()
    if runtime.auth.mode == "bearer":
        app = BearerAuthMiddleware(app, runtime.auth.token or "")
    elif runtime.host not in {"127.0.0.1", "localhost", "::1"}:
        logging.warning("MCP is listening on a non-loopback address without authentication")
    return app


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return pid > 0
    except (OSError, SystemError):
        return False


def service_status(runtime: McpRuntimeConfig) -> dict[str, Any]:
    path = runtime.runtime_dir / "service.json"
    default_url = f"http://{runtime.host}:{runtime.port}{runtime.path}"
    if not path.is_file():
        return {"status": "stopped", "url": default_url}
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "stopped", "url": default_url, "error": "invalid service metadata"}
    return {**info, "status": "running" if _pid_alive(int(info.get("pid", 0))) else "stopped"}


def _worker_entry(home: str, config_path: str, stop_path: str) -> None:
    _, service = _load(Path(home), Path(config_path))
    worker = WorkflowWorker(service)
    stop = Path(stop_path)
    service.queue.reconcile()
    while not stop.exists():
        if not worker.run_once():
            time.sleep(0.5)


def serve(home: Path, config_path: Path) -> int:
    runtime, service = _load(home, config_path)
    runtime.runtime_dir.mkdir(parents=True, exist_ok=True)
    stop_path = runtime.runtime_dir / "stop.request"
    stop_path.unlink(missing_ok=True)
    worker = multiprocessing.Process(
        target=_worker_entry,
        args=(str(home), str(config_path), str(stop_path)),
        name="videocreator-worker",
    )
    worker.start()
    info = {
        "status": "running", "pid": os.getpid(), "worker_pid": worker.pid,
        "url": f"http://{runtime.host}:{runtime.port}{runtime.path}", "started_at": time.time(),
    }
    metadata = runtime.runtime_dir / "service.json"
    metadata.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    server = uvicorn.Server(uvicorn.Config(
        create_http_app(runtime, service), host=runtime.host, port=runtime.port, log_level="info"
    ))

    def monitor() -> None:
        while not stop_path.exists() and not server.should_exit:
            time.sleep(0.25)
        if stop_path.exists():
            server.should_exit = True

    threading.Thread(target=monitor, daemon=True).start()
    try:
        server.run()
    finally:
        stop_path.touch(exist_ok=True)
        worker.join(timeout=runtime.shutdown_grace_seconds)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=5)
        info.update(status="stopped", stopped_at=time.time())
        metadata.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def start_service(home: Path, config_path: Path) -> dict[str, Any]:
    runtime, _ = _load(home, config_path)
    current = service_status(runtime)
    if current["status"] == "running":
        return current
    runtime.runtime_dir.mkdir(parents=True, exist_ok=True)
    log_dir = runtime.runtime_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = (log_dir / "service.log").open("a", encoding="utf-8")
    command = [
        sys.executable, "-m", "videocreator.mcp_runtime", "serve",
        "--home", str(home), "--config", str(config_path),
    ]
    kwargs: dict[str, Any] = {
        "cwd": str(home), "stdout": log, "stderr": log, "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)
    deadline = time.time() + 10
    while time.time() < deadline:
        status = service_status(runtime)
        if status["status"] == "running":
            return status
        time.sleep(0.1)
    raise RuntimeError("MCP service did not start within 10 seconds")


def stop_service(runtime: McpRuntimeConfig) -> dict[str, Any]:
    current = service_status(runtime)
    if current["status"] != "running":
        return current
    runtime.runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime.runtime_dir / "stop.request").touch()
    deadline = time.time() + runtime.shutdown_grace_seconds + 5
    while time.time() < deadline:
        status = service_status(runtime)
        if status["status"] == "stopped":
            return status
        time.sleep(0.2)
    return {**service_status(runtime), "status": "stopping"}


def read_logs(runtime: McpRuntimeConfig, lines: int = 100) -> str:
    path = runtime.runtime_dir / "logs" / "service.log"
    if not path.is_file():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("serve",))
    parser.add_argument("--home", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    return serve(Path(args.home).resolve(), Path(args.config).resolve())


if __name__ == "__main__":
    raise SystemExit(_main())
