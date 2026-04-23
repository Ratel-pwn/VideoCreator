# Visual Audit Contract

## Problem

Audit every planned or rendered scene for screenshot cleanliness, source correctness, and overlay readability before a video is approved.

## Inputs

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `video_id` | string | yes | Stable per-video identifier. |
| `artifact_type` | string | yes | `visual_audit_request`. |
| `scenes` | array | yes | One record per scene. |
| `frames` | array | yes | Representative frame captures. |
| `source_evidence` | array | yes | Screenshots, docs, or URLs used for claims. |
| `narration_map` | array | yes | Spoken point attached to each scene id. |

## Outputs

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `status` | string | yes | `pass` or `fail`. |
| `scene_findings` | array | yes | Findings for every scene. |
| `required_actions` | array | yes | Blocking revisions. |
| `gate_summary` | object | yes | Separate pre-render and post-render gate result. |

## Validation Rules

- Every scene is audited.
- Lazy-loaded, partially rendered, or placeholder captures are rejected.
- Visual evidence must match the spoken point of the scene.
- Overlay text must remain readable against the background.

## Failure Conditions

- scene has no representative frame
- evidence does not support spoken point
- screenshot still shows placeholder / loading state
- busy image area makes overlay unreadable

## Dependencies

- upstream: scene planning and render preview generation
- downstream: approval gate before packaging
