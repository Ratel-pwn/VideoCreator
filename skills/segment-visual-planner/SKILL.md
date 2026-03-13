---
name: segment-visual-planner
description: Plan visuals at the semantic scene level from subtitle timing plus article meaning. Use when Codex needs to group consecutive subtitle segments into coherent visual scenes before asset collection or generation.
---

# Semantic Visual Planner

## Goal

Read the final `.srt` subtitle file together with the approved article draft.
Do not plan one visual per raw subtitle block.
Instead, group consecutive subtitle segments into semantic scenes and output one structured visual strategy per scene.

The subtitle file provides timing anchors.
The article draft provides semantic continuity.

## Output file

Write a machine-readable `visual-plan.json`.
This file is a scene plan, not a raw subtitle-to-shot map.

## Core rule

A scene may cover one or more consecutive subtitle segments.
Group by meaning, not by subtitle line breaks.
Prefer shorter scenes with a single visual intention. If a line contains two clearly different semantic moves, split them into two scenes.

Good grouping signals:
- the same historical example continues across several subtitle segments
- several subtitle segments explain one mechanism or one concept
- a transition sentence introduces a new visual chapter
- a closing judgment should often become a single closing scene

Bad grouping:
- changing the visual just because the subtitle wrapped onto a new line
- forcing one asset decision for every tiny subtitle fragment
- merging two distinct semantic beats into one long scene just because they are adjacent

## Required output shape

Top-level JSON must contain:
- `topic`
- `category`
- `segment_count`
- `segments`

Each item in `segments` must contain:
- `subtitle_segment_ids`
- `brief`
- `material_type`
- `asset_strategy`
- `visual_role`
- `search_queries`
- `generation_prompts`
- `transition`
- `notes`

## Field rules

- `subtitle_segment_ids`: an ordered list of consecutive subtitle ids covered by the same scene
- `brief`: a short Chinese summary for naming and human review
- `material_type`: one of `video`, `image`, `subtitle_only`
- `asset_strategy`: one of `search_first`, `generate_only`, `subtitle_only`
- `visual_role`: one of `evidential`, `illustrative`, `abstract`, `atmospheric`
- `search_queries`: object with `image` and `video` arrays
- `generation_prompts`: object with `image` and `video` strings
- `transition`: short value such as `cut`, `dissolve`, `hold`
- `notes`: one short sentence explaining the choice

## Material Type Rules

Use `image` when the main job of the visual is to show what something is.
Typical `image` cases:
- people
- books
- documents
- maps
- institutions
- objects
- portraits
- diagrams
- historical evidence that is better inspected than watched

Use `video` when the main job of the visual is to show what is happening or how something changes.
Typical `video` cases:
- movement
- transformation
- expansion
- migration
- discipline
- circulation
- conflict
- environment change
- any mechanism that loses force if the frame stays static

Use `subtitle_only` when any external visual would weaken the line.
Typical `subtitle_only` cases:
- final judgments
- sharp closing lines
- abstract conclusions that should be carried by voice and text alone
- moments where extra footage would feel noisy, redundant, or fake

## Decision Priority

Always decide `material_type` with this order:

1. Is this scene mainly identifying an object, person, place, document, institution, or concept anchor?
If yes, prefer `image`.

2. Is this scene mainly showing a process, transition, expansion, displacement, or operational mechanism?
If yes, prefer `video`.

3. Is the main value in letting the audience inspect details?
If yes, prefer `image`.

4. Is the main value in letting the audience feel motion, pressure, change, or progression?
If yes, prefer `video`.

5. Would any external visual distract from the sentence instead of strengthening it?
If yes, use `subtitle_only`.

Do not use vague compromise logic.
If a scene could be both image and video, choose the dominant one.
Do not recreate `mixed` in any disguised form.

## Planning principles

- In normal narration, a scene should usually carry one semantic beat only
- If a sentence shifts from thesis to example, from example to mechanism, or from mechanism to judgment, split it
- Do not use `mixed` to avoid making a decision; choose the dominant asset type
- Search first for real people, places, books, documents, maps, institutions, events, and historical imagery
- Use generated assets for abstract mechanisms, invisible systems, metaphors, or scenes that are unlikely to exist as reusable footage
- Use `subtitle_only` when external visuals would weaken the line instead of strengthening it
- Keep scene order aligned with the subtitle timeline
- Cover every subtitle segment exactly once, with no overlap and no gaps

## Output rules

- Return JSON only
- Do not wrap JSON in Markdown fences
- Do not output prose before or after the JSON
- Do not return one plan item per subtitle block unless the meaning genuinely changes that often
