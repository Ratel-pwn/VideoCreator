# Shared Artifact Conventions

This repository is still in the integration-design phase.

The first implementation cycle uses shared artifact shapes so the core skills can be wired together before pulling code from the source projects.

## Goals

- keep contracts stable while implementation is still partial
- make it obvious which artifact becomes the source of truth at each stage
- avoid scenario-specific fields in shared infrastructure artifacts

## Artifact Rules

### 1. Identity

- every artifact includes `video_id`
- every artifact includes `artifact_type`
- every artifact includes `generated_at`
- every artifact may include `source_project` when derived from a reference project

### 2. Timing Source Hierarchy

The authoritative timing chain is:

1. approved narration text
2. final generated audio
3. aligned subtitle timing output from `tts-align-srt`
4. segmented subtitle output from `subtitle-segmentation`
5. rendered burn-in captions

No downstream stage should invent timing if an upstream authoritative artifact already exists.

### 3. File Format Preference

- human review artifacts: Markdown or JSON
- machine handoff artifacts: JSON
- exchange subtitle artifacts: `.srt` plus JSON mirror where useful

### 4. Audit Report Shape

Every audit report should contain:

- `video_id`
- `artifact_type`
- `status`
- `findings`
- `required_actions`
- `checked_inputs`

### 5. Cleanup Safety

Packaging or cleanup operations must classify files into:

- preserved source assets
- final deliverables
- disposable intermediates

Disposable intermediates may only be removed after explicit approval.
