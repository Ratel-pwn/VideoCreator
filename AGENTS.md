# VideoCreator AGENTS.md

## Purpose

VideoCreator is a local-first production pipeline and the integration workspace for reusable video capabilities. Do not modify external source projects while working here.

## Source Of Truth

```text
templates/                              # Scenario-owned declarative policy and resources
skills/                                 # Shared capability contracts and orchestration only
scripts/                                # Executable workflow and utility scripts only
videocreator/                           # Shared Python implementation and data contracts
renderer/                               # Shared Remotion implementation
docs/                                   # Architecture, plans, and external API references
config/                                 # Public examples plus ignored local configuration
library/                                # Global style and voice defaults
projects/<project>/                     # Project inputs, reusable media, and generated runs
```

Reusable prompts that choose writing or visual strategy belong in a template, not `docs/` or scenario-specific skill directories. Templates may not contain executable code.

## Workflow

1. Validate the project's `template_id` and snapshot the effective template and libraries.
2. Prepare the topic and retain the conversation in the run.
3. Generate and approve the script.
4. Generate chunked narration with Volcengine TTS.
5. Align original approved text to Whisper timestamps.
6. Apply template subtitle policy and generate visual-plan schema v2.
7. Run deterministic visual-plan density/schema audit.
8. Find reusable public online assets first, recording source and attribution; generation is an optional fallback.
9. Mute source video audio and assemble the final video with Remotion.

## Project Layout

```text
projects/<project>/                     # Long-lived project boundary
├── project.json                        # Schema v2 metadata and required template_id
├── sources/                            # Imported source files and source metadata
├── library/                            # Project style/voice complete overrides
├── media/                              # Project-reusable images and silent video clips
└── runs/<run-id>/                      # Immutable production attempt
    ├── state.json                      # Resume state
    ├── manifest.json                   # Artifact index and lineage
    ├── inputs/                         # Effective input snapshots
    ├── session/                        # Topic preparation and conversation
    ├── writing/                        # Raw draft and approved script
    ├── audio/                          # Generated and cleaned narration
    ├── subtitles/                      # Aligned and render subtitles
    ├── visual/                         # Plan, audit, request, manifest, and provenance
    ├── render/                         # Frozen input, log, report, and final video
    └── review/                         # Human QA material
```

All generated artifacts belong to a run. Only sources, project library overrides, and reusable media live at project level. Do not rediscover artifacts by broad glob when `manifest.json` has a path.

## Libraries

Resolve each resource type independently with complete override: populated project resource directory, then populated template resource directory, then populated global default. Do not merge levels. Empty directories do not override.

## Subtitles And Visuals

- Final subtitle text comes from the approved script; Whisper supplies timing only.
- Template subtitle policy controls segmentation and render constraints.
- Chaos Museum captions are exactly one visual line and have no sentence-final punctuation.
- Visual planning consumes final render subtitles, not rough drafts.
- Ordinary material changes use hard cuts.
- Public footage is muted and must retain source URL and attribution.
- Entity cards use a blurred material background, a fixed display image, a primary name, and an optional secondary name.
- Declarative explainers cover formulas, processes, lists, functions, scores, code, and quoted passages with animated emphasis.
- Visual-plan audit must pass before asset collection or render.

## Configuration And Security

Commit public examples as `*.example.json`. Store real keys, tokens, and machine paths only in ignored `*.local.json` or script `.config.json` files. Templates and run snapshots must never contain credentials.

## Quality Bar

Every shared capability must define its problem, exact input, exact output, and explicit non-ownership. Every template must be valid, declarative, independently selectable, and free of executable source files. Verify Python tests, Remotion tests, TypeScript checks, manifests, media decode, and sibling-project isolation before completion.
