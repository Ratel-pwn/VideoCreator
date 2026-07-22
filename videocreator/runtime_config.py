from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class AuthConfig:
    mode: str
    bearer_token_env: str
    token: str | None


@dataclass(frozen=True)
class McpRuntimeConfig:
    host: str
    port: int
    path: str
    public_base_url: str | None
    runtime_dir: Path
    worker_count: int
    lease_seconds: int
    shutdown_grace_seconds: int
    allowed_hosts: tuple[str, ...]
    auth: AuthConfig

    @property
    def database_path(self) -> Path:
        return self.runtime_dir / "mcp.sqlite3"

    @classmethod
    def from_workflow(
        cls,
        workflow: dict,
        home: Path,
        environ: Mapping[str, str] | None = None,
    ) -> "McpRuntimeConfig":
        raw = workflow.get("mcp", {})
        auth_raw = raw.get("auth", {})
        mode = str(auth_raw.get("mode", "none"))
        if mode not in {"none", "bearer"}:
            raise ValueError(f"Unsupported MCP auth mode: {mode}")
        token_env = str(auth_raw.get("bearer_token_env", "VIDEO_CREATOR_MCP_TOKEN"))
        token = (environ or os.environ).get(token_env) if mode == "bearer" else None
        if mode == "bearer" and not token:
            raise ValueError(f"MCP bearer token environment variable is not set: {token_env}")

        runtime_value = Path(str(raw.get("runtime_dir", ".runtime"))).expanduser()
        runtime_dir = runtime_value.resolve() if runtime_value.is_absolute() else (home / runtime_value).resolve()
        path = "/" + str(raw.get("path", "/mcp")).strip("/")
        port = int(raw.get("port", 8765))
        worker_count = int(raw.get("worker_count", 1))
        lease_seconds = int(raw.get("lease_seconds", 60))
        shutdown_grace_seconds = int(raw.get("shutdown_grace_seconds", 30))
        if port <= 0 or port > 65535:
            raise ValueError("MCP port must be between 1 and 65535")
        if min(worker_count, lease_seconds, shutdown_grace_seconds) <= 0:
            raise ValueError("MCP worker and timing settings must be positive")

        return cls(
            host=str(raw.get("host", "127.0.0.1")),
            port=port,
            path=path,
            public_base_url=str(raw["public_base_url"]).rstrip("/") if raw.get("public_base_url") else None,
            runtime_dir=runtime_dir,
            worker_count=worker_count,
            lease_seconds=lease_seconds,
            shutdown_grace_seconds=shutdown_grace_seconds,
            allowed_hosts=tuple(str(value) for value in raw.get("allowed_hosts", ["127.0.0.1", "localhost"])),
            auth=AuthConfig(mode=mode, bearer_token_env=token_env, token=token),
        )

