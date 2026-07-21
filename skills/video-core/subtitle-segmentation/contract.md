# Subtitle Segmentation Contract

## Problem

Convert aligned narration timing into display-ready subtitle segments that stay readable across short-video aspect ratios.

## Inputs

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `video_id` | string | yes | Stable per-video identifier. |
| `artifact_type` | string | yes | Must be `aligned_segments_request`. |
| `aspect_ratio` | string | yes | One of `3:4`, `9:16`, `4:3`, `16:9`. |
| `aligned_segments` | array | yes | Timing spans from `tts-align-srt`. |
| `style.max_chars_per_line` | number | yes | Width heuristic before wrap. |
| `style.min_duration_ms` | number | yes | Lower bound after splitting. |
| `style.max_duration_ms` | number | no | Optional readability ceiling. |
| `language` | string | no | Helps future language-specific splitting. |

## Outputs

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `video_id` | string | yes | Mirrors input. |
| `artifact_type` | string | yes | `segmented_captions`. |
| `segments` | array | yes | Final subtitle segments with timing. |
| `report.rule_hits` | array | yes | Records which rules triggered split decisions. |
| `report.wrap_risks_remaining` | array | yes | Must be empty for pass state. |

## Validation Rules

- Exactly one rendered line is required for every supported ratio.
- Split before visual wrap whenever a semantic break is available.
- Reject embedded line breaks in display text.
- Allow render-time font shrinking only as a final no-wrap safeguard.
- Remove trailing punctuation from display text.
- Avoid one-word or tiny tail wraps by splitting into separate entries.
- Reject outputs that create ultra-short flash segments.

## Failure Conditions

- any segment lacks a valid start/end timestamp
- split would violate `min_duration_ms`
- unsupported ratio with no fallback width rule
- output still contains embedded line breaks or wrap-risk segments

## Dependencies

- upstream: `tts-align-srt`
- downstream: `subtitle-layout-audit`, render-time caption burn-in
