---
name: project-packager
description: Package final deliverables into a stable per-video project directory while cleaning disposable intermediate versions. Use when a video is approved and ready to ship.
---

# Project Packager

## Goal

Move a finished video into a stable project layout and remove disposable intermediate versions without deleting source materials.

## Owns

- final project directory layout
- deliverable manifest updates
- intermediate render cleanup
- preservation of source assets

## Input

- approved render outputs
- metadata and cover artifacts
- project manifest or equivalent video id
- cleanup approval flag

## Output

- packaged project directory
- updated deliverable manifest
- cleanup report

## Does Not Own

- cover creation
- metadata writing logic
- video rendering itself

## Required Artifacts

- contract: `contract.md`
- example input: `examples/input.package-request.json`
- example output: `examples/output.package-report.json`
- starter script: `scripts/package_project.py`

## Validation Checklist

- Final deliverables live inside one per-video project folder.
- Source assets are preserved even when intermediates are removed.
- Cleanup only runs after explicit approval.
- Deliverable references stay updated after packaging.

## Failure Conditions

- package request has no stable video id
- required final outputs are missing
- cleanup would remove preserved source assets
