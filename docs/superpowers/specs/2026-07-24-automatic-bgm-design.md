# Automatic BGM Selection And Mixing Design

## Goal

VideoCreator must automatically add one suitable background-music track to a
video when a valid local BGM library is available. When no local BGM resource
is available, it must search online using the project subject and selected
template as context. If all online lookup fails, the workflow may render a
narration-only video with an explicit warning.

The feature must preserve the existing responsibility boundaries:

- templates declare music preferences but contain no executable code,
- Python owns selection, downloading, validation, workflow state, and FFmpeg
  invocation,
- FFmpeg owns deterministic audio preparation and mixing,
- Remotion continues to consume one final audio track,
- narration/subtitle synchronization is audited before BGM is introduced.

## Non-Goals

This version does not:

- change BGM by chapter or scene,
- generate music with an AI music provider,
- edit the approved narration or subtitle timing,
- mix source-video audio into the final video,
- make legal claims about online music licensing,
- provide a full music-library management interface.

## Resource Layout

BGM is a new independently resolved library resource type:

```text
projects/<project>/library/bgm/          # Project-local BGM override
templates/<template>/library/bgm/        # Template-local BGM override
library/bgm/default/                     # Global default BGM library
```

Resolution uses the repository's complete-override rule:

1. use the project BGM directory when it contains at least one valid track,
2. otherwise use the template BGM directory when it contains a valid track,
3. otherwise use the global default BGM directory when it contains a valid
   track,
4. otherwise begin online search.

Tracks from different levels are never merged into one candidate pool. A
directory containing files but no valid track does not override a lower valid
level.

Each local track consists of an audio file and a same-stem metadata sidecar:

```text
calm-technology.mp3
calm-technology.bgm.json
```

The sidecar schema contains:

```json
{
  "schema_version": 1,
  "id": "calm-technology",
  "title": "Calm Technology",
  "creator": "Example Creator",
  "source_url": "https://example.com/source-page",
  "license": "Unknown",
  "rights_status": "unknown",
  "subjects": ["technology", "education"],
  "moods": ["reflective", "restrained"],
  "energy": "low-medium",
  "tempo_bpm": 88,
  "instrumental": true,
  "template_tags": ["science-explainer"],
  "avoid_for": ["comedy"],
  "preferred_start_ms": 0,
  "loopable": true
}
```

Required fields are `schema_version`, `id`, `title`, `subjects`, `moods`,
`energy`, and `instrumental`. A missing sidecar, invalid schema, missing audio,
or undecodable audio makes the track ineligible and produces a library warning.

## Template Policy

Each template may declare `bgm.json`:

```json
{
  "schema_version": 1,
  "enabled": true,
  "instrumental_only": true,
  "preferred_moods": ["reflective", "restrained", "technological"],
  "preferred_energy": "low-medium",
  "preferred_tempo_bpm": [70, 105],
  "avoid_tags": ["vocal", "heavy-drums", "comedy"],
  "ducking_strength": "medium",
  "fade_in_ms": 2000,
  "fade_out_ms": 3000
}
```

Templates own only preference and presentation policy. They do not own search,
download, scoring, FFmpeg commands, or provider credentials. A missing template
policy uses conservative core defaults. `enabled: false` disables BGM for that
template without disabling narration audio.

## Candidate Query And Scoring

The selector consumes:

- project title and metadata,
- template ID and BGM policy,
- approved narration text,
- detected subject category,
- local BGM sidecars or online candidate metadata.

It produces normalized Chinese and English query terms. Candidate scoring is
deterministic and records every component:

- subject match,
- mood match,
- template-tag match,
- energy match,
- tempo-range match,
- instrumental requirement,
- avoided-tag penalty,
- decode and duration eligibility.

The highest-scoring eligible candidate wins. Stable track ID is the final
tie-breaker so the same frozen inputs produce the same selection.

## Hybrid Online Search

Online search has two layers:

1. the core tries configured provider adapters,
2. if no eligible candidate remains, the MCP workflow asks a connected Agent
   to find candidates.

Provider adapters return candidate metadata and URLs only. Credentials and
provider-specific settings remain in ignored local configuration.

The Agent request includes the normalized search terms, template preferences,
duration requirement, and rejected candidates. The Agent returns structured
candidate records containing:

- title,
- creator when available,
- source page URL,
- direct download URL,
- provider,
- known license text,
- proposed subject, mood, energy, tempo, and instrumental tags.

The Agent may not write files directly into the run. The core downloads every
candidate, validates its URL and media, recomputes technical metadata, and
scores it using the same deterministic selector used for local tracks.

Public accessibility or downloadability is not represented as permission.
Unknown rights are stored as `rights_status: unknown`. This status generates a
prominent production warning but does not block rendering.

## Run Artifacts

Every run freezes the selected inputs and generated outputs:

```text
runs/<run-id>/inputs/library.snapshot.json   # Selected BGM library level and hashes
runs/<run-id>/audio/bgm.source.<ext>         # Frozen selected local or online track
runs/<run-id>/audio/bgm-selection.json       # Candidates, scores, rejection reasons, provenance
runs/<run-id>/audio/bgm.prepared.wav         # Normalized, cropped or looped BGM stem
runs/<run-id>/audio/final-mix.wav            # Narration plus BGM
runs/<run-id>/audio/bgm-mix-report.json      # Hash chain, mix parameters, loudness, warnings
```

