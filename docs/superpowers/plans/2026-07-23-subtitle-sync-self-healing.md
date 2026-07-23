# Subtitle Synchronization Self-Healing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a text-aware subtitle alignment pipeline, a hash-bound synchronization audit, diagnosis-specific automatic repair, and a mandatory pre-render gate.

**Architecture:** Keep provider-specific TTS and Whisper invocation in scripts, while moving deterministic alignment, auditing, and repair decisions into focused `videocreator` modules. TTS emits reusable segment artifacts; alignment emits evidence; the audit verifies exact artifact hashes; the repair controller chooses one safe action per diagnosis and prevents repeated work.

**Tech Stack:** Python 3.12, Whisper word timestamps, FFmpeg/ffprobe, Volcengine TTS V3, pytest, existing Remotion renderer.

## Global Constraints

- Never retrain a cloned voice.
- Reuse the configured existing speaker and keep its raw ID in ignored local configuration.
- A failed TTS segment may be regenerated at most once per run.
- Never repeat the same repair action against unchanged input hashes.
- Rendering requires a fresh passing report bound to the exact audio and SRT hashes.
- Subtitle timing must come from recognized-text anchors, not character-count allocation.
- Automatic repair must not modify approved narration text.

---

### Task 1: Reusable TTS Segment Artifacts

**Files:**
- Modify: `scripts/volc_tts_ws.py`
- Modify: `main.py`
- Test: `tests/test_volc_tts.py`

**Interfaces:**
- Produces: `write_tts_segment_manifest(path: Path, *, segments: list[dict], output: Path, speaker_fingerprint: str) -> dict`
- Produces: CLI options `--segment-manifest PATH` and `--repair-segment ID`
- Produces: manifest artifact `audio/tts-segments.json`
- Consumes: existing `synthesize_chunk(settings, text)` and `write_audio_chunks(...)`

- [ ] **Step 1: Write failing tests for retained segments and bounded regeneration**

