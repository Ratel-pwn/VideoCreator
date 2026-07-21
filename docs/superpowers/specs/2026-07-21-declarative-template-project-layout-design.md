# Declarative Templates And Project Layout Design

**Status:** Approved for implementation  
**Date:** 2026-07-21  
**Scope:** VideoCreator repository, existing scenario skills, project artifact layout, and the local `资本主义潘多拉魔盒` project

## 1. Objective

VideoCreator remains a local-first production pipeline whose core provides only reusable capabilities: source acquisition, transcript extraction, reference-based writing, subtitle generation, narration generation, visual planning, asset collection, and final assembly. Scenario decisions such as writing voice, shot density, subtitle presentation, editorial framing, and explanatory animation policy move into named declarative templates.

This change also makes every production run self-contained. Drafts, narration, subtitles, visual plans, manifests, render inputs, reports, and final media belong to one immutable run instead of being mixed in project-level `drafts/` and `audio/` directories.

## 2. Non-Negotiable Decisions

1. Templates are declarative. A template may contain JSON configuration, Markdown instructions, and reusable library resources, but no Python, TypeScript, JavaScript, Remotion components, or shell scripts.
2. The core owns executable capabilities. Templates select and configure those capabilities; they do not implement new engines.
3. Every existing scenario family becomes a template: `chaos-museum`, `product-intro`, `science-explainer`, and `ai-daily`.
4. Scenario-specific skills left outside templates after migration are deleted. Shared `video-core`, workflow controller, and workflow orchestrator capabilities remain.
5. A project must declare `template_id`. Projects without a valid template cannot start or resume generation.
6. Library lookup is resource-type based and uses complete override: project library, then template library, then global library. An empty directory does not override a populated lower level.
7. The completed `资本主义潘多拉魔盒` project is migrated explicitly to `chaos-museum`, preserving its two historical runs.
8. `蚱蜢：游戏、生命与乌托邦` is a new, unproduced project and is not modified in this iteration.
9. The tracked `projects/sample-project` skeleton is removed. `project init` is the supported way to create projects.

## 3. Ownership Boundaries

### Core

Core code owns stable mechanics and contracts:

- template discovery, validation, and snapshotting
- project and run directory creation
- source selection and immutable project media references
- LLM/TTS/Whisper invocation
- subtitle segmentation and validation
- visual-plan schema and deterministic pacing audit
- online asset collection, attribution, and manifest validation
- Remotion render-input generation and final assembly
- resumable state and run manifests

Core code must not choose a scenario's editorial voice, default shot rhythm, frame branding, caption style, or preferred explainer language.

### Template

Each template owns:

- preparation instructions
- writing instructions
- visual-planning instructions
- pacing thresholds
- subtitle policy
- composition policy
- template-level style and voice libraries

The four required template directories are:

```text
templates/                              # Declarative scenario templates
├── chaos-museum/                       # 通职者“混乱博物馆”叙事模板
├── product-intro/                      # 产品介绍模板
├── science-explainer/                  # 科普解释模板
└── ai-daily/                           # AI 日报模板
```

Each template has this exact contract:

```text
<template>/                             # One complete scenario declaration
├── template.json                       # Identity, version, capabilities, and relative paths
├── prepare.md                          # Topic preparation behavior
├── writing.md                          # Script-writing behavior
├── visual-planning.md                  # Shot and visual assignment behavior
├── pacing.json                         # Shot-duration and density constraints
├── subtitle.json                       # Caption segmentation and rendering constraints
├── composition.json                    # Frame, branding, and scene presentation policy
└── library/                            # Template-level complete overrides
    ├── style/                          # Writing references for this template
    └── voice/                          # Voice references for this template
```

`template.json` declares only repository-relative files inside its own template directory. Path traversal, missing required files, unknown capability names, duplicate IDs, and executable source extensions are validation errors.

## 4. Project And Run Layout

