from pathlib import Path

import pytest

from videocreator.runtime_config import McpRuntimeConfig


def test_runtime_config_resolves_defaults_from_home(tmp_path: Path):
    config = McpRuntimeConfig.from_workflow({}, tmp_path)

    assert config.host == "127.0.0.1"
    assert config.port == 8765
    assert config.path == "/mcp"
    assert config.runtime_dir == (tmp_path / ".runtime").resolve()
    assert config.database_path == config.runtime_dir / "mcp.sqlite3"
    assert config.auth.mode == "none"


def test_runtime_config_allows_remote_bind_and_bearer_auth(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VC_TEST_TOKEN", "secret")
    config = McpRuntimeConfig.from_workflow(
        {
            "mcp": {
                "host": "0.0.0.0",
                "port": 9443,
                "path": "video/mcp",
                "public_base_url": "https://video.example.test",
                "runtime_dir": "var/runtime",
                "allowed_hosts": ["video.example.test"],
                "auth": {"mode": "bearer", "bearer_token_env": "VC_TEST_TOKEN"},
            }
        },
        tmp_path,
    )

    assert config.host == "0.0.0.0"
    assert config.port == 9443
    assert config.path == "/video/mcp"
    assert config.public_base_url == "https://video.example.test"
    assert config.runtime_dir == (tmp_path / "var/runtime").resolve()
    assert config.allowed_hosts == ("video.example.test",)
    assert config.auth.token == "secret"


def test_bearer_auth_requires_environment_secret(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MISSING_TOKEN", raising=False)

    with pytest.raises(ValueError, match="MISSING_TOKEN"):
        McpRuntimeConfig.from_workflow(
            {"mcp": {"auth": {"mode": "bearer", "bearer_token_env": "MISSING_TOKEN"}}},
            tmp_path,
        )