The manifest registers each produced artifact. A local track is copied into the
run rather than rendered from a mutable library path. An online track is stored
only as a run artifact in this version; automatic promotion to a reusable
project library is out of scope.

## Audio Preparation And Mixing

Only one BGM track is used for the entire video.

FFmpeg performs the following deterministic steps:

1. decode and standardize the selected track to the configured sample rate and
   stereo channel layout,
2. begin at `preferred_start_ms` when declared and valid,
3. crop tracks longer than the narration,
4. repeat shorter loopable tracks with equal-power crossfades at loop points,
5. reject non-loopable tracks that are too short and try the next candidate,
6. apply the configured intro fade-in and outro fade-out,
7. use narration as the sidechain signal to duck BGM while speech is present,
8. allow a bounded BGM recovery during narration pauses,
9. normalize the final mix and enforce peak limits,
10. make the final mix duration match narration within 100 milliseconds.

Conservative defaults are:

- final integrated loudness near `-16 LUFS`,
- true peak no higher than `-1.5 dBTP`,
- BGM approximately `12-18 dB` below narration while speech is active,
- `2000ms` fade-in,
- `3000ms` fade-out,
- medium sidechain ducking.

Exact FFmpeg filter parameters are core configuration, not template executable
content. Templates select a named ducking strength, which the core maps to
validated parameters.

## Workflow Integration

The workflow gains a `bgm` stage:

```text
visual_assets -> bgm -> video_render
```

`main.py` owns this stage. `videocreator/cli.py` continues to own command
parsing and run selection only; `vc resume` reaches the BGM stage through the
normal workflow controller and does not duplicate its implementation.

The stage:

1. verifies that narration/subtitle synchronization has passed,
2. resolves the effective BGM library,
3. selects a local candidate or starts hybrid online search,
4. freezes and prepares the selected track,
5. mixes narration and BGM,
6. writes selection and mix reports,
7. registers `final_mix` as the render audio,
8. advances only after the mix audit passes.

If no candidate can be selected, the stage registers a narration-only fallback
and a warning, then advances. It does not manufacture an empty music file.

Changing only the BGM selection or mix policy reruns BGM preparation, mixing,
and downstream rendering. It does not regenerate TTS, subtitles, the visual
plan, or visual assets.

## Audit And Render Gate

Narration/subtitle synchronization remains bound to the narration stem, not the
music mix. The BGM mix report separately binds:

- narration path and SHA-256,
- selected BGM source path and SHA-256,
- prepared BGM path and SHA-256,
- template BGM policy hash,
- core mix configuration hash,
- FFmpeg version and command parameters,
- final mix path and SHA-256,
- narration, BGM, and final durations,
- measured integrated loudness and true peak,
- provenance and rights warnings.

Rendering requires:

- a current passing narration/subtitle synchronization report,
- a passing BGM mix report when BGM was selected,
- a valid narration-only fallback report when no BGM was selected,
- an exact hash match between the declared render audio and the audited mix or
  narration fallback,
- final duration within 100 milliseconds of narration,
- decodable output with no detected clipping,
- source and retrieval metadata for online music.

`rights_status: unknown` is a warning, not an error. The report must not state
or imply that an unknown-rights track is licensed.

## Failure Handling

- Invalid local track: exclude it, record the reason, and continue.
- No valid track at the selected local level: try the next valid library level.
- No valid local library: start online provider search.
- Provider failure or no results: request Agent candidates.
- Candidate download or decode failure: record rejection and try the next
  candidate.
- No eligible online candidate: use narration-only fallback with a warning.
- Selected-track preparation or mixing failure: block the workflow instead of
  silently removing BGM, because this indicates a processing defect after a
  valid selection.
- Loudness, peak, hash, or duration audit failure: block rendering and retain
  diagnostic reports.
- Missing Agent connection: treat Agent search as unavailable and continue to
  narration-only fallback after provider search.

## Security And Isolation

- Provider credentials remain in ignored local configuration.
- Templates, sidecars, project snapshots, and run manifests contain no API
  credentials.
- Downloaded paths must remain inside the current run.
- Candidate URLs are validated before download and cannot select local machine
  paths.
- The feature modifies only the active VideoCreator project and run.
- No external source repository is read for mutation or written.

## Verification

Unit tests cover:

- project, template, and global complete-override resolution,
- invalid directories not masking lower valid levels,
- BGM sidecar validation,
- deterministic scoring and tie-breaking,
- provider-to-Agent search fallback,
- unknown-rights warning behavior,
- narration-only fallback behavior,
- manifest and hash-chain validation,
- workflow stage transition and resume behavior.

FFmpeg integration tests use short generated fixtures to verify:

- crop behavior for long tracks,
- loop and equal-power crossfade behavior for short tracks,
- fade-in and fade-out,
- sidechain ducking during speech,
- bounded recovery during pauses,
- output duration,
- integrated loudness and true peak limits,
- decodable final audio.

Renderer tests verify that:

- render input still contains one authoritative audio path,
- the path selects `final-mix.wav` after a successful mix,
- narration is selected after an explicit no-BGM fallback,
- the final MP4 contains valid H.264 video and AAC audio.

Completion verification runs Python tests, Remotion tests, TypeScript checks,
manifest audits, full media decode, duration checks, representative frame
inspection, and sibling-project isolation checks.
