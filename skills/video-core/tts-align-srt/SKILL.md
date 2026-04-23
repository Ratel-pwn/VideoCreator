---
name: tts-align-srt
description: Align subtitle timing to final generated audio and produce the timing source used by downstream video assembly. Use when TTS output exists and subtitle timing must be normalized.
---

# TTS Align SRT

## Goal

Generate or refine the subtitle timing source from final audio plus approved text.

## Owns

- alignment against final audio
- producing stable `.srt` timing output
- defining the canonical subtitle timing source for downstream use

## Input

- final audio
- approved narration text
- provisional subtitle timing if available
- alignment engine configuration

## Output

- aligned `.srt`
- normalized downstream caption timing source
- alignment report with confidence / drift notes

## Does Not Own

- subtitle segmentation policy
- TTS generation itself

## Required Artifacts

- contract: `contract.md`
- example input: `examples/input.tts-align-request.json`
- example output: `examples/output.aligned-srt.json`
- starter script: `scripts/align_tts_srt.py`

## Validation Checklist

- Aligned subtitle output becomes the canonical timing source.
- Downstream burn-in steps must consume this output instead of inferred durations.
- Alignment emits a machine-readable report for drift or low-confidence spans.

## Failure Conditions

- final audio missing or unreadable
- approved text missing
- alignment engine output cannot cover the entire narration
