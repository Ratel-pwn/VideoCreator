# VideoCreator

VideoCreator is a local-first pipeline for topic preparation, reference-based writing, narration, aligned subtitles, visual planning, online asset collection, and Remotion final assembly.

## Templates

Scenario behavior is declarative and lives under `templates/`. The core contains executable capabilities only. Included templates are `chaos-museum`, `product-intro`, `science-explainer`, and `ai-daily`.

Install the Git-style command once. Editable installation keeps `vc` connected to this checkout:

```powershell
python -m pip install -e E:\Projects\AIGC\VideoCreator
```

When using pyenv-win, refresh its command shims once after installation:

```powershell
pyenv rehash
```

Then use it from any directory:

```powershell
vc templates
vc init "新项目" -t chaos-museum --title "视频标题" --date 2026.07.22
vc chat "新项目" "本期讨论主题"
vc status "新项目"
vc runs "新项目"
vc resume "新项目"
vc audit subtitles "新项目" --run <run-id>
vc repair subtitles "新项目" --run <run-id>
```

`vc init` prompts for omitted values unless `--non-interactive` is set. `vc chat` always starts a new run; `vc resume` continues the newest unfinished run, or a specific run selected with `-r RUN_ID`. Set `VIDEO_CREATOR_HOME` or pass `--home PATH` when the command should use a different checkout. `--config FILE` selects another workflow configuration, and `--json` gives machine-readable `templates`, `status`, and `runs` output.

The previous Python entry point remains compatible:

```powershell
python main.py templates
python main.py project init --template chaos-museum --name "新项目" --title "视频标题" --publication-date "2026.07.21"
python main.py chat --project "新项目" --topic "讨论主题"
python main.py import-chat input.md --project "新项目"
python main.py resume projects/<project>/runs/<run-id>
```

A project without a valid `template_id` cannot generate or resume. Templates contain JSON, Markdown, and library resources only; executable Python/TypeScript/Remotion code remains in the core.

## Layout

```text
templates/                              # Declarative writing, pacing, subtitle, and composition policy
skills/                                 # Shared executable capability contracts and orchestration guidance
scripts/                                # Workflow and migration command-line programs
videocreator/                           # Python core models, validation, and orchestration helpers
renderer/                               # Shared typed Remotion compositor
config/                                 # Public examples and ignored local configuration
library/                                # Global style and voice resources
projects/                               # Local projects and generated artifacts, ignored by default
docs/                                   # Architecture, external API references, and implementation plans
```

```text
projects/<project>/                     # One long-lived production project
├── project.json                        # Project metadata and required template_id
├── sources/                            # Imported source documents and metadata
├── library/                            # Project-level complete resource overrides
├── media/                              # Reusable immutable images and muted videos
└── runs/<run-id>/                      # One reproducible production attempt
    ├── state.json                      # Resumable stage state
    ├── manifest.json                   # Template lineage and artifact index
    ├── inputs/                         # Frozen template, project, source, and library snapshots
    ├── session/                        # Preparation and conversation records
    ├── writing/                        # Raw and approved scripts
    ├── audio/                          # Generated and render narration
    ├── subtitles/                      # Aligned and render SRT files
    ├── visual/                         # Plan, pacing audit, requests, manifests, and asset audit
    ├── render/                         # Remotion input, report, log, and final.mp4
    └── review/                         # Review frames and QA notes
```

Library selection is a complete override per resource type: populated project library, then populated template library, then populated global default. Files from levels are never merged, and empty directories do not override.

## Visual And Render Rules

The planner consumes final subtitles and emits visual-plan schema v2. Deterministic audit runs before asset lookup and checks continuity, shot density, duration, subtitle block/character limits, entity/explainer declarations, and template subtitle policy.

Online images and videos require source URLs and attribution. Source video audio is always muted. Remotion assembles 1920x1080, 25 fps H.264/AAC output with hard cuts by default. The Chaos Museum template additionally enforces one-line captions without sentence-final punctuation, mixed image/video material, entity cards, declarative explainers, and its editorial frame.

Install and verify the renderer:

```powershell
npm --prefix renderer install
npm --prefix renderer test
npm --prefix renderer run typecheck
npm --prefix renderer run studio
```

Render an already frozen input:

```powershell
python scripts/render_video.py --project-root projects/<project> --input projects/<project>/runs/<run-id>/render/render-input.json --output projects/<project>/runs/<run-id>/render/final.mp4
```

## Subtitle Synchronization Gate

TTS retains ordered segment audio and metadata in `audio/tts-segments.json`. Whisper supplies timestamps while the approved narration remains the subtitle text source. Every alignment writes evidence to `subtitles/alignment-timing.json` and `subtitles/alignment-report.json`.

Before Remotion rendering, VideoCreator requires `review/subtitle-sync-audit.json` to pass against the exact audio and SRT hashes. `vc audit subtitles` performs a read-only check. `vc repair subtitles` applies diagnosis-specific repairs, records attempts in `review/subtitle-sync-repairs.json`, and refuses to repeat the same action against unchanged inputs.

Localized TTS repair can consume provider quota. It reuses the configured trained speaker ID, regenerates at most one affected segment, and never retrains or creates a voice.

## Configuration

Only example configuration is committed. API keys, tokens, and machine-specific paths belong in ignored `*.local.json` or script `.config.json` files. Never put real credentials in an example, template, project snapshot, or run manifest.

## License

GPL-3.0.
