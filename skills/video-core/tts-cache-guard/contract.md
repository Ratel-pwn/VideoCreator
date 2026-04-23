# TTS Cache Guard Contract

## Problem

Protect paid or cloned TTS usage by reusing existing audio unless the script or voice inputs materially changed.

## Inputs

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `video_id` | string | yes | Stable per-video identifier. |
| `artifact_type` | string | yes | `tts_cache_request`. |
| `script_text` | string | yes | Approved narration. |
| `voice_profile` | object | yes | Provider, voice id, pricing tier, and mode. |
| `output_audio_path` | string | yes | Expected final audio file. |
| `existing_asset` | object | no | Existing audio + provenance metadata. |
| `force` | boolean | no | Explicit override. Defaults to `false`. |

## Outputs

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `decision` | string | yes | `reuse`, `regenerate`, or `reject`. |
| `fingerprint` | string | yes | Stable material-change fingerprint. |
| `reasoning` | array | yes | Human-readable reasons for the decision. |
| `provenance_record` | object | yes | Stored metadata for selected asset. |

## Validation Rules

- Fingerprint must include narration text and voice profile fields that affect sound or cost.
- Paid or cloned voice defaults to reuse.
- Force regeneration requires an explicit operator reason.
- Existing audio without provenance metadata is unsafe to reuse silently.

## Failure Conditions

- request has no script text
- request has no voice profile
- existing audio exists but has no provenance metadata
- force requested with no recorded reason

## Dependencies

- upstream: scenario writing skills
- downstream: `tts-align-srt`
