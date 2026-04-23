# VideoCreator

VideoCreator is a local-first content production pipeline for short-form humanities, science, and technology videos.

This repository also serves as the integration workspace for extracting reusable video-production capabilities from:

- `E:\Projects\AIGC\ChaosMuseum`
- `E:\Projects\Experiment\remotion-demo`

## What it does

- guided topic discussion
- article generation from conversation records
- Volcengine TTS voice synthesis
- subtitle alignment using original text plus Whisper timestamps
- semantic visual planning from subtitles and draft text
- visual asset resolution through search-first and generation fallback flows
- reusable skill boundary design for future multi-scenario orchestration

## Repository policy

This public repository intentionally excludes private reference materials, local secrets, and generated project outputs.

Included:
- source code
- project skills
- workflow configuration
- public config examples
- docs for API integration
- one sample project structure under `projects/sample-project`
- empty global library structure under `library/`
- integration plans under `plans/`

Excluded:
- real style library files
- real voice source files
- generated project outputs under `projects/` except the sample project template
- local config files with real API keys or machine-specific paths

## Directory layout

- `skills/`: project-owned skills and reusable capability definitions
- `scripts/`: workflow and utility scripts
- `docs/`: external API docs and reference material
- `plans/`: roadmap and integration decisions
- `config/`: public config examples and local config conventions
- `library/`: global style and voice directory skeleton only
- `projects/`: project template plus local/generated project work

## Config files

The repository only tracks example configuration files.
Real local configs should stay untracked.

Tracked examples:
- `scripts/jimeng_visual.config.example.json`
- `scripts/volc_tts_ws.config.example.json`
- `scripts/whisper_batch_transcribe.config.example.json`
- `scripts/yt_batch_download.config.example.json`
- `config/video-creator.example.json`

Before running the scripts locally, copy the example file you need to its local counterpart and fill in your own values or environment variable names.

Repository-level config convention:
- public example: `config/video-creator.example.json`
- local only: `config/video-creator.local.json`

The local file is ignored by git and must hold any real API keys or machine-specific paths.

## License

This project is licensed under GPL-3.0. If you distribute a modified version or a derivative work, it must also remain open under the GPL terms.
