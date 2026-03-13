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

- At `prepare`, use `prepare-topic-chat` if enabled.
- At `draft`, use `article-from-chat`.
- At `tts`, call the configured TTS script with the approved draft text.
- At `visual_plan`, call the visual planning script with the final subtitle file and write `drafts/visual-plan.json`.
- At `visual_assets`, call the configured asset-building script with `drafts/visual-plan.json` and write `runs/asset-manifest.json`.
- Stop for confirmation at any stage whose config flag is true.
- Do not invent final video assembly behavior yet. Keep that as a documented placeholder.

## Output

State the next stage, required input, and expected artifact.
Do not rewrite the article skill, visual planning skill, or TTS internals here.
