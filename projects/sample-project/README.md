# Sample Project

This project is the directory template for every real project.

- `project.json`: project-level overrides for style library and voice source
- `library/style/`: project-specific style corpus
- `library/voice/voice.mp3`: project-specific voice source
- `sessions/`: conversation records
- `drafts/`: article drafts, approved scripts, and `visual-plan.json`
- `audio/`: final audio and subtitles
- `assets/`: final image/video materials only
- `runs/`: per-run workflow state and manifests, including `asset-manifest.json`

If project overrides are missing, the workflow falls back to the global defaults in `library/`.
