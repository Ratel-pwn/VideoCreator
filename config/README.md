# Config Conventions

Sensitive values such as API keys must stay in local config files and must not be committed to a public repository.

## Files

- `video-creator.example.json`
  - committed
  - documents the required config shape
- `video-creator.local.json`
  - local only
  - ignored by git
  - stores real secrets and machine-specific paths

## Rule

When a future script needs provider credentials or machine-specific paths, it should read from `config/video-creator.local.json` first and treat `config/video-creator.example.json` as documentation only.

## MCP

Public MCP listener and queue settings live in the `mcp` section of `workflow.config.json`. Bearer tokens never belong in that file. When `mcp.auth.mode` is `bearer`, the service reads the secret from the environment variable named by `mcp.auth.bearer_token_env`, which defaults to `VIDEO_CREATOR_MCP_TOKEN`.

Local and remote deployments use the same Streamable HTTP server. Remote operators are responsible for TLS, firewall policy, reverse-proxy configuration, and mapping `public_base_url` artifact URLs to approved project files.
