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
- visual_plan
- visual_plan_confirm
- visual_assets
- visual_assets_confirm
- video_stub
- done

## Rules

- Before `prepare`, validate the project's `template_id` and load the run template snapshot.
- At `prepare`, use the selected template's `prepare.md`.
- At `draft`, use the selected template's `writing.md`.
- At `tts`, call the configured TTS script with the approved draft text.
- At `visual_plan`, use the template visual, pacing, and subtitle declarations and write run `visual/` artifacts.
- At `visual_assets`, resolve reusable project `media/` and write manifests under run `visual/`.
- At `video_render`, freeze `render/render-input.json` and assemble `render/final.mp4` with Remotion.
- Stop for confirmation at any stage whose config flag is true.
- Scenario strategy belongs only to declarative templates.

## Output

State the next stage, required input, and expected artifact.
Do not rewrite the article skill, visual planning skill, or TTS internals here.
