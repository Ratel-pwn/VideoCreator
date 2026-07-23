# Subtitle Synchronization Self-Healing Design

## Goal

VideoCreator must not render a final video unless the exact narration audio and
subtitle files have passed a synchronization audit. When the audit finds a
repairable problem, the workflow diagnoses the cause and applies a targeted
repair automatically instead of repeating the same alignment operation.

The mechanism must handle:

- subtitle boundary drift,
- missing, repeated, or reordered spoken content,
- mismatched script, audio, and subtitle versions,
- damaged or incorrectly concatenated audio,
- abnormal silence or truncation,
- low-confidence speech recognition,
- false positives caused by names, English terms, and specialized vocabulary.

## Scope

This iteration covers narration generated through the existing Volcengine TTS
adapter, Whisper-based recognition, SRT subtitles, and the Remotion render
workflow.

It does not retrain or replace a cloned voice. It may call Volcengine TTS again
for a failed narration segment, using the already configured speaker. Each
segment may be regenerated at most once during one workflow run.

## Pipeline Position

The authoritative sequence is:

1. approved narration text,
2. chunked TTS generation,
3. audio assembly and integrity validation,
4. Whisper recognition with word timestamps,
5. text-aware forced alignment,
6. subtitle segmentation,
7. synchronization audit,
8. diagnosis-driven repair,
9. synchronization re-audit,
10. visual planning and final rendering.

Rendering is forbidden unless the current audio and SRT hashes match a passing
audit report.

## Artifacts

The TTS stage writes:

- `audio/narration.generated.<format>`,
- `audio/tts-segments.json`,
- optional retained segment audio under `audio/segments/`.

`tts-segments.json` records, for every segment:

- stable segment ID and ordinal,
- normalized source text,
- source-text SHA-256,
- speaker fingerprint, never the raw speaker ID,
- generated audio path and SHA-256,
- measured duration,
- generation attempt count,
- assembly order.

The alignment stage writes:

- `subtitles/subtitles.aligned.srt`,
- `subtitles/alignment-timing.json`,
- `subtitles/alignment-report.json`.

The audit and repair stages write:

- `review/subtitle-sync-audit.json`,
- `review/subtitle-sync-repairs.json`,
- optional diagnostic clips for unresolved spans.

All reports identify the exact source files by SHA-256.

## Text-Aware Alignment

Whisper supplies recognized words, timestamps, and confidence values. The
aligner normalizes both the approved narration and recognized text into
comparable characters while preserving a mapping back to the original text.

The aligner performs monotonic sequence alignment between recognized characters
and approved characters. Exact matches establish timing anchors. Small
substitutions and omissions may be interpolated only between reliable anchors.
An unmatched region cannot silently inherit timing for the rest of the video.

Subtitle block boundaries are derived from the first and last aligned
characters in each block. They are not allocated by character count.

The alignment report contains:

- approved and recognized character counts,
- exact-match coverage,
- character error rate,
- timing coverage,
- confidence distribution,
- unmatched approved and recognized spans,
- per-block start and end confidence,
- warnings for interpolated boundaries.

## Synchronization Audit

The audit operates on the final audio used by Remotion and the final SRT. It
verifies:

- audio, SRT, approved text, segment manifest, and alignment report hashes,
- decodability and continuous audio assembly,
- no unexpected duplicate or missing TTS segment,
- monotonic, non-overlapping subtitle ranges,
- subtitle coverage of recognized speech,
- text agreement between each subtitle and overlapping recognized words,
- start and end boundary drift,
- abnormal silence within spoken sections,
- final subtitle proximity to the final spoken boundary.

Default pass thresholds are configurable. Initial defaults are:

- exact-match coverage at least 92%,
- character error rate at most 18%,
- timing coverage at least 98%,
- no missing or repeated TTS segment,
- no subtitle overlap,
- no unexplained boundary drift above 700 ms,
- no unresolved span longer than 2 seconds.

Specialized vocabulary may lower text confidence without proving timing drift.
The audit therefore treats low textual confidence and boundary drift as
different signals.

## Diagnosis

Each failure receives one primary error code:

- `artifact_hash_mismatch`,
- `audio_decode_failure`,
- `segment_missing`,
- `segment_duplicate`,
- `segment_order_mismatch`,
- `unexpected_silence`,
- `speech_truncated`,
- `asr_low_confidence`,
- `text_content_mismatch`,
- `subtitle_boundary_drift`,
- `subtitle_overlap`,
- `audit_threshold_false_positive`.

