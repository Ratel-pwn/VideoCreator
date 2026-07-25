import json
import wave
from pathlib import Path

from videocreator.subtitle_sync import (
    SyncThresholds,
    audit_subtitle_sync,
    sha256_file,
)


def make_fixture(tmp_path: Path) -> dict:
    audio = tmp_path / "voice.wav"
    srt = tmp_path / "voice.srt"
    report = tmp_path / "alignment-report.json"
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\0\0" * 8000 * 3)
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n第一句\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\n第二句\n",
        encoding="utf-8",
    )
    report.write_text(
        json.dumps({
            "audio_sha256": sha256_file(audio),
            "srt_sha256": sha256_file(srt),
            "exact_match_coverage": 0.98,
            "character_error_rate": 0.05,
            "timing_coverage": 1.0,
            "blocks": [
                {"index": 1, "boundary_drift_ms": 100, "confidence": 0.9},
                {"index": 2, "boundary_drift_ms": 120, "confidence": 0.9},
            ],
        }),
        encoding="utf-8",
    )
    return {
        "audio": audio,
        "srt": srt,
        "alignment_report": report,
        "thresholds": SyncThresholds(),
    }


def finding_codes(result: dict) -> set[str]:
    return {item["code"] for item in result["findings"]}


def test_audit_rejects_stale_audio_hash(tmp_path):
    fixture = make_fixture(tmp_path)
    fixture["audio"].write_bytes(fixture["audio"].read_bytes() + b"changed")

    result = audit_subtitle_sync(**fixture)

    assert result["status"] == "failed"
    assert "artifact_hash_mismatch" in finding_codes(result)


def test_audit_rejects_unexplained_boundary_drift(tmp_path):
    fixture = make_fixture(tmp_path)
    report = json.loads(fixture["alignment_report"].read_text(encoding="utf-8"))
    report["blocks"][0]["boundary_drift_ms"] = 1200
    fixture["alignment_report"].write_text(json.dumps(report), encoding="utf-8")

    result = audit_subtitle_sync(**fixture)

    assert "subtitle_boundary_drift" in finding_codes(result)


def test_audit_rejects_subtitle_overlap(tmp_path):
    fixture = make_fixture(tmp_path)
    fixture["srt"].write_text(
        "1\n00:00:00,000 --> 00:00:01,200\n第一句\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\n第二句\n",
        encoding="utf-8",
    )
    report = json.loads(fixture["alignment_report"].read_text(encoding="utf-8"))
    report["srt_sha256"] = sha256_file(fixture["srt"])
    fixture["alignment_report"].write_text(json.dumps(report), encoding="utf-8")

    result = audit_subtitle_sync(**fixture)

    assert "subtitle_overlap" in finding_codes(result)


def test_audit_rejects_subtitle_beyond_audio_duration(tmp_path):
    fixture = make_fixture(tmp_path)
    fixture["srt"].write_text(
        "1\n00:00:00,000 --> 00:00:03,500\n第一句\n",
        encoding="utf-8",
    )
    report = json.loads(fixture["alignment_report"].read_text(encoding="utf-8"))
    report["srt_sha256"] = sha256_file(fixture["srt"])
    fixture["alignment_report"].write_text(json.dumps(report), encoding="utf-8")

    result = audit_subtitle_sync(**fixture)

    assert "subtitle_boundary_drift" in finding_codes(result)


def test_audit_passes_fresh_high_coverage_alignment(tmp_path):
    result = audit_subtitle_sync(**make_fixture(tmp_path))

    assert result["status"] == "passed"
    assert result["findings"] == []


def test_audit_targets_the_segment_containing_unmatched_text(tmp_path):
    fixture = make_fixture(tmp_path)
    first = tmp_path / "segment-0001.mp3"
    second = tmp_path / "segment-0002.mp3"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    manifest = tmp_path / "tts-segments.json"
    manifest.write_text(
        json.dumps({
            "output_sha256": sha256_file(fixture["audio"]),
            "segments": [
                {
                    "id": "segment-0001",
                    "ordinal": 1,
                    "text": "甲乙",
                    "audio_path": str(first),
                    "audio_sha256": sha256_file(first),
                },
                {
                    "id": "segment-0002",
                    "ordinal": 2,
                    "text": "丙丁",
                    "audio_path": str(second),
                    "audio_sha256": sha256_file(second),
                },
            ],
        }),
        encoding="utf-8",
    )
    report = json.loads(fixture["alignment_report"].read_text(encoding="utf-8"))
    report["exact_match_coverage"] = 0.5
    report["unmatched_approved_spans"] = [
        {"start_index": 2, "end_index": 4, "text": "丙丁"}
    ]
    fixture["alignment_report"].write_text(json.dumps(report), encoding="utf-8")
    fixture["segment_manifest"] = manifest

    result = audit_subtitle_sync(**fixture)

    mismatch = next(
        item for item in result["findings"]
        if item["code"] == "text_content_mismatch"
    )
    assert mismatch["target"] == "segment-0002"
