---
name: workflow-controller
description: Coordinate the topic-to-script-to-voice-to-visual workflow. Use when Codex needs to decide which stage comes next, which sub-skill to call, whether to wait for user confirmation, and when to hand off to TTS, visual planning, or asset-building scripts.
---

# Workflow Controller

This skill manages the high-level sequence only.

## Stages

- prepare
- chat
- draft
- draft_confirm
- tts
- tts_confirm
- subtitle_sync
- visual_plan
- visual_plan_confirm
- visual_assets
- visual_assets_confirm
- bgm
- video_render
- video_render_confirm
- done

## Rules

- Before `prepare`, validate the project's `template_id` and load the run template snapshot.
- At `prepare`, use the selected template's `prepare.md`.
- At `draft`, use the selected template's `writing.md`.
- At `tts`, call the configured TTS script with the approved draft text.
- At `subtitle_sync`, audit the exact narration and SRT hashes. Apply only diagnosis-specific bounded repairs, and block the workflow if the resulting audit does not pass.
- At `visual_plan`, use the template visual, pacing, and subtitle declarations and write run `visual/` artifacts.
- At `visual_assets`, resolve reusable project `media/` and write manifests under run `visual/`.
- At `bgm`, require a passing narration/subtitle audit, resolve local then online candidates, and write an audited final mix or an explicit narration-only fallback.
- At `video_render`, freeze `render/render-input.json` and assemble `render/final.mp4` with Remotion.
- Before `video_render`, require fresh passing subtitle-sync and BGM audio reports; pass exactly one hash-verified authoritative audio path to Remotion.
- Localized TTS repair may reuse the configured trained speaker ID, but must never retrain or create a voice.
- Stop for confirmation at any stage whose config flag is true.
- Scenario strategy belongs only to declarative templates.

## Output

State the next stage, required input, and expected artifact.
Do not rewrite the article skill, visual planning skill, or TTS internals here.
