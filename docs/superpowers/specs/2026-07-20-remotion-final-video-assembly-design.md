# Remotion Final Video Assembly Design

## Status

Approved for implementation planning on 2026-07-20.

## Context

VideoCreator can currently produce or import narration drafts, voice audio, aligned SRT subtitles, visual plans, and partially resolved visual assets. It cannot yet turn those artifacts into a finished video.

The repository also has functionalization gaps that directly affect final assembly:

- `main.py` combines configuration, workflow state, provider calls, confirmation prompts, and stage execution in one file.
- visual planning and visual asset stages exist as functions but are missing from resume dispatch.
- older projects can contain valid artifacts without `runs/<run-id>/state.json` and `manifest.json`.
- there is no automated test suite or declared Python/Node test entrypoint.
- visual asset resolution assumes provider API fallback and has no stable contract for AI-curated web materials.

The first target is the local project `projects/资本主义潘多拉魔盒`. The two source repositories remain references only and must not be modified.

## Goals

1. Add a reusable Remotion-based final video renderer.
2. Make the minimum workflow refactor required for stable stage execution and resume.
3. Support AI-curated web materials without requiring image or video generation APIs.
4. Enforce source provenance and asset completeness before rendering.
5. Render the target project as a 1920x1080, 25fps H.264 video with burned-in subtitles.
6. Use hard cuts between materials and restrained motion on still images.
7. Preserve original audio, subtitle, draft, and visual-plan artifacts when corrected derivatives are produced.

## Non-Goals

- no automatic generative image or video calls in the target project workflow
- no transition effects between scenes beyond hard cuts
- no timeline editor UI
- no vertical-video output in this milestone
- no cloud or serverless rendering
- no full rewrite of the existing topic, article, TTS, or subtitle pipelines
- no automatic legal determination of whether an online asset is safe to publish

Generation provider adapters may remain in the repository, but they are disabled by default and are not part of the target project's execution path.

## Chosen Architecture

The system is split into a Python orchestration layer and an independent Remotion renderer.

```text
visual-plan.json
        |
        v
asset-request.json
        |
        v
AI web research and local download
        |
        v
asset-manifest.json
        |
        v
Python validation and timeline normalization
        |
        v
render-input.json
        |
        v
Remotion composition and local render
        |
        v
final.mp4 + render-report.json
```

Python owns workflow state, artifact validation, legacy-project import, timeline normalization, and invoking the renderer. Remotion owns frame-level visual composition, local media playback, image motion, subtitles, audio placement, and MP4 rendering.

## Repository Structure

The implementation will introduce the following boundaries:

```text
videocreator/
  config.py
  models.py
  project_import.py
  workflow_state.py
  asset_manifest.py
  render_contract.py

renderer/
  package.json
  tsconfig.json
  remotion.config.ts
  src/
    index.ts
    Root.tsx
    VideoComposition.tsx
    schema.ts
    timeline.ts
    components/
      Scene.tsx
      StillScene.tsx
      VideoScene.tsx
      SubtitleTrack.tsx
  scripts/
    render.mjs
  tests/

scripts/
  create_asset_request.py
  audit_asset_manifest.py
  import_legacy_project.py
  render_video.py

tests/
  fixtures/
  test_project_import.py
  test_asset_manifest.py
  test_render_contract.py
  test_workflow_resume.py
```

`main.py` remains the public Python entrypoint, but delegates reusable behavior to `videocreator/` modules. Provider-specific scripts remain in `scripts/`.

## Functionalization Changes

### Workflow state

The workflow gains these explicit stages:

- `visual_plan`
- `visual_plan_confirm`
- `visual_assets`
- `visual_assets_confirm`
- `video_render`
- `video_render_confirm`
- `done`

Every defined stage must have a corresponding resume handler. Unknown stages fail with a diagnostic that includes the state path and valid stage names.

### Legacy project import

`import_legacy_project.py` creates a new run directory without moving or rewriting existing artifacts. It discovers one approved draft, one audio file, one SRT file, and one visual plan, then registers their absolute paths in a new manifest.

Import fails when an artifact role is ambiguous. The operator must choose explicitly if more than one candidate exists.

### Configuration

`workflow.config.json` remains the committed non-secret workflow configuration. The renderer section contains no credentials:

