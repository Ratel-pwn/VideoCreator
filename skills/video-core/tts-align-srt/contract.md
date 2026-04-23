# TTS Align SRT Contract

## Problem

Create the authoritative subtitle timing source from approved narration and final audio before video rendering.

## Inputs

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `video_id` | string | yes | Stable per-video identifier. |
| `artifact_type` | string | yes | `tts_align_request`. |
| `final_audio_path` | string | yes | The actual audio used by the render. |
| `approved_text` | string | yes | The narration approved upstream. |
| `alignment_engine` | object | yes | Provider and config for alignment. |
| `provisional_srt_path` | string | no | Optional seed timing. |

## Outputs

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `aligned_srt_path` | string | yes | Canonical exchange subtitle file. |
| `timing_json_path` | string | yes | JSON mirror for downstream parsing. |
| `report` | object | yes | Coverage, drift, and low-confidence notes. |

## Validation Rules

- Alignment is run against the final audio, not a provisional render proxy.
- The resulting SRT becomes the canonical timing source.
- Downstream caption burn-in must consume this output instead of guessed durations.

## Failure Conditions

- final audio missing
- approved text missing
- alignment coverage incomplete
- output paths not written

## Dependencies

- upstream: `tts-cache-guard`
- downstream: `subtitle-segmentation`
