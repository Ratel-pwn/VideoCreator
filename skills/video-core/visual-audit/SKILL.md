---
name: visual-audit
description: Audit planned and rendered video visuals for readability, source correctness, and presentation quality. Use when checking screenshots, layouts, motion settling, and subtitle-safe regions.
---

# Visual Audit

## Goal

Catch visual problems before a video is treated as done.

## Owns

- source/claim mismatch checks
- screenshot cleanliness checks
- lazy-load / placeholder checks
- overlap and readability checks
- representative frame review

## Input

- planned shots
- rendered video frames
- source screenshots
- spoken-point or narration mapping per scene

## Output

- shot-by-shot findings
- required revisions before approval
- pre-render and post-render gate summary

## Does Not Own

- script writing
- TTS alignment

## Required Artifacts

- contract: `contract.md`
- example input: `examples/input.visual-audit-request.json`
- example output: `examples/output.visual-audit-report.json`
- starter script: `scripts/run_visual_audit.py`

## Validation Checklist

- Every scene is checked, not only the opener.
- Lazy-loaded, partially rendered, or placeholder captures are rejected.
- Footage or screenshots must match the spoken point of the segment.
- Text must remain readable against the underlying imagery.

## Failure Conditions

- one or more scenes missing representative frames
- scene source evidence cannot be tied to the spoken point
- busy-image collision risk with overlays remains unresolved