```text
projects/<project>/                     # One long-lived video project
├── project.json                        # Project identity, template_id, and project settings
├── sources/                            # Imported source documents and source metadata
├── library/                            # Project-level complete library overrides
│   ├── style/                          # Project-specific writing references
│   └── voice/                          # Project-specific voice references
├── media/                              # Immutable reusable visual media
│   ├── images/                         # Downloaded or generated still images
│   └── videos/                         # Downloaded or generated silent video clips
└── runs/<run-id>/                      # One reproducible production attempt
    ├── state.json                      # Resumable stage state
    ├── manifest.json                   # Run identity, lineage, and artifact index
    ├── inputs/                         # Frozen effective inputs
    │   ├── template.snapshot.json      # Template identity, configuration, prompt hashes
    │   ├── project.snapshot.json       # Project configuration used by the run
    │   ├── source-selection.json       # Selected source files and hashes
    │   └── library.snapshot.json       # Effective library level, files, and hashes
    ├── session/                        # Topic preparation and conversation record
    │   ├── prepare.md                  # Prepared angles and questions
    │   ├── conversation.md             # Human-readable conversation
    │   └── conversation.json           # Structured conversation
    ├── writing/                        # Script evolution
    │   ├── draft.raw.md                # First generated draft
    │   └── script.approved.md          # Approved source of truth for narration text
    ├── audio/                          # Run-specific audio only
    │   ├── narration.generated.mp3     # Raw TTS result
    │   └── narration.render.mp3        # Cleaned render narration
    ├── subtitles/                      # Run-specific subtitle outputs
    │   ├── subtitles.aligned.srt       # Article text aligned to speech timestamps
    │   ├── subtitles.aligned.json      # Structured alignment data
    │   └── subtitles.render.srt        # Caption policy applied for rendering
    ├── visual/                         # Visual planning and resolved-asset contracts
    │   ├── visual-plan.json             # Machine-readable shot plan
    │   ├── visual-plan-audit.json       # Deterministic pacing/schema audit
    │   ├── asset-request.json           # Search/generation requests
    │   ├── asset-manifest.json          # Resolved assets, sources, and attribution
    │   └── asset-audit.json             # Media and rights validation
    ├── render/                          # Final compositor boundary
    │   ├── render-input.json            # Frozen Remotion input
    │   ├── render-report.json           # Render metadata and verification
    │   ├── render.log                   # Renderer output
    │   └── final.mp4                    # Final video
    └── review/                          # Review frames and human QA notes
```

Project-level `sources/`, `library/`, and `media/` are reusable inputs. Every generated or derived output belongs to a run. Media paths in manifests are project-relative and must remain inside the project root.

## 5. Configuration And Selection

`project.json` minimally contains:

```json
{
  "schema_version": 2,
  "name": "资本主义潘多拉魔盒",
  "template_id": "chaos-museum",
  "title": "资本主义的潘多拉魔盒是如何开启的？",
  "publication_date": "2026.07.21"
}
```

The CLI exposes:

```text
python main.py templates
python main.py project init --template chaos-museum --name "新项目"
python main.py chat --project "新项目"
python main.py import-chat input.md --project "新项目"
python main.py resume projects/<project>/runs/<run-id>
```

`templates` lists only valid templates. `project init` rejects unknown templates and existing non-empty project directories. `chat`, `import-chat`, and `resume` reject missing or invalid `template_id` before making external API calls.

## 6. Library Resolution

Library resolution is performed independently for each resource type (`style`, `voice`):

1. use `projects/<project>/library/<type>/` when it contains at least one regular file
2. otherwise use `templates/<template>/library/<type>/` when it contains at least one regular file
3. otherwise use `library/<type>/default/` when it contains at least one regular file
4. otherwise resolve to an empty selection and let the owning capability apply its explicit no-reference behavior

Files from different levels are never merged for one resource type. The run records the selected level and SHA-256 of every selected file in `inputs/library.snapshot.json`.

## 7. Reproducibility And Authority Chain

The authority chain is:

```text
sources -> session -> approved script -> generated narration -> aligned subtitles
        -> visual plan -> asset manifest -> render input -> final video
```

A run snapshots all effective declarative inputs before generation. A later template edit must not silently alter a resumed historical run. Resume reads the run snapshot; starting a new run reads the current template.

The run manifest stores schema version, project, template ID/version, run ID, creation time, stage statuses, and project-relative artifact paths. Artifacts must not be discovered by ambiguous globbing when a manifest entry exists.

## 8. Template Behavior

### chaos-museum