```json
{
  "renderer": {
    "engine": "remotion",
    "project_dir": "renderer",
    "composition_id": "NarratedVideo",
    "width": 1920,
    "height": 1080,
    "fps": 25,
    "codec": "h264",
    "generation_enabled": false
  }
}
```

Provider keys remain in ignored local config files or environment variables. Rendering and web-curated asset validation require no provider API keys.

## Web-Curated Asset Workflow

### Asset request

`create_asset_request.py` converts each visual-plan scene into an explicit request. Existing `generate_only` strategies are normalized to `web_curated` for this milestone.

Each request contains:

- scene id and timing
- narration text and visual brief
- preferred asset type
- search phrases
- semantic acceptance criteria
- visual rejection criteria
- minimum technical quality
- whether historical evidence or atmospheric illustration is required

The request does not contain an automatic downloader instruction. An AI agent browses the web, selects an appropriate source, downloads the file into the project asset directory, and records the result.

### Asset manifest

Every non-subtitle scene must have one manifest record with:

```json
{
  "scene_id": "scene-001",
  "asset_type": "image",
  "local_path": "assets/scene-001-opening.jpg",
  "source_page_url": "https://example.org/source-page",
  "direct_download_url": "https://example.org/media.jpg",
  "provider": "Example Archive",
  "license": "Public domain",
  "credit": "Example Archive",
  "retrieved_at": "2026-07-20T12:00:00+08:00",
  "duration_ms": null,
  "fit_mode": "cover",
  "trim_start_ms": 0,
  "short_video_policy": "reject",
  "review_status": "approved"
}
```

`source_page_url` and `license` are mandatory publication provenance fields. The system records the operator's statement; it does not claim to provide legal advice.

### Asset audit

The audit gate verifies:

- every non-`subtitle_only` scene has exactly one approved record
- every local path resolves inside the current project directory
- media files exist and are readable
- video dimensions are at least 1280x720
- video duration and trim settings satisfy the scene policy
- image dimensions are recorded; a long edge below 1280 pixels produces a warning
- source page URL, provider, license, and retrieval timestamp are present
- asset type matches the actual decoded media
- duplicate local files and repeated source URLs are reported

Warnings require explicit approval in the manifest. Errors block render-input generation.

## Render Contract

`render-input.json` is the only contract consumed by Remotion. It contains no provider details or workflow state.

Top-level fields:

- `videoId`
- `width`
- `height`
- `fps`
- `durationInFrames`
- `audioPath`
- `subtitlePath`
- `backgroundColor`
- `scenes`

Each scene contains:

- `id`
- `fromFrame`
- `durationInFrames`
- `assetType`: `image`, `video`, or `subtitle_only`
- `assetPath`
- `fitMode`
- `trimBeforeFrames`
- `shortVideoPolicy`
- `motionPreset`

All paths are relative to the project directory used as Remotion's public media root.

## Timeline Normalization

The normalized timeline is continuous and has no overlaps.

Rules:

1. Scene start and end milliseconds are converted to frames using the configured 25fps rate.
2. The first scene starts at frame zero.
3. Small gaps between visual-plan scenes are absorbed by extending the previous scene to the next scene start.
4. Overlapping scenes are rejected.
5. The last scene ends at the cleaned narration duration, not at trailing encoder silence.
6. Scene durations are at least one frame.
7. Hard cuts occur because adjacent sequences do not overlap and no transition component is inserted.

For `资本主义潘多拉魔盒`, a cleaned audio derivative is produced because the source MP3 has about 50.7 seconds of trailing silence. The source MP3 remains untouched. The last subtitle and final scene are shortened to the detected spoken-audio boundary after manual verification.

## Remotion Composition

### Metadata

The composition accepts validated input props. `calculateMetadata()` derives the exact duration, width, height, and fps from the render contract.

### Scene rendering

Scenes are rendered with adjacent `Sequence` components.

- images use `Img` with `object-fit: cover`
- still-image motion scales from approximately 1.00 to 1.05 across the scene
- horizontal translation alternates direction to reduce repetition
- videos use the current `Video` component from `@remotion/media`
- videos are muted because narration is the authoritative audio track
- videos use explicit trim settings from the manifest
- `subtitle_only` scenes use a restrained dark background or an explicitly configured continuation of the previous visual

