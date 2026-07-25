# Task 6 Report: Workflow Stage And Mandatory Render Gate

## Status

DONE

Commit: `792b558`

## Approved Base

`1de6a3ce62cb78f551401b3eefd7d49589bdbdc8`

## TDD Evidence

- RED: Task 6 collection failed because `ensure_bgm_mix_gate` did not exist.
- RED: resume/config tests failed because audited-output reuse and workflow-driven mix settings did not exist.
- RED: snapshot test failed because the BGM stage did not validate the run library snapshot.
- GREEN: focused Task 6 tests pass after the minimal stage, gate, lineage, resume, and settings implementation.

## Implementation

- Added `bgm` between visual asset confirmation and `video_render`.
- Added local/provider/Agent resolution through the existing Task 1-5 contracts.
- Added explicit narration-only fallback reporting and warnings.
- Froze selected BGM into the run and registered selection, source, prepared stem, final mix, report, and manifest lineage.
- Added idempotent reuse of a current audited result and rejection of changed run-library snapshots.
- Bound workflow config and template policy hashes into selection/report/manifest lineage.
- Applied configured LUFS, peak, duration, and crossfade values to FFmpeg mixing.
- Required fresh subtitle-sync and BGM audio gates before writing the single Remotion `audioPath`.
- Kept narration/subtitle synchronization bound to the narration stem.
- Kept templates declarative and left CLI stage orchestration unchanged.

## Verification

- `pytest -q`: 254 passed.
- `npm --prefix renderer test`: 16 passed.
- `npm --prefix renderer run typecheck`: passed.
- `python -m py_compile ...`: passed.
- `workflow.config.json` PowerShell JSON parse: passed.
- `git diff --check`: passed.

## Self-Review

- No credentials or `*.local.json` files are included.
- Remotion still renders one `<Audio>` sourced from `audioPath`.
- Selected-track, mix, report, config, policy, hash, duration, loudness, peak, decode, and lineage failures block rendering.
- No known concerns remain.

## Review Findings Follow-Up

- Render gating now requires the canonical current-run report, selection, prepared
  stem, final mix, narration, and lineage paths; report and manifest paths must
  agree, and the narration hash is checked against the current manifest artifact.
- Legacy unfinished `video_render` runs without a BGM report migrate once to
  `bgm`, preserve prior artifacts, and record an idempotent migration marker.
- Console resume preserves a matching durable BGM Agent interaction as waiting;
  stale BGM interactions are cleared only when their request fingerprint changes.
- New runs snapshot BGM policy content, source path, source hash, and canonical
  content hash. Legacy runs freeze recorded selection policy or the live policy
  exactly once.
- Disabled BGM bypasses library resolution and library snapshot verification and
  produces the normal audited narration-only result.
- Workflow Agent candidate/response limits are bound into request fingerprints,
  response schema, parser calls, durable state submission, and service queue
  submission before raw response persistence.

## Follow-Up TDD And Verification

- RED: 13 regression cases failed across the six review findings before fixes.
- GREEN: focused BGM, interaction, service, migration, snapshot, and render-gate
  suites pass.
- `pytest -q`: 267 passed.
- `npm --prefix renderer test`: 16 passed.
- `npm --prefix renderer run typecheck`: passed.
- `python -m py_compile ...`: passed.
- `git diff --check`: passed with only repository line-ending notices.
