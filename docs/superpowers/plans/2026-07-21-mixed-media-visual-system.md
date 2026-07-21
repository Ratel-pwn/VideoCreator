# Mixed-Media Visual System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add v2 mixed-media scene contracts, layered entity/explainer rendering, strict video resolution, and re-render `资本主义潘多拉魔盒` with footage, stills, entity cards, and explainers.

**Architecture:** Versioned Python contracts preserve the current v1 one-asset path and add a v2 slot-based path. The normalized Remotion contract uses explicit presentation modes and focused scene components. Target project artifacts migrate fully to v2 while existing projects remain compatible.

**Tech Stack:** Python 3.12, pytest, TypeScript 5.8, React 19, Remotion 4.0.494, Zod 3, Vitest 3, FFmpeg/FFprobe 8.

## Global Constraints

- The first v2 scene is `footage` with a real video asset.
- Requested videos cannot silently resolve to images.
- Source-video audio is always muted.
- No image/video generation APIs are called.
- Entity foreground images remain fixed and use primary plus optional secondary labels.
- Explainers use blurred contextual backgrounds and deterministic frame animation.
- First templates are `flow`, `list`, `quote_highlight`, and `relation_loop`.
- Scene boundaries remain hard cuts; image/video cadence is semantic, not fixed.
- Public asset records include source, credit, retrieval date, rights status, and rights note.
- Existing subtitle and editorial-frame rules remain unchanged.

---

### Task 1: Add Versioned Slot-Based Asset Contracts

**Files:**
- Modify: `videocreator/models.py`
- Modify: `videocreator/asset_manifest.py`
- Modify: `tests/test_asset_manifest.py`
- Modify: `skills/segment-visual-planner/SKILL.md`

**Interfaces:**
- Consumes: v1 or v2 visual plan plus matching manifest.
- Produces: slot requests keyed by `<scene_id>:<role>` and strict v2 audit results.

- [ ] Add failing tests for a v2 footage request, entity-card background/display requests, exact type mismatch, missing slot, rights warning, and schema-version mismatch.
- [ ] Run `python -m pytest tests/test_asset_manifest.py -v`; expect the new tests to fail.
- [ ] Extend `AssetRecord` with optional `request_id`, `role`, `rights_status`, and `rights_note` while preserving v1 parsing.
- [ ] Dispatch `create_asset_requests()` and `audit_asset_manifest()` by `schema_version`.
- [ ] Require exact role/type matches, opening video, rights metadata, and approved review status in v2.
- [ ] Update the planner skill with the five presentation modes and no-silent-downgrade rule.
- [ ] Re-run the focused tests and commit `feat: add mixed-media asset contracts`.

### Task 2: Normalize V2 Scenes Into The Render Contract

**Files:**
- Modify: `videocreator/render_contract.py`
- Modify: `tests/test_render_contract.py`
- Modify: `renderer/src/schema.ts`
- Modify: `renderer/tests/schema.test.ts`

**Interfaces:**
- Consumes: v2 scenes plus manifest assets indexed by request ID.
- Produces: mode-aware render scenes with normalized media assets.

- [ ] Add failing Python tests for all five modes and exact required slots.
- [ ] Add failing Zod tests for all four explainer kinds and invalid mode payloads.
- [ ] Run focused Python and renderer schema tests; verify failures are caused by missing v2 support.
- [ ] Add a `normalize_v2_scenes()` path while preserving `normalize_scenes()` v1 behavior.
- [ ] Define shared media schema and mode-discriminated render schemas in TypeScript.
- [ ] Re-run focused tests and commit `feat: normalize mixed-media render scenes`.

### Task 3: Render Layered Entity Cards

**Files:**
- Create: `renderer/src/components/BlurredBackground.tsx`
- Create: `renderer/src/components/EntityCardScene.tsx`
- Create: `renderer/src/components/FullBleedScene.tsx`
- Modify: `renderer/src/components/Scene.tsx`
- Modify: `renderer/tests/components.test.tsx`