```python
def test_synthesize_retains_ordered_segments_and_manifest(tmp_path, monkeypatch):
    outputs = iter([(b"one", []), (b"two", [])])
    monkeypatch.setattr(volc, "synthesize_chunk", lambda *_: next(outputs))
    settings = make_settings(tmp_path, text="第一句。第二句。")
    settings["segment_manifest"] = tmp_path / "tts-segments.json"

    volc.synthesize(settings)

    manifest = json.loads(settings["segment_manifest"].read_text("utf-8"))
    assert [item["id"] for item in manifest["segments"]] == [
        "segment-0001",
        "segment-0002",
    ]
    assert all(item["audio_sha256"] for item in manifest["segments"])


def test_repair_segment_rejects_second_regeneration(tmp_path, monkeypatch):
    manifest = write_manifest(tmp_path, attempts=1)
    with pytest.raises(ValueError, match="regeneration limit"):
        volc.repair_tts_segment(manifest, "segment-0001", {}, max_attempts=1)
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `pytest tests/test_volc_tts.py -q`

Expected: FAIL because segment manifest and repair functions do not exist.

- [ ] **Step 3: Implement retained segment output and manifest writing**

```python
def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_tts_segment_manifest(path, *, segments, output, speaker_fingerprint):
    payload = {
        "schema_version": 1,
        "output_path": str(output),
        "output_sha256": sha256_file(output),
        "speaker_fingerprint": speaker_fingerprint,
        "segments": segments,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return payload
```

Store every completed chunk as `audio/segments/segment-NNNN.<format>`, then assemble from those paths. Record normalized text, text hash, audio hash, ordinal, duration, and `generation_attempts`.

- [ ] **Step 4: Implement one-segment regeneration**

Load the existing manifest, reject `generation_attempts >= 1`, synthesize only the declared segment text, replace its retained audio, increment the counter, reassemble all segments in declared order, and refresh hashes.

- [ ] **Step 5: Connect the manifest to `run_tts`**

Pass:

```python
"--segment-manifest",
str(ctx.run_dir / "audio" / "tts-segments.json"),
```

Register the resulting artifact as `tts_segment_manifest`.

- [ ] **Step 6: Verify Task 1**

Run: `pytest tests/test_volc_tts.py tests/test_main_config.py -q`

Expected: PASS.

### Task 2: Text-Aware Forced Alignment

**Files:**
- Create: `videocreator/subtitle_alignment.py`
- Modify: `scripts/align_subtitles_with_whisper.py`
- Create: `tests/test_subtitle_alignment.py`

**Interfaces:**
- Produces: `RecognizedChar(text: str, start_ms: int, end_ms: int, confidence: float)`
- Produces: `align_approved_text(approved_text: str, recognized: list[RecognizedChar]) -> AlignmentResult`
- Produces: `build_aligned_blocks(chunks: list[str], result: AlignmentResult) -> list[AlignedBlock]`
- Produces: `AlignmentResult.to_report() -> dict`
- Consumes: Whisper `segments[].words[]`

- [ ] **Step 1: Write failing tests for exact, omitted, substituted, and mixed-language text**

```python
def test_alignment_uses_matching_text_instead_of_position():
    recognized = chars("甲乙多余丙丁", step_ms=100)
    result = align_approved_text("甲乙丙丁", recognized)
    assert result.approved[2].start_ms == 400
    assert result.exact_match_coverage == 1.0


def test_alignment_reports_unanchored_omission():
    result = align_approved_text("甲乙缺失丙丁", chars("甲乙丙丁"))
    assert result.exact_match_coverage < 1.0
    assert result.unmatched_approved_spans


def test_alignment_normalizes_mixed_english_case():
    result = align_approved_text("AI时代", chars("ai时代"))
    assert result.exact_match_coverage == 1.0
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_subtitle_alignment.py -q`

Expected: FAIL because `videocreator.subtitle_alignment` does not exist.

- [ ] **Step 3: Implement normalization and monotonic matching**

Use `difflib.SequenceMatcher(autojunk=False)` over normalized visible
characters. Exact matching blocks establish anchors. Interpolate unmatched
approved characters only when bounded by anchors no farther than 12 recognized
characters apart; otherwise leave timing unresolved.

```python
matcher = SequenceMatcher(None, approved_chars, recognized_chars, autojunk=False)
for block in matcher.get_matching_blocks():
    for offset in range(block.size):
        approved[block.a + offset].bind(recognized[block.b + offset], exact=True)
```

- [ ] **Step 4: Derive subtitle boundaries from anchors**

Each subtitle block uses its first and last resolved approved characters.
Unresolved edge characters may use a neighboring anchor within 700 ms. Never
force the last subtitle to the final ASR timestamp.

- [ ] **Step 5: Emit timing JSON and alignment report**

Add CLI options:

```python
parser.add_argument("--output-timing-json", type=Path)
parser.add_argument("--output-report", type=Path)
```

The report includes file hashes, exact-match coverage, CER, timing coverage,
unmatched spans, low-confidence spans, and per-block evidence.

- [ ] **Step 6: Verify Task 2**

Run: `pytest tests/test_subtitle_alignment.py -q`

Expected: PASS.

### Task 3: Synchronization Audit and Diagnosis

**Files:**
- Create: `videocreator/subtitle_sync.py`
- Create: `scripts/audit_subtitle_sync.py`
- Create: `tests/test_subtitle_sync.py`

**Interfaces:**
- Produces: `SyncThresholds.from_dict(value: dict) -> SyncThresholds`
- Produces: `audit_subtitle_sync(audio: Path, srt: Path, report: Path, *, thresholds: SyncThresholds, segment_manifest: Path | None) -> dict`
- Produces: CLI exit `0` for pass and `1` for fail
- Consumes: Task 1 segment manifest and Task 2 alignment report

- [ ] **Step 1: Write failing tests for stale hashes, drift, overlap, and passing evidence**

```python
def test_audit_rejects_stale_audio_hash(fixture):
    fixture.audio.write_bytes(b"changed")
    result = audit_subtitle_sync(**fixture.args)
    assert result["status"] == "failed"
    assert result["findings"][0]["code"] == "artifact_hash_mismatch"


def test_audit_rejects_unexplained_boundary_drift(fixture):
    fixture.report["blocks"][0]["boundary_drift_ms"] = 1200
    result = audit_subtitle_sync(**fixture.args)
    assert "subtitle_boundary_drift" in finding_codes(result)


def test_audit_passes_fresh_high_coverage_alignment(fixture):
    result = audit_subtitle_sync(**fixture.args)
    assert result["status"] == "passed"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_subtitle_sync.py -q`

Expected: FAIL because audit module does not exist.

- [ ] **Step 3: Implement SRT and artifact validation**

Parse every SRT block, reject invalid ranges, overlaps, non-monotonic order, and
captions beyond audio duration. Recompute hashes for audio, SRT, timing JSON,
and segment manifest.

- [ ] **Step 4: Implement metric thresholds and diagnosis codes**

Emit the primary codes defined by the specification. Keep low ASR confidence
separate from boundary drift so vocabulary recognition errors do not
automatically move correct subtitles.

- [ ] **Step 5: Add the audit CLI**

Required arguments:

```text
--audio
--srt
--alignment-report
--output
--thresholds
--segment-manifest (optional)
```

- [ ] **Step 6: Verify Task 3**

Run: `pytest tests/test_subtitle_sync.py -q`

Expected: PASS.

### Task 4: Diagnosis-Driven Automatic Repair

**Files:**
- Create: `videocreator/subtitle_repair.py`
- Create: `scripts/repair_subtitle_sync.py`
- Create: `tests/test_subtitle_repair.py`

**Interfaces:**
- Produces: `RepairAction(code: str, target: str, input_fingerprint: str)`
- Produces: `choose_repair(audit: dict, history: dict) -> RepairAction | None`
- Produces: `run_repair(action, *, aligner, tts_regenerator, assembler) -> dict`
- Consumes: Task 3 findings and Task 1/2 commands

- [ ] **Step 1: Write failing tests for diagnosis mapping and deduplication**

```python
@pytest.mark.parametrize(
    ("code", "action"),
    [
        ("artifact_hash_mismatch", "rebuild_alignment"),
        ("audio_decode_failure", "reassemble_audio"),
        ("segment_missing", "regenerate_segment"),
        ("text_content_mismatch", "regenerate_segment"),
        ("asr_low_confidence", "recognize_window"),
        ("subtitle_boundary_drift", "realign_range"),
    ],
)
def test_choose_repair_maps_diagnosis(code, action):
    assert choose_repair(audit_with(code), {})["action"] == action


def test_choose_repair_does_not_repeat_unchanged_action():
    audit = audit_with("subtitle_boundary_drift")
    chosen = choose_repair(audit, {})
    assert choose_repair(audit, {chosen["fingerprint"]: chosen}) is None
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_subtitle_repair.py -q`

Expected: FAIL because repair module does not exist.

- [ ] **Step 3: Implement deterministic repair selection**

Map each diagnosis to the table in the specification. Fingerprint actions from
diagnosis, target segment/time range, and current artifact hashes.

- [ ] **Step 4: Implement the repair controller**

The controller loops only while the latest failed audit exposes an unattempted
safe action. After each action it rebuilds alignment and re-runs the audit.
Persist every attempt to `review/subtitle-sync-repairs.json`.

- [ ] **Step 5: Implement localized TTS repair invocation**

For `regenerate_segment`, invoke:

```text
python scripts/volc_tts_ws.py
  --config <local-config>
  --segment-manifest <manifest>
  --repair-segment <segment-id>
  --output <narration>
```

Do not invoke `volc_clone_voice.py`.

- [ ] **Step 6: Verify Task 4**

Run: `pytest tests/test_subtitle_repair.py tests/test_volc_tts.py -q`

Expected: PASS.

### Task 5: Mandatory Workflow and CLI Gate

**Files:**
- Modify: `main.py`
- Modify: `workflow.config.json`
- Modify: `videocreator/cli.py`
- Modify: `videocreator/workflow_state.py`
- Modify: `skills/workflow-controller/SKILL.md`
- Test: `tests/test_main_stage_dispatch.py`
- Test: `tests/test_cli.py`
- Create: `tests/test_subtitle_sync_workflow.py`

**Interfaces:**
- Produces stages `subtitle_sync` and `subtitle_sync_blocked`
- Produces CLI `vc audit subtitles <project> [--run <id>]`
- Produces CLI `vc repair subtitles <project> [--run <id>]`
- Consumes: Tasks 1-4 scripts and reports

- [ ] **Step 1: Write failing workflow tests**

```python
def test_render_refuses_missing_sync_report(context):
    with pytest.raises(RuntimeError, match="synchronization audit"):
        run_video_render(context)


def test_render_refuses_report_for_stale_srt(context):
    write_passing_report(context)
    context.subtitle_path.write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="artifact_hash_mismatch"):
        run_video_render(context)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_subtitle_sync_workflow.py tests/test_main_stage_dispatch.py -q`

Expected: FAIL because the render gate is absent.

- [ ] **Step 3: Add workflow configuration**

```json
"subtitle_sync": {
  "enabled": true,
  "audit_script": "scripts/audit_subtitle_sync.py",
  "repair_script": "scripts/repair_subtitle_sync.py",
  "min_exact_match_coverage": 0.92,
  "max_character_error_rate": 0.18,
  "min_timing_coverage": 0.98,
  "max_boundary_drift_ms": 700,
  "max_unresolved_span_ms": 2000,
  "max_tts_regeneration_per_segment": 1
}
```

- [ ] **Step 4: Add the subtitle-sync stage**

After TTS alignment, run audit. If it fails, run the repair controller and
audit again. Advance only on `status == "passed"`; otherwise set
`subtitle_sync_blocked`.

- [ ] **Step 5: Enforce the render gate**

Before asset audit, recompute current hashes through
`audit_subtitle_sync(...)`. Abort before writing render input when the report is
missing, stale, or failed.

- [ ] **Step 6: Add CLI audit and repair commands**

Both commands resolve the selected run using existing project/run resolution
helpers and print the report path plus terminal status. JSON output follows the
existing `--json` convention.

- [ ] **Step 7: Verify Task 5**

Run: `pytest tests/test_subtitle_sync_workflow.py tests/test_main_stage_dispatch.py tests/test_cli.py -q`

Expected: PASS.

### Task 6: Acceptance Project Repair and Full Verification

**Files:**
- Modify generated artifacts under: `projects/蚱蜢：游戏、生命与乌托邦/runs/20260722-infinite-game-manifesto/`
- Modify: `README.md`

**Interfaces:**
- Consumes: all preceding tasks
- Produces: passing sync audit and a newly rendered final MP4

- [ ] **Step 1: Run alignment and self-healing audit**

Run the workflow against the existing final narration, approved narration text,
and current SRT. Expected: either immediate pass or diagnosis-specific repair,
followed by `review/subtitle-sync-audit.json` with `status: passed`.

- [ ] **Step 2: Render only after the gate passes**

Run:

```powershell
python scripts/render_video.py `
  --project-root "projects/蚱蜢：游戏、生命与乌托邦" `
  --input "projects/蚱蜢：游戏、生命与乌托邦/runs/20260722-infinite-game-manifesto/render/render-input.json" `
  --output "projects/蚱蜢：游戏、生命与乌托邦/runs/20260722-infinite-game-manifesto/render/final.mp4"
```

- [ ] **Step 3: Verify the final media**

Run:

```powershell
ffmpeg -v error -i "projects/蚱蜢：游戏、生命与乌托邦/runs/20260722-infinite-game-manifesto/render/final.mp4" -f null -
ffprobe -v error -show_entries format=duration -of json "projects/蚱蜢：游戏、生命与乌托邦/runs/20260722-infinite-game-manifesto/render/final.mp4"
```

Expected: no decode errors and duration equal to the render timeline.

- [ ] **Step 4: Run all automated verification**

Run:

```powershell
pytest -q
npm --prefix renderer test
npm --prefix renderer run typecheck
git diff --check
```

Expected: all tests and checks pass.

- [ ] **Step 5: Update documentation**

Document the audit and repair CLI, report locations, render gate behavior, and
the fact that localized TTS repair consumes provider quota but never retrains a
voice.
