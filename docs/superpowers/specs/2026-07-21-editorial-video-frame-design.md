# Editorial Video Frame Design

## Status

Approved in conversation on 2026-07-21. Pending implementation planning.

## Context

The Remotion composition currently renders every scene edge-to-edge across the
1920x1080 canvas. The target video needs a reusable editorial container inspired
by the supplied reference without copying its bilateral labels or metadata
layout.

The container must display project-specific metadata rather than hard-coded
copy. For `资本主义潘多拉魔盒`, the values are:

- video title: `资本主义的潘多拉魔盒是如何开启的？`
- publication date: `2026.07.21`
- creator handle: `@通职者Ratel`

The question mark belongs to the video title and is not affected by the rule
that removes punctuation from burned-in subtitle endings.

## Goals

1. Add a reusable editorial frame around the complete video timeline.
2. Keep the media area visually dominant while leaving intentional white space.
3. Read title, publication date, and creator handle from project configuration.
4. Preserve hard cuts, existing scene motion, narration, subtitle timing, and
   subtitle rules.
5. Re-render and visually verify the target video.

## Non-Goals

- copying the reference image's side labels, bottom topic, or decorative copy
- adding `DAWN RATEL` to this layout
- animating the frame chrome
- changing source assets, narration, SRT timing, or scene boundaries
- introducing another aspect ratio

## Layout

The output remains 1920x1080.

- Canvas background: charcoal `#2b2b2a`.
- Editorial card: white `#f7f6f2`, spanning from y=36 to y=1044.
- Header: x=110 to x=1810, y=36 to y=120.
- Video title: left aligned at x=110, vertically centered in the header.
- Publication date: right aligned at x=1810 on the same baseline.
- Media viewport: x=110, y=120, width=1700, height=852, approximately 2:1.
- Media viewport corners: 24px radius with overflow clipped.
- Media treatment: scenes fill the viewport; `cover` scenes may crop vertically.
- Footer: x=110 to x=1810, y=972 to y=1044.
- Creator handle: left aligned at x=110 and vertically centered in the footer.

The frame is static for the entire composition. Scene hard cuts and still-image
motion happen only inside the clipped media viewport.

## Typography

- Video title: dark charcoal, 34px, semibold, Chinese-capable sans-serif.
- Publication date: dark charcoal, 26px, medium weight, expanded letter spacing.
- Creator handle: muted gray, 24px, medium weight.
- No other frame copy is rendered.

The video title is allowed to retain punctuation. Burned-in subtitles continue
to render on one line and strip all trailing punctuation.

## Configuration Contract

Project configuration gains an optional `presentation` object:

```json
{
  "presentation": {
    "frame_preset": "editorial-wide",
    "video_title": "资本主义的潘多拉魔盒是如何开启的？",
    "publication_date": "2026.07.21",
    "creator_handle": "@通职者Ratel"
  }
}
```

Python maps this into the renderer contract:

```json
{
  "frame": {
    "preset": "editorial-wide",
    "videoTitle": "资本主义的潘多拉魔盒是如何开启的？",
    "publicationDate": "2026.07.21",
    "creatorHandle": "@通职者Ratel"
  }
}
```

`frame` remains optional in `render-input.json` for compatibility with existing
projects. If absent, the renderer keeps the current full-bleed composition. If
present, every field is required and validated as a non-empty string.

## Component Boundaries

`EditorialFrame` owns only the card, header, media viewport, footer, clipping,
and configured metadata. It receives the visual timeline and subtitle track as
children.

`VideoComposition` owns timeline sequencing and audio. When `frame` is present,
it places all scene sequences and `SubtitleTrack` inside `EditorialFrame`.

`SubtitleTrack` gains viewport-relative positioning so captions remain near the
bottom of the media area. Its text normalization rules remain unchanged.

## Testing And Verification

1. Python contract tests verify project presentation data reaches render input.
2. Zod schema tests accept a complete frame and reject incomplete frame data.
3. component tests verify title, date, handle, viewport dimensions, and clipping.
4. Existing subtitle tests continue to verify one-line text and no trailing
   punctuation.
5. The Remotion integration test renders a framed fixture.
6. The target video is re-rendered, fully decoded with FFmpeg, probed for
   H.264/AAC 1920x1080 at 25fps, and inspected at representative frames.

## Acceptance Criteria

- the title shown at top left is exactly `资本主义的潘多拉魔盒是如何开启的？`
- the date shown at top right is exactly `2026.07.21`
- the footer shows only `@通职者Ratel` at bottom left
- `DAWN RATEL` and vertical side labels do not appear
- all media is clipped inside the rounded approximately 2:1 viewport
- subtitles stay inside the viewport, on one line, without trailing punctuation
- narration, timing, hard cuts, and source artifacts remain unchanged
