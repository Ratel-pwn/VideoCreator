---
name: subtitle-layout-audit
description: Audit subtitle placement and readability against the actual rendered frame and chosen aspect ratio. Use when checking whether captions are too low, too small, overlapping UI, or wrapping poorly.
---

# Subtitle Layout Audit

## Goal

Validate subtitle placement and readability inside the actual video layout.

## Owns

- subtitle safe-zone checks
- overlap checks with UI and screenshots
- wrap-risk review by ratio

## Input

- rendered or planned frames
- subtitle style settings
- target aspect ratio
- optional UI bounding boxes / safe-area map

## Output

- audit notes
- required layout changes
- pass/fail report with ratio-specific findings

## Does Not Own

- subtitle segmentation logic
- final script writing

## Required Artifacts

- contract: `contract.md`
- example input: `examples/input.layout-audit-request.json`
- example output: `examples/output.layout-audit-report.json`
- starter script: `scripts/audit_subtitle_layout.py`

## Validation Checklist

- The subtitle box stays above the lower edge safe zone.
- Font size is readable for mobile-first short video use.
- Layout checks are applied for `3:4`, `9:16`, `4:3`, and `16:9`.
- Preview review happens before final render approval.

## Failure Conditions

- no preview frames or layout metadata supplied
- subtitle box intersects reserved UI zones
- subtitle font size falls below configured floor
- ratio-specific checks cannot be computed from the input
