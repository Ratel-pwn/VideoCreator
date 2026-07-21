---
name: subtitle-segmentation
description: Segment subtitles into readable on-screen units with strong layout discipline. Use when turning narration text or aligned subtitles into burn-in and external subtitle segments for short-form video.
---

# Subtitle Segmentation

## Goal

Turn narration-aligned text into subtitle segments that are stable, readable, and suitable for the chosen aspect ratio.

## Owns

- mandatory single-line output
- semantic splitting before forced wrap
- orphan-word prevention
- trailing punctuation removal
- avoiding ultra-short flicker segments

## Input

- aligned subtitle timing source
- target aspect ratio
- subtitle style constraints
- optional language-specific split hints

## Output

- segmented subtitle file
- segmented burned-in caption data
- segmentation report with rule decisions

## Rules

- Every subtitle must render as exactly one visual line for the chosen ratio.
- If a segment would wrap, split it into two semantic segments before rendering.
- Embedded line breaks are forbidden and must be normalized before rendering.
- Render-time font shrinking is a safeguard, not a substitute for readable segmentation.
- Do not leave a single word or very short tail phrase alone on a second line.
- Do not leave punctuation at the end of subtitle segments.
- Preserve readable timing when splitting.

## Does Not Own

- TTS generation
- subtitle alignment
- visual layout placement

## Required Artifacts

- contract: `contract.md`
- example input: `examples/input.aligned-segments.json`
- example output: `examples/output.segmented-captions.json`
- starter script: `scripts/segment_subtitles.py`

## Validation Checklist

- Every output segment must reference a source timing span.
- No output segment may end with trailing punctuation.
- No output segment may contain an embedded line break or require visual wrapping.
- Segments that would create a one-word tail must be split into two entries instead.
- New segments must preserve readable minimum duration and avoid flash-like fragments.

## Failure Conditions

- missing aligned timing source
- unsupported aspect ratio with no width heuristic
- source timing too short to split without violating minimum duration
- output contains embedded line breaks or wrap-risk segments after rule application
