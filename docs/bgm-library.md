# Adding And Resolving BGM

## Capability Contract

**Problem:** select one suitable instrumental background track, preserve its provenance, mix it below narration, and block rendering when the selected media or audit lineage is stale.

**Exact input:** the approved script, topic, selected template and immutable `bgm.json` snapshot, narration audio, passing subtitle-sync audit, one independently resolved BGM library, and the public or local search configuration.

**Exact output:** `audio/bgm-selection.json`, an optional frozen `audio/bgm.source.<ext>` plus `audio/bgm.source.bgm.json`, an optional `audio/bgm.prepared.wav`, `audio/bgm-mix-report.json`, and either `audio/final-mix.wav` or the unchanged narration path.

**Non-ownership:** this capability does not generate music, modify approved narration or subtitle timing, choose visual assets, retain source-video audio, add a second Remotion audio track, or put executable behavior in templates.

## Add A Local Track

Add the audio file and a same-stem UTF-8 JSON sidecar:

```text
projects/<project>/library/bgm/          # Project-level complete override
├── calm-technology.mp3                  # Decodable local audio
└── calm-technology.bgm.json             # Metadata and provenance sidecar
```

Supported audio suffixes are `.mp3`, `.wav`, `.m4a`, `.aac`, `.flac`, and `.ogg`. A file without a valid sidecar, a unique ID, or decodable audio is ineligible.

Example sidecar:

```json
{
  "schema_version": 1,
  "id": "calm-technology",
  "title": "Calm Technology",
  "creator": "Example Composer",
  "source_url": "https://example.org/tracks/calm-technology",
  "license": "CC BY 4.0",
  "rights_status": "cleared",
  "subjects": ["technology", "education"],
  "moods": ["calm", "reflective"],
  "energy": "low-medium",
  "tempo_bpm": 88,
  "instrumental": true,
  "template_tags": ["science-explainer"],
  "avoid_for": ["breaking-news"],
  "preferred_start_ms": 0,
  "loopable": true
}
```

## Sidecar Fields

| Field | Required | Contract |
| --- | --- | --- |
| `schema_version` | Yes | Integer `1`. |
| `id` | Yes | Non-empty stable ID, unique within one library level. |
| `title` | Yes | Reader-facing track title. |
| `subjects` | Yes | Array of non-empty subject tags used for deterministic scoring. |
| `moods` | Yes | Array of non-empty mood tags used for deterministic scoring. |
| `energy` | Yes | Non-empty energy label such as `low`, `low-medium`, or `medium`. |
| `instrumental` | Yes | Boolean; template policy normally rejects vocals. |
| `creator` | No | Creator or performer attribution. |
| `source_url` | No | Public HTTP(S) source page without credentials or signed query data. |
| `license` | No | License or usage basis recorded in selection provenance. |
| `rights_status` | No | Rights review label; missing or `unknown` remains eligible but emits a warning. |
| `tempo_bpm` | No | Numeric tempo or `null`. |
| `template_tags` | No | Array of template IDs or scenario tags. |
| `avoid_for` | No | Array of subjects, moods, or template tags that reduce selection score. |
| `preferred_start_ms` | No | Non-negative crop offset; defaults to `0`. |
| `loopable` | No | Boolean; defaults to `true`. Short non-loopable tracks are rejected for longer narration. |

## Complete Override

BGM is resolved independently from style and voice:

1. `projects/<project>/library/bgm/`
2. `templates/<template>/library/bgm/`
3. `library/bgm/default/`

The first level containing at least one eligible track wins completely. Tracks from lower levels are not merged. Empty or wholly invalid directories do not override; they emit warnings and resolution continues to the next level. The selected level, audio hash, sidecar hash, and provenance are frozen in the run input snapshot.

## Online Fallback

When no local candidate is eligible, VideoCreator queries enabled core providers from `config/bgm-search.local.json`, falling back to the committed `config/bgm-search.example.json` defaults when the local file is absent. Provider candidates must expose public HTTP(S) source and download URLs. Downloads are size-bounded, redirect- and host-validated, media-probed, stored inside the run, and retain creator, source, provider, license, and rights metadata.

If providers do not produce an eligible track and the workflow is running through an MCP-capable Agent, it writes a durable `bgm_candidates` interaction. The Agent searches public downloadable material and returns the declared JSON schema. The run waits and resumes from that interaction without discarding accepted responses or duplicating validated downloads.

If provider and Agent paths both fail, or Agent handoff is unavailable, the stage writes a passing `narration_only` mix report with warnings. Production continues with the unchanged narration.

Unknown rights never silently become cleared rights. They are preserved as `unknown` in selection and mix provenance and emit a warning, but do not block rendering. The producer remains responsible for final rights review.

## Mix And Render Gate

The subtitle synchronization gate always audits narration before BGM is introduced. FFmpeg then crops or crossfades a loop to narration duration, applies configured fades, uses narration-driven sidechain ducking, and loudness-normalizes one final mix.

The render gate fails closed if the selected audio, sidecar, narration, prepared stem, final mix, workflow configuration, template policy, or run-local lineage no longer matches its recorded hash. In BGM mode Remotion receives only `audio/final-mix.wav`; in narration-only mode it receives only the narration. Source footage is always rendered with its original audio muted.

## Search Configuration

Copy `config/bgm-search.example.json` to ignored `config/bgm-search.local.json` only when local overrides are needed. Keep provider keys, tokens, private endpoints, and machine-specific values out of committed examples, templates, project snapshots, and run manifests.
