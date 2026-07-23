import json
import wave
from pathlib import Path

import pytest

from main import bind_render_inputs_to_sync_audit, ensure_subtitle_sync_gate
from videocreator.subtitle_sync import sha256_file


def write_inputs(tmp_path: Path):
    audio = tmp_path / "voice.wav"
    srt = tmp_path / "voice.srt"
    report = tmp_path / "alignment-report.json"
    audit = tmp_path / "sync-audit.json"
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\0\0" * 8000 * 2)
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n字幕\n",
        encoding="utf-8",
    )
    report.write_text(
        json.dumps({
            "audio_sha256": sha256_file(audio),
            "srt_sha256": sha256_file(srt),
            "exact_match_coverage": 1.0,
            "character_error_rate": 0.0,
            "timing_coverage": 1.0,
            "blocks": [{"index": 1, "boundary_drift_ms": 0, "confidence": 1.0}],
        }),
        encoding="utf-8",
    )
    return audio, srt, report, audit


def test_render_gate_refuses_missing_alignment_report(tmp_path):
    audio, srt, report, audit = write_inputs(tmp_path)
    report.unlink()

    with pytest.raises(RuntimeError, match="alignment report"):
        ensure_subtitle_sync_gate(audio, srt, report, audit, {})


def test_render_gate_refuses_report_for_stale_srt(tmp_path):
    audio, srt, report, audit = write_inputs(tmp_path)
    srt.write_text(srt.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8")

    with pytest.raises(RuntimeError, match="artifact_hash_mismatch"):
        ensure_subtitle_sync_gate(audio, srt, report, audit, {})


def test_render_gate_writes_passing_audit(tmp_path):
    audio, srt, report, audit = write_inputs(tmp_path)

    result = ensure_subtitle_sync_gate(audio, srt, report, audit, {})

    assert result["status"] == "passed"
    assert json.loads(audit.read_text(encoding="utf-8"))["status"] == "passed"


def test_render_gate_binds_deterministically_cleaned_inputs(tmp_path):
    audio, srt, report, audit = write_inputs(tmp_path)
    result = ensure_subtitle_sync_gate(audio, srt, report, audit, {})
    cleaned_audio = tmp_path / "voice.render.wav"
    cleaned_srt = tmp_path / "voice.render.srt"
    cleaned_audio.write_bytes(audio.read_bytes() + b"cleaned")
    cleaned_srt.write_text(
        "1\n00:00:00,000 --> 00:00:00,900\n字幕\n",
        encoding="utf-8",
    )

    bound = bind_render_inputs_to_sync_audit(
        result,
        audio_path=cleaned_audio,
        subtitle_path=cleaned_srt,
        audit_output_path=audit,
    )

    assert bound["status"] == "passed"
    assert bound["render_inputs"]["derived_from_audited_inputs"] is True
    assert bound["render_inputs"]["audio_sha256"] == sha256_file(cleaned_audio)
