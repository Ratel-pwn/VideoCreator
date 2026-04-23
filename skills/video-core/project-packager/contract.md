# Project Packager Contract

## Problem

Package approved outputs into one stable project folder while preserving source assets and cleaning disposable intermediates only after approval.

## Inputs

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `video_id` | string | yes | Stable per-video identifier. |
| `artifact_type` | string | yes | `package_request`. |
| `project_root` | string | yes | Base workspace folder for packaging. |
| `deliverables` | array | yes | Final outputs such as video, captions, cover, metadata. |
| `source_assets` | array | yes | Assets that must never be removed. |
| `intermediate_assets` | array | yes | Candidates for cleanup after approval. |
| `cleanup_approved` | boolean | yes | Explicit operator confirmation. |

## Outputs

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `package_path` | string | yes | Final per-video folder. |
| `manifest_path` | string | yes | Updated manifest for packaged outputs. |
| `cleanup_report` | object | yes | Removed vs preserved files. |

## Validation Rules

- Every final deliverable must live in one per-video package folder.
- Source assets remain preserved.
- Cleanup runs only when explicitly approved.
- Manifest references must point at packaged output locations.

## Failure Conditions

- no stable `video_id`
- missing required deliverables
- cleanup set would remove preserved assets
- cleanup requested without approval

## Dependencies

- upstream: render, cover, metadata, audit approval
- downstream: publishing or archive workflows
