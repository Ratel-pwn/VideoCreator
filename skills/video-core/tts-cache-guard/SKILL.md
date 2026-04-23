---
name: tts-cache-guard
description: Prevent unnecessary regenerated voice output and protect paid or cloned TTS usage. Use when a workflow may call TTS repeatedly during iteration.
---

# TTS Cache Guard

## Goal

Ensure that voice generation is reused by default and only regenerated when explicitly justified.

## Owns

- checking whether output audio already exists
- deciding reuse versus regeneration
- enforcing force-only regeneration policy

## Input

- intended script input
- intended audio output path
- voice profile metadata
- optional force flag

## Output

- reuse / regenerate decision
- reasoning for the decision
- provenance record for the selected audio asset

## Core Rule

Do not rerun paid or cloned TTS just because the video is being iterated visually.

## Required Artifacts

- contract: `contract.md`
- example input: `examples/input.tts-cache-request.json`
- example output: `examples/output.tts-cache-decision.json`
- starter script: `scripts/tts_cache_guard.py`

## Validation Checklist

- Decisions must use a deterministic content fingerprint.
- Cloned or paid voice regeneration is blocked unless the script materially changed or `force=true`.
- The selected audio asset records origin, voice, and fingerprint metadata.

## Failure Conditions

- voice asset exists but has no provenance record
- request omits the script or voice profile fingerprint inputs
- force regeneration requested without explicit reason
