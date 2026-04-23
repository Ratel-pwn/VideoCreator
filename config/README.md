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
