# ChaosMuseum

ChaosMuseum is a local-first content production pipeline for short-form humanities, science, and technology videos.

## What it does

- guided topic discussion
- article generation from conversation records
- Volcengine TTS voice synthesis
- subtitle alignment using original text plus Whisper timestamps
- semantic visual planning from subtitles and draft text
- visual asset resolution through search-first and generation fallback flows

## Repository policy

This public repository intentionally excludes private reference materials and generated project outputs.

Included:
- source code
- project skills
- workflow configuration
- docs for API integration
- one sample project structure under `projects/sample-project`
- empty global library structure under `library/`

Excluded:
- real style library files
- real voice source files
- generated project outputs under `projects/` except the sample project template

## Directory layout

- `skills/`: project-owned skills
- `scripts/`: workflow and utility scripts
- `docs/`: external API docs and reference material
- `plans/`: roadmap and future decisions
- `library/`: global style and voice directory skeleton only
- `projects/`: project template plus local/generated project work

## License

This project is licensed under GPL-3.0. If you distribute a modified version or a derivative work, it must also remain open under the GPL terms.

## Config files

The repository only tracks example configuration files.
Real local configs should stay untracked.

Tracked examples:
- `scripts/jimeng_visual.config.example.json`
- `scripts/volc_tts_ws.config.example.json`
- `scripts/whisper_batch_transcribe.config.example.json`
- `scripts/yt_batch_download.config.example.json`

Before running the scripts locally, copy the example file you need to its `.config.json` counterpart and fill in your own values or environment variable names.