- Uses the approved editorial frame with left title, right fixed publication date, and bottom-left `@通职者Ratel`.
- Captions are single-line and omit sentence-final punctuation.
- Uses hard cuts for ordinary material changes.
- Opens with video when usable footage exists, then mixes images and muted public video at irregular intervals.
- Uses blurred material as background for entity cards and explainers.
- Supports strict objects and broad entities, with primary and optional secondary names.
- Uses declarative explainer scenes for formulas, processes, enumerations, functions, score fragments, code, and quoted passages with animated emphasis.
- Requires online source URL, attribution, and muted original audio for public footage.
- Enforces target shot duration 3500-6500 ms, soft maximum 8000 ms, hard maximum 10000 ms, maximum two subtitle blocks, maximum 48 Chinese characters, and at least nine shots per minute. A long hold requires an explicit reason.

### product-intro

- Structures writing around problem, evidence, demonstrated capability, constraint, and call to action.
- Favors product footage, UI demonstrations, entity cards, and concise evidence callouts.
- Does not inherit chaos-museum branding or editorial frame.

### science-explainer

- Structures writing around observable question, model, mechanism, example, limitation, and conclusion.
- Favors diagrams, formulas, processes, comparisons, and highlighted source passages.
- Does not inherit chaos-museum branding or editorial frame.

### ai-daily

- Structures writing around dated developments, verified facts, practical impact, uncertainty, and source attribution.
- Favors fast evidence-led scene changes, product footage, screenshots, timelines, and short explainers.
- Does not inherit chaos-museum branding or editorial frame.

## 9. Visual Planning And Audit

The planner consumes final render subtitles plus the selected template. It emits visual-plan schema v2. A shot may use searched image, searched video, entity card, or declarative explainer presentation. Video source audio is always disabled.

The audit is deterministic and runs before asset collection. It verifies continuous timing, allowed presentation modes, required entity/explainer fields, subtitle policy, shot duration, subtitle-block count, character count, minimum shots per minute, and explicit long-hold reasons. Violations are written to `visual/visual-plan-audit.json` and block asset collection.

This separates two concerns: deterministic segmentation/density enforcement and model-assisted visual choice. The model may decide what best explains a segment, but it may not bypass pacing limits.

## 10. Existing Project Migration

The migration command operates on an explicit project path and template ID, produces a migration report, validates every copied file hash, then removes only mapped legacy artifacts. It never scans or changes sibling projects.

For `资本主义潘多拉魔盒`:

- set `template_id` to `chaos-museum`
- move reusable visual assets to `media/images/` and `media/videos/`
- map the v1 visual plan and first final video to run `20260720-remotion-final`
- map the v2 mixed-media plan and second final video to run `20260721-mixed-media-final`
- copy the approved script, narration, aligned subtitles, effective project/template/library snapshots, and source hashes into each historical run
- move each run's manifests, audits, render inputs, reports, logs, final video, and review artifacts into the new run subdirectories
- rewrite manifest paths to the new project-relative media and run-relative artifact locations
- verify file hashes and media decode after migration

The migration explicitly excludes `projects/蚱蜢：游戏、生命与乌托邦`.

## 11. Failure Rules

- Invalid template: stop before run creation or API access.
- Missing project template: stop with a migration/init instruction.
- Snapshot mismatch on resume: stop unless the referenced snapshot itself is intact; current template differences are informational only.
- Path outside template/project root: reject.
- Visual pacing or schema audit error: block assets and render.
- Missing attribution for searched public media: block render audit.
- Existing project directory during init: never overwrite.
- Migration hash mismatch: stop and retain both source and destination; do not clean legacy files.

## 12. Acceptance Criteria

1. Four valid declarative templates are discoverable and contain no executable code.
2. No scenario-specific writing or visual strategy remains under `skills/`.
3. Project initialization and template selection work through the CLI.
4. Complete-override library resolution is tested at project, template, global, and empty levels.
5. New runs write every artifact to the documented run layout and snapshot effective inputs.
6. Visual-plan audit enforces the chaos-museum density and subtitle principles before rendering.
7. The capital project is migrated to `chaos-museum`, both historical videos remain independently addressable, and references resolve.
8. The grasshopper project is byte-for-byte unchanged.
9. Existing core unit tests, new template/layout/migration tests, Remotion tests, TypeScript checks, and applicable render-contract integration tests pass.
10. README, AGENTS, workflow configuration, and examples describe the new architecture without stale old-layout instructions.
