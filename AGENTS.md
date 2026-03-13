# ChaosMuseum AGENTS.md

## Purpose

This repository is a content production pipeline for short-form humanities, science, and technology videos.
The current implemented workflow is:

1. topic discussion and collection
2. article generation from the conversation
3. voice generation through Volcengine TTS
4. subtitle alignment using original text plus Whisper timestamps
5. visual planning from subtitle segments
6. asset collection or generation per segment
7. final video assembly reserved only, not implemented yet

When changing this project, preserve that workflow unless the user explicitly requests a redesign.

## Source Of Truth

Use these locations consistently:

- `skills/` stores project-owned skills only
- `scripts/` stores executable workflow and utility scripts only
- `docs/` stores external API docs and reference material only
- `plans/` stores roadmap, pending decisions, and future implementation notes
- `library/` stores global style and voice resources
- `projects/<project>/` stores generated artifacts and project-local overrides

Do not place new skill-like prompts in `docs/`.
If a prompt governs agent behavior or a reusable workflow step, it belongs in `skills/`.

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

Do not create flat global folders like `projects/audio` or `projects/drafts` at the repository root again.
Do not store the global voice source inside the global style library.

## Workflow Rules

### Topic chat

- The prepare/chat behavior is defined by `skills/prepare-topic-chat/SKILL.md`
- Do not reintroduce a separate chat system prompt in `docs/`
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
- Do not keep extra intermediate subtitle files unless the user explicitly asks for them
- The expected final subtitle artifact is the normal `.srt` beside the final audio

### Visual planning

- The visual-planning behavior is defined by `skills/segment-visual-planner/SKILL.md`
- The planning input is the final `.srt` file, not a rough text draft
- The planning output is `drafts/visual-plan.json`
- The plan must be structured and machine-readable, not prose

### Visual assets

- Asset lookup must try reusable online material first when the plan says `search_first`
- If no usable online material is found, fallback to Jimeng image/video generation
- Asset files must be saved under `projects/<project>/assets/`
- Asset file names must use `timestamp + visual brief`
- `runs/asset-manifest.json` is the source of truth for resolved assets

## Maintenance Rules

- Prefer improving Python scripts over adding manual multi-step instructions
- If a new repeated workflow appears, first consider whether it belongs in `skills/` or `scripts/`
- If a new provider or API is introduced, store the provider documentation in `docs/` first, then wire scripts against it
- If a new major stage is planned but not implemented, record it in `plans/workflow-roadmap.md`
- Keep `main.py` as the workflow orchestrator; avoid scattering orchestration logic across many unrelated files

## Iteration Rules

When extending the pipeline:

- update the relevant skill if the change affects agent behavior or generation logic
- update the relevant script if the change affects executable processing
- update `workflow.config.json` if the change introduces new configurable behavior
- update `plans/workflow-roadmap.md` if the change opens a new future stage or unresolved design decision

## Current Non-Goals

These are intentionally not implemented yet:

- final video timeline assembly
- automatic compositor selection
- unifying all image/video search providers into one abstraction

Do not implement those silently. Record the decision path first in `plans/workflow-roadmap.md` and only then add code.

## Practical Defaults

- Default article input for voice is the approved draft
- Default subtitle output is a single final `.srt`
- Default visual planning input is the final subtitle file
- Default visual planning output is `drafts/visual-plan.json`
- Default asset resolution output is `runs/asset-manifest.json`
- Default global style library is `library/style/default`
- Default global voice source is `library/voice/default/voice.mp3`
- Project-local overrides live in `projects/<project>/project.json` and `projects/<project>/library/`
- Default project directory name should be derived from the topic and normalized for reuse
- Prefer keeping final artifacts and deleting only disposable debug intermediates