No crossfade, dissolve, slide, or other transition is used.

### Audio

The cleaned narration audio starts at frame zero and is the only required audio track. Source video audio is always muted.

### Subtitles

The renderer uses `parseSrt()` from `@remotion/captions`. Captions are mapped to frame intervals without word-level animation.

Default style:

- one line whenever practical
- large white semibold text
- dark outline and shadow
- horizontally centered
- positioned inside the lower safe area, not against the bottom edge
- bounded width to avoid edge collisions

Subtitle layout is verified on representative frames before the final render is accepted.

## Render Invocation

The Python `render_video.py` script performs this sequence:

1. load workflow and run manifests
2. validate required artifacts
3. validate the asset manifest
4. normalize timeline and write `render-input.json`
5. invoke the renderer's Node script
6. run `ffprobe` on the output
7. write `render-report.json`
8. register the final video and report in the run manifest
9. move the workflow to `video_render_confirm`

The Node render script bundles the Remotion entrypoint with the current project directory as the public media root, selects the composition with input props, and calls `renderMedia()` locally.

## Error Handling

Rendering is rejected when:

- audio, SRT, visual plan, or asset manifest is missing
- render JSON does not match its schema
- a required scene has no approved local asset
- a media file cannot be decoded
- source provenance fields are missing
- scene frames overlap or total duration is invalid
- a short video has no explicit loop, freeze, or reject policy
- Remotion or FFmpeg exits unsuccessfully
- the output does not contain a valid H.264 video stream and audio stream

On renderer failure, the system preserves:

- `render-input.json`
- Remotion bundle/cache
- command and environment summary without secrets
- renderer logs
- partial output path if one exists

This allows retrying only the final stage.

## Render Report

The report records:

- render status
- composition id
- input manifest paths
- output path
- width, height, fps, codec, and duration from `ffprobe`
- scene count
- subtitle count
- asset warnings and approvals
- render start and finish timestamps
- renderer command version information

## Testing Strategy

### Python unit tests

- import a legacy project without changing source artifacts
- reject ambiguous legacy artifacts
- validate complete and incomplete asset manifests
- reject paths escaping the project directory
- normalize timing gaps and reject overlaps
- derive the last scene boundary from cleaned narration duration
- resume every declared workflow stage

### TypeScript unit tests

- validate render props schema
- convert milliseconds to frames consistently at 25fps
- create adjacent hard-cut sequences with no overlap
- apply image motion presets deterministically
- apply video trim and short-video policies
- map parsed SRT captions to frame intervals

### Integration test

A committed three-scene fixture contains one image, one short video, one subtitle-only scene, narration audio, and SRT captions. The test renders a short MP4 and uses `ffprobe` to assert:

- 1920x1080 output
- 25fps video stream
- H.264 codec
- audio stream present
- duration within one frame of the contract

### Visual smoke test

Representative frames are rendered from the opening, middle, and closing scenes. Review checks:

- image crop and motion remain acceptable
- video crop fills the frame
- hard cuts occur on intended boundaries
- captions remain in the safe area
- no missing-media or browser error frame appears

## Target Project Rollout

The target video proceeds in this order:

1. import existing artifacts into a new run manifest
2. create cleaned audio and corrected SRT derivatives
3. regenerate synchronized visual-plan timing without changing approved narration
4. generate 18 asset requests
5. source and approve 17 external assets; one scene remains subtitle-only
6. pass automated and semantic asset audits
7. generate render input
8. render representative frames and review subtitle/crop layout
9. render the complete MP4
10. verify the output and register final deliverables

## Acceptance Criteria

The milestone is complete when:

1. a legacy project can be imported into resumable workflow state
2. every declared workflow stage can be resumed
3. web-curated assets are represented with local paths and provenance
4. missing or invalid assets block rendering with actionable errors
5. generation APIs remain disabled and unused for the target video
6. Remotion renders image, video, and subtitle-only scenes with hard cuts
7. the target output is 1920x1080, 25fps, H.264, with narration and burned-in subtitles
8. the original project artifacts remain preserved
9. automated tests and the short render fixture pass
10. `render-report.json` documents the final output and verification results
