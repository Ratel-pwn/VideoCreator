# Subtitle Layout Audit Contract

## Problem

Check whether planned or rendered subtitle placement remains readable inside ratio-specific safe areas.

## Inputs

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `video_id` | string | yes | Stable per-video identifier. |
| `artifact_type` | string | yes | `subtitle_layout_audit_request`. |
| `aspect_ratio` | string | yes | `3:4`, `9:16`, `4:3`, or `16:9`. |
| `subtitle_box` | object | yes | Frame-relative bounds for subtitle region. |
| `font_size_px` | number | yes | Evaluated against ratio floor. |
| `safe_area` | object | yes | Reserved subtitle-safe region. |
| `ui_blocks` | array | no | Optional blocks subtitles must not intersect. |
| `preview_frames` | array | yes | Preview frame paths or ids to inspect. |

## Outputs

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `status` | string | yes | `pass` or `fail`. |
| `findings` | array | yes | Ratio-aware layout findings. |
| `required_actions` | array | yes | Concrete layout changes. |
| `checked_inputs` | array | yes | Traceability for audited previews. |

## Validation Rules

- Subtitles should sit near the lower third safe area, not the bottom edge.
- Mobile readability is the default baseline.
- Ratio-specific checks must run for all supported aspect ratios.
- Preview review is required before final render approval.

## Failure Conditions

- safe-area data missing
- preview frames missing
- subtitle box intersects UI or falls outside safe zone
- font size below configured threshold

## Dependencies

- upstream: `subtitle-segmentation`
- downstream: Remotion or other renderer layout tuning
