# VideoCreator AGENTS.md

## Purpose

This repository now serves two closely related purposes:

1. it remains a local-first short-video production pipeline
2. it is the integration workspace for extracting reusable capabilities from:
   - `E:\Projects\AIGC\ChaosMuseum`
   - `E:\Projects\Experiment\remotion-demo`

Do not modify the two source projects during the integration design phase.
Use this repository to stabilize reusable contracts, skills, and orchestration boundaries first.

## Source Of Truth

Use these locations consistently:

- `skills/` stores project-owned skills and reusable capability definitions
- `scripts/` stores executable workflow and utility scripts only
- `docs/` stores external API docs and reference material only
- `plans/` stores roadmap, split decisions, and architecture notes
- `config/` stores public config examples plus local-only ignored config files
- `library/` stores global style and voice resources
- `projects/<project>/` stores generated artifacts and project-local overrides

Do not place new skill-like prompts in `docs/`.
If a prompt governs agent behavior or a reusable workflow step, it belongs in `skills/`.

## Current Workflow

The currently implemented pipeline is still:

1. topic discussion and collection
2. article generation from the conversation
3. voice generation through Volcengine TTS
4. subtitle alignment using original text plus Whisper timestamps
5. visual planning from subtitle segments
6. asset collection or generation per segment
7. final video assembly reserved only, not implemented yet

Preserve that workflow unless the user explicitly requests a redesign.

## Integration Direction

The long-term architecture should separate:

1. shared infrastructure
2. scenario-specific writing and visual strategy
3. orchestration / composition

Preferred build order:

1. define reusable skill boundaries
2. define input/output contracts
3. implement shared subtitle / TTS / packaging infrastructure
4. implement scenario-level writing and visual planners
5. implement orchestration

Do not silently merge everything into one giant workflow skill.

## Output Layout

Generated artifacts and project overrides must use this structure:

- `projects/<project>/project.json`
- `projects/<project>/runs/`
- `projects/<project>/assets/`
- `projects/<project>/audio/`
- `projects/<project>/drafts/`
- `projects/<project>/sessions/`

Rules:

- `runs/` stores per-run state, manifest, and resumable workflow records
- `assets/` stores project visual materials only, including searched or generated images and videos
- `audio/` stores final audio and final subtitle outputs
- `drafts/` stores article drafts and approved article files
- `sessions/` stores conversation records and preparation notes

## Config Rules

Sensitive values such as API keys, access tokens, and machine-specific paths must stay in local config files and must not be committed.

- committed examples belong in `config/*.example.json` or existing script-level example config files
- local real values belong in `config/*.local.json` or ignored script-level `.config.json` files

If a future script requires credentials, read local config first and treat example config as documentation only.

## Workflow Rules

### Topic chat

- The prepare/chat behavior is defined by `skills/prepare-topic-chat/SKILL.md`
- The chat phase should gather useful angles, examples, definitions, disputes, and hooks for later writing

### Article generation

- The article-writing behavior is defined by `skills/article-from-chat/SKILL.md`
- Default style alignment is driven by `library/style/default`
- If a project provides `projects/<project>/library/style`, that project-local library overrides the global default

### TTS generation

- Long article TTS must be synthesized in chunks, not as a single full-text request
- TTS is responsible for audio generation only
- Do not treat Volcengine subtitle events as the final subtitle source for long-form outputs

### Subtitle generation

- Final subtitles must use the original approved article text as the subtitle text source
- Whisper is used for timestamps only
- If Whisper text differs from the article text, keep the article text and use Whisper timing
- The expected final subtitle artifact is the normal `.srt` beside the final audio
- Burned-in subtitles must always render as exactly one visual line; automatic or explicit line wrapping is not allowed
- Split long captions at semantic boundaries before rendering; the renderer may shrink text only as a final safeguard

### Visual planning

- The visual-planning behavior is defined by `skills/segment-visual-planner/SKILL.md`
- The planning input is the final `.srt` file, not a rough text draft
- The planning output is `drafts/visual-plan.json`
- The plan must be structured and machine-readable, not prose

### Visual assets

- Asset lookup must try reusable online material first when the plan says `search_first`
- If no usable online material is found, fallback to Jimeng image/video generation
- Asset files must be saved under `projects/<project>/assets/`
- `runs/asset-manifest.json` is the source of truth for resolved assets

## Quality Bar

Every reusable skill should answer:

- what exact problem it solves
- what input it expects
- what output it must produce
- what it explicitly does not own

If those four points are blurry, the skill boundary is still wrong.

## Current Non-Goals

These are intentionally not implemented yet:

- final video timeline assembly
- automatic compositor selection
- silently replacing the original working pipelines
- creating one universal mega skill before the contracts are stable
