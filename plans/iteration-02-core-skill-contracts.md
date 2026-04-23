# Iteration 02: Core Skill Contracts

## Goal

Advance the first implementation cycle from plan-only skill skeletons to reusable contracts plus starter implementation entrypoints.

## Scope Included

- `subtitle-segmentation`
- `subtitle-layout-audit`
- `tts-cache-guard`
- `tts-align-srt`
- `visual-audit`
- `project-packager`

## What This Iteration Adds

### 1. Contract Documents

Each priority core skill now has:

- explicit required inputs
- explicit outputs
- validation rules
- failure conditions
- upstream/downstream dependency notes

### 2. Shared Artifact Conventions

Added a shared artifact note at `skills/shared/artifact-conventions.md` so the first implementation pass uses the same identity, timing, audit, and cleanup expectations.

### 3. Example Artifacts

Each priority core skill now includes one example request and one example response artifact. These are intended to stabilize interface shape before implementation is pulled from the source projects.

### 4. Starter Script Entry Points

Each priority core skill now includes a minimal Python CLI entrypoint that:

- validates required input fields
- writes a structured stub output
- provides a clear place to wire extracted implementation next

## What This Iteration Does Not Yet Do

- perform real subtitle segmentation
- perform real forced alignment
- inspect frames or screenshots
- package files on disk
- connect to either source project directly

That work belongs to the next implementation pass.

## Recommended Next Iteration

Implement real logic in this order:

1. `subtitle-segmentation/scripts/segment_subtitles.py`
2. `tts-cache-guard/scripts/tts_cache_guard.py`
3. `tts-align-srt/scripts/align_tts_srt.py`
4. `subtitle-layout-audit/scripts/audit_subtitle_layout.py`
5. `visual-audit/scripts/run_visual_audit.py`
6. `project-packager/scripts/package_project.py`

## Acceptance For This Iteration

This iteration is complete if:

1. the six priority core skills no longer rely on vague ownership statements alone
2. each of them has a stable artifact contract
3. each of them has an implementation slot that can be wired next
