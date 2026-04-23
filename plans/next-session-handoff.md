# Next Session Handoff

## What This Project Is

`VideoCreator` is a new integration workspace.

Do not modify either source project during the initial buildout:

- `E:\Projects\AIGC\ChaosMuseum`
- `E:\Projects\Experiment\remotion-demo`

Use them as references for extraction only.

## Immediate Goal

Turn the existing two scenario-specific systems into a reusable skill library plus a thin orchestration layer.

The target is not one giant workflow.
The target is a modular library that can support:

- science explainer videos
- product intro videos
- AI daily / news recap videos

## What To Implement First

Implement the first batch of high-value reusable skills before touching scenario expansion:

1. `subtitle-segmentation`
2. `subtitle-layout-audit`
3. `tts-cache-guard`
4. `tts-align-srt`
5. `visual-audit`
6. `project-packager`

These form the minimum reusable infrastructure.

## Why This Order

- subtitle behavior is already a recurring failure point and must be standardized first
- paid cloned voice must be guarded before more orchestration is added
- subtitle timing must come from aligned output rather than ad hoc scene timing
- visual audit must become a reusable gate instead of a one-off correction step
- packaging rules must be stable before more video scenarios are generated

## Required Behavior For Phase 1

### Subtitle Segmentation

Must enforce:

- single-line preference under every supported aspect ratio
- split by semantic clause before visual wrap
- no trailing punctuation at subtitle end
- no orphaned single word or tiny tail clause on a new line
- if one short word would wrap alone, split into two subtitle entries instead
- avoid ultra-short flashes caused by over-segmentation

Expected outputs:

- a reusable skill contract
- any helper script or script stub needed by the skill
- explicit validation checklist

### Subtitle Layout Audit

Must enforce:

- subtitles sit near the lower third safe area, not hugging the bottom edge
- font size is large enough for mobile short-video reading
- ratio-aware layout checks for `3:4`, `9:16`, `4:3`, and `16:9`
- rendered subtitle preview must be checked before final render

Expected outputs:

- reusable layout rules
- clear failure conditions

## Required Behavior For Phase 2

### TTS Cache Guard

Must enforce:

- reuse existing generated voice whenever possible
- cloned paid voice should not regenerate unless script materially changes
- hash or equivalent fingerprinting should be part of the guard
- voice asset provenance should be recorded

### TTS Align SRT

Must enforce:

- aligned subtitle output becomes the authoritative timing source
- later burn-in subtitles must consume this timing source rather than guessing durations
- SRT or equivalent timing file must be created during the voice pipeline, not after the final render

## Required Behavior For Phase 3

### Visual Audit

Must enforce:

- pre-render scene audit, not only post-render review
- every scene is audited, not just the opening scene
- screenshot cleanliness checks
- lazy-loaded or partially rendered captures are rejected
- footage / screenshot content must match the spoken point of the segment
- text must not collide with busy image areas and become unreadable

### Project Packager

Must enforce:

- final deliverables live in a per-video project folder
- after final user confirmation, disposable intermediate versions are removed
- source assets are preserved
- final metadata and deliverable references are updated

## Deliverable Shape Expected In This Repository

By the end of the first implementation cycle, this repo should contain:

- real reusable skill definitions
- any scripts needed to support them
- a documented contract for each skill
- a small orchestration layer that can call the reusable skills

It is acceptable for scenario-specific writing and visual planning to remain thin at first.
It is not acceptable for subtitle, TTS, audit, and packaging logic to remain embedded in one-off scenario workflows.

## Non-Goals For The First Pass

Do not start by:

- rebuilding the full UI
- making a giant end-to-end mega skill
- fully merging both source projects
- optimizing for every future video type before core contracts stabilize

## Acceptance Criteria For The First Milestone

The first milestone is complete only if:

1. subtitle processing rules are reusable outside the current Remotion intro workflow
2. cloned voice regeneration is prevented by default
3. aligned subtitle timing exists before render and drives later subtitle display
4. visual audit is defined as a reusable gate
5. packaging and cleanup rules are reusable and documented
6. no changes were required in the source projects to prove the design

## Recommended First Read Order Next Session

1. `E:\Projects\AIGC\VideoCreator\README.md`
2. `E:\Projects\AIGC\VideoCreator\plans\skill-library-split.md`
3. `E:\Projects\AIGC\VideoCreator\plans\implementation-roadmap.md`
4. `E:\Projects\AIGC\VideoCreator\plans\next-session-handoff.md`

Then begin implementation in `skills\video-core\`.