The report includes evidence, affected segment IDs, time ranges, and the next
allowed repair action.

## Automatic Repair

Repairs are selected by diagnosis rather than a fixed retry count:

| Diagnosis | Automatic action |
| --- | --- |
| Hash mismatch | Rebuild downstream artifacts from the current authoritative input. |
| Decode or assembly failure | Reassemble retained TTS segments with FFmpeg and revalidate. |
| Missing, duplicate, or reordered segment | Restore the declared segment order; regenerate only a missing or invalid segment. |
| Unexpected silence or truncation | Regenerate the affected TTS segment once, then reassemble. |
| ASR low confidence | Re-recognize the affected audio window with context and finer segmentation. |
| Text content mismatch | Compare against the segment manifest; regenerate the mismatched segment once. |
| Boundary drift | Re-run forced alignment for the affected anchored range and rebuild SRT blocks. |
| Subtitle overlap | Recompute adjacent boundaries from aligned character anchors. |
| Threshold false positive | Re-evaluate names and mixed-language spans with normalized and phonetic matching. |

Every action is recorded with its input hashes and result. The workflow never
repeats the same action against unchanged inputs.

## Repair Limits

- Voice training is never invoked.
- A TTS segment may be regenerated at most once per workflow run.
- Full narration regeneration is not an automatic repair.
- Repairs may change subtitle timing and affected TTS segment audio, but not the
  approved narration text.
- If no new safe repair is available, the workflow stops.

## Render Gate

Immediately before render input is written, VideoCreator loads the latest audit
report and recomputes the audio and SRT hashes.

Rendering proceeds only when:

- audit status is `passed`,
- report hashes match current artifacts,
- no unresolved finding has severity `error`,
- every automatic repair has a terminal result,
- the report was generated after the most recent audio or SRT modification.

Failure sets the workflow stage to `subtitle_sync_blocked` and points to the
audit and repair reports.

## Configuration

`workflow.config.json` gains a `subtitle_sync` section containing:

- enabled flag,
- audit and repair script paths,
- matching and timing thresholds,
- diagnostic clip settings,
- per-segment TTS regeneration limit,
- whether retained TTS segment audio may be reused.

Templates may tighten presentation-related subtitle constraints, but they
cannot disable the core synchronization gate.

Provider credentials and the raw speaker ID remain in ignored local
configuration.

## CLI and Workflow Service

The high-level workflow exposes:

- `vc audit subtitles <project> [--run <id>]`,
- `vc repair subtitles <project> [--run <id>]`,
- `vc resume <project>` automatically resumes diagnosis and repair before
  visual planning or rendering.

The same operations are exposed through the workflow service so MCP clients do
not need to call low-level scripts directly.

## Failure Handling

The workflow stops only when:

- credentials or network access required for an approved repair are unavailable,
- the approved text conflicts with the declared TTS segment manifest,
- the source audio remains undecodable after reassembly,
- a segment remains incorrect after its single allowed regeneration,
- recognition cannot establish enough timing anchors,
- all safe diagnosis-specific repair actions have already been attempted.

The blocked report must state the root cause, evidence, attempted actions, and
the smallest user decision required to continue.

## Testing

Unit tests cover:

- normalized character alignment,
- substitutions, omissions, and mixed Chinese/English text,
- boundary derivation from anchors,
- hash and freshness validation,
- every diagnosis-to-repair mapping,
- repair deduplication and per-segment limits,
- render blocking on stale or failed reports.

Integration fixtures cover:

- a correctly synchronized narration,
- globally shifted subtitles,
- one missing spoken sentence,
- duplicate TTS audio,
- corrupted MP3 assembly,
- a low-confidence specialized term,
- successful localized TTS regeneration,
- a failure that remains blocked after all safe repairs.

The existing project `蚱蜢：游戏、生命与乌托邦` is the acceptance case. Its
audio is re-aligned, audited, repaired if necessary, rendered, and checked for
decodability only after the synchronization gate passes.

## Acceptance Criteria

- No final render begins without a passing, fresh synchronization audit.
- Subtitle timing is based on matched recognized text, never only character
  counts.
- Repair actions are diagnosis-specific and idempotent.
- Existing cloned voice IDs are reused without voice retraining.
- TTS regeneration is localized and bounded.
- Reports identify exact artifacts by hash without exposing credentials or raw
  speaker IDs.
- The acceptance project produces a passing audit and a newly rendered final
  video.