**Interfaces:**
- Consumes: `footage`, `still`, and `entity_card` render scenes.
- Produces: muted full-bleed video/still playback and fixed entity foreground cards.

- [ ] Add failing component tests for blur/scrim, fixed foreground geometry, optional secondary label, and muted video.
- [ ] Run component tests and verify red state.
- [ ] Implement the three focused components and mode dispatch.
- [ ] Re-run component tests and commit `feat: render layered entity cards`.

### Task 4: Add The Explainer Registry And Four Templates

**Files:**
- Create: `renderer/src/components/ExplainerScene.tsx`
- Create: `renderer/src/components/explainers/shared.tsx`
- Create: `renderer/src/components/explainers/FlowExplainer.tsx`
- Create: `renderer/src/components/explainers/ListExplainer.tsx`
- Create: `renderer/src/components/explainers/QuoteHighlightExplainer.tsx`
- Create: `renderer/src/components/explainers/RelationLoopExplainer.tsx`
- Modify: `renderer/src/components/Scene.tsx`
- Create: `renderer/tests/explainers.test.tsx`

**Interfaces:**
- Consumes: validated explainer configs and current scene frame.
- Produces: deterministic SVG/HTML teaching animations over blurred media.

- [ ] Add failing tests for node/edge reveal, list stagger, quote underline growth, loop direction, and unsupported kinds.
- [ ] Run explainer tests and verify red state.
- [ ] Implement each template as a pure function of frame, fps, and config; reserve the lower subtitle band.
- [ ] Add registry dispatch in `ExplainerScene` and `Scene`.
- [ ] Run all renderer tests and typecheck; commit `feat: add Remotion explainer templates`.

### Task 5: Migrate The Target Plan And Resolve Assets

**Files:**
- Replace locally: `projects/资本主义潘多拉魔盒/drafts/visual-plan.json`
- Regenerate locally: `projects/资本主义潘多拉魔盒/runs/20260720-remotion-final/asset-request.json`
- Replace locally: `projects/资本主义潘多拉魔盒/runs/20260720-remotion-final/asset-manifest.json`
- Add local assets under: `projects/资本主义潘多拉魔盒/assets/`
- Regenerate locally: `projects/资本主义潘多拉魔盒/runs/20260720-remotion-final/render-input.json`

**Interfaces:**
- Consumes: final SRT/article, existing approved images, and researched public footage.
- Produces: complete v2 plan, manifest, and render input.

- [ ] Replan the narration into explicit footage/still/entity/explainer modes; split long semantic scenes where needed.
- [ ] Require footage for opening, enclosure, industrial labor, discipline, globalization, and closing metaphor where usable footage exists.
- [ ] Reuse approved images for backgrounds and entity displays where semantically correct.
- [ ] Search and download public footage, recording exact provenance and rights notes; keep all original audio muted at render time.
- [ ] Generate and audit slot requests; resolve every required slot with no type downgrade.
- [ ] Build the mode-aware render input and verify continuous frame coverage.

### Task 6: Render And Audit The Mixed-Media Video

**Files:**
- Regenerate locally: `projects/资本主义潘多拉魔盒/runs/20260720-remotion-final/final.mp4`
- Regenerate locally: `projects/资本主义潘多拉魔盒/runs/20260720-remotion-final/render-report.json`
- Regenerate review frames under: `projects/资本主义潘多拉魔盒/runs/20260720-remotion-final/review/`

- [ ] Run `python -m pytest -q`, `npm --prefix renderer test`, and `npm --prefix renderer run typecheck`.
- [ ] Run the asset audit and require zero errors; report rights warnings.
- [ ] Render the full video through `scripts/render_video.py`.
- [ ] Fully decode with FFmpeg and verify H.264/AAC 1920x1080, 25fps, 5462 frames.
- [ ] Inspect representative frames from every scene and verify footage motion with multi-frame comparisons.
- [ ] Confirm entity foregrounds stay fixed, explainers animate, subtitles remain single-line without trailing punctuation, and frame metadata remains correct.
- [ ] Run final git status and present branch integration options.
