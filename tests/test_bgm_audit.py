import json
import hashlib
import math
from dataclasses import replace
from pathlib import Path

from videocreator.bgm_library import BgmTrack
from videocreator.bgm_policy import BgmPolicy


def finding_codes(result):
    return {finding["code"] for finding in result["findings"]}


def make_result(tmp_path: Path):
    from videocreator.bgm_mix import BgmMixResult, BgmMixSettings, sha256_file

    narration = tmp_path / "narration.wav"
    source = tmp_path / "source.wav"
    metadata = tmp_path / "source.bgm.json"
    prepared = tmp_path / "prepared.wav"
    final_mix = tmp_path / "final-mix.wav"
    for path, value in (
        (narration, b"narration"),
        (source, b"source"),
        (metadata, b"{}"),
        (prepared, b"prepared"),
        (final_mix, b"mix"),
    ):
        path.write_bytes(value)
    track = BgmTrack(
        id="track",
        path=source,
        metadata_path=metadata,
        level="project",
        sha256=sha256_file(source),
        title="Track",
        creator="Composer",
        source_url="https://example.com/source",
        license="CC BY 4.0",
        rights_status="known",
        subjects=(),
        moods=(),
        energy="low",
        tempo_bpm=90,
        instrumental=True,
        template_tags=(),
        avoid_for=(),
        preferred_start_ms=0,
        loopable=True,
        metadata_sha256=hashlib.sha256(metadata.read_bytes()).hexdigest(),
        provider="wikimedia",
    )
    return BgmMixResult(
        narration_path=narration,
        bgm=track,
        prepared_bgm_path=prepared,
        mix_path=final_mix,
        narration_sha256=sha256_file(narration),
        bgm_sha256=sha256_file(source),
        prepared_bgm_sha256=sha256_file(prepared),
        mix_sha256=sha256_file(final_mix),
        narration_duration_ms=10_000,
        bgm_duration_ms=12_000,
        prepared_bgm_duration_ms=10_000,
        mix_duration_ms=10_020,
        measured_lufs=-16.1,
        true_peak_dbtp=-1.6,
        policy_hash="policy-hash",
        configuration_hash="config-hash",
        ffmpeg_version="ffmpeg version 7.0",
        command_parameters=(("ffmpeg", "-i", "source"),),
        settings=BgmMixSettings(),
        warnings=(),
    )


def test_mix_report_records_hash_chain_provenance_and_measurement(tmp_path):
    from videocreator.bgm_audit import write_bgm_mix_report

    result = make_result(tmp_path)
    path = tmp_path / "bgm-mix-report.json"

    report = write_bgm_mix_report(result, path)

    assert report["mode"] == "bgm"
    assert report["status"] == "passed"
    assert report["inputs"]["narration"]["sha256"] == result.narration_sha256
    assert report["inputs"]["bgm"]["sha256"] == result.bgm_sha256
    assert report["inputs"]["bgm"]["metadata_sha256"]
    assert report["outputs"]["prepared_bgm"]["sha256"] == result.prepared_bgm_sha256
    assert report["outputs"]["render_audio"]["sha256"] == result.mix_sha256
    assert report["policy_sha256"] == "policy-hash"
    assert report["configuration_sha256"] == "config-hash"
    assert report["measurement"]["integrated_lufs"] == -16.1
    assert report["provenance"]["source_url"] == "https://example.com/source"
    assert report["provenance"]["provider"] == "wikimedia"
    assert report["provenance"]["rights_status"] == "known"
    assert json.loads(path.read_text(encoding="utf-8")) == report


def test_mix_audit_rejects_stale_render_audio(tmp_path):
    from videocreator.bgm_audit import (
        audit_bgm_render_audio,
        write_bgm_mix_report,
    )

    result = make_result(tmp_path)
    report = write_bgm_mix_report(result, tmp_path / "report.json")
    render_audio = result.mix_path
    render_audio.write_bytes(render_audio.read_bytes() + b"changed")

    audit = audit_bgm_render_audio(render_audio, report)

    assert audit["status"] == "failed"
    assert "artifact_hash_mismatch" in finding_codes(audit)


def test_mix_audit_accepts_current_passing_report(tmp_path, monkeypatch):
    from videocreator.bgm_audit import (
        audit_bgm_render_audio,
        write_bgm_mix_report,
    )
    from videocreator.media import MediaMetadata

    result = make_result(tmp_path)
    report = write_bgm_mix_report(result, tmp_path / "report.json")
    monkeypatch.setattr(
        "videocreator.bgm_audit.probe_media",
        lambda _path: MediaMetadata("audio", "pcm", None, None, 10_020),
    )

    audit = audit_bgm_render_audio(result.mix_path, report)

    assert audit["status"] == "passed"


def test_mix_report_and_audit_reject_bad_measurement(tmp_path):
    from videocreator.bgm_audit import (
        audit_bgm_render_audio,
        write_bgm_mix_report,
    )

    result = replace(make_result(tmp_path), measured_lufs=-18.1, true_peak_dbtp=-0.9)
    report = write_bgm_mix_report(result, tmp_path / "report.json")
    audit = audit_bgm_render_audio(result.mix_path, report)

    assert report["status"] == "failed"
    assert audit["status"] == "failed"
    assert {"integrated_loudness_out_of_range", "true_peak_too_high"} <= finding_codes(
        audit
    )


def test_mix_audit_rejects_non_finite_true_peak(tmp_path):
    from videocreator.bgm_audit import (
        audit_bgm_render_audio,
        write_bgm_mix_report,
    )

    result = make_result(tmp_path)
    report = write_bgm_mix_report(result, tmp_path / "report.json")
    report["measurement"]["true_peak_dbtp"] = math.nan
    audit = audit_bgm_render_audio(result.mix_path, report)

    assert "true_peak_too_high" in finding_codes(audit)


def test_narration_only_report_binds_authoritative_audio(tmp_path, monkeypatch):
    from videocreator.bgm_audit import (
        audit_bgm_render_audio,
        write_narration_only_report,
    )
    from videocreator.media import MediaMetadata

    narration = tmp_path / "narration.wav"
    narration.write_bytes(b"narration")
    monkeypatch.setattr(
        "videocreator.bgm_audit.probe_media",
        lambda _path: MediaMetadata("audio", "pcm", None, None, 5000),
    )

    report = write_narration_only_report(
        narration, tmp_path / "report.json", ["No eligible BGM candidate"]
    )
    audit = audit_bgm_render_audio(narration, report)

    assert report["mode"] == "narration_only"
    assert report["outputs"]["render_audio"]["path"] == str(narration)
    assert report["warnings"] == ["No eligible BGM candidate"]
    assert audit["status"] == "passed"


def test_narration_only_audit_rejects_render_audio_other_than_narration(
    tmp_path, monkeypatch
):
    from videocreator.bgm_audit import (
        audit_bgm_render_audio,
        write_narration_only_report,
    )
    from videocreator.media import MediaMetadata

    narration = tmp_path / "narration.wav"
    narration.write_bytes(b"same-audio")
    copied = tmp_path / "copied.wav"
    copied.write_bytes(narration.read_bytes())
    monkeypatch.setattr(
        "videocreator.bgm_audit.probe_media",
        lambda _path: MediaMetadata("audio", "pcm", None, None, 5000),
    )
    report = write_narration_only_report(
        narration, tmp_path / "report.json", []
    )
    report["outputs"]["render_audio"]["path"] = str(copied)

    audit = audit_bgm_render_audio(copied, report)

    assert audit["status"] == "failed"
    assert "narration_only_not_narration" in finding_codes(audit)


def test_audit_rejects_mutated_bgm_metadata(tmp_path):
    from videocreator.bgm_audit import (
        audit_bgm_render_audio,
        write_bgm_mix_report,
    )

    result = make_result(tmp_path)
    report = write_bgm_mix_report(result, tmp_path / "report.json")
    result.bgm.metadata_path.write_bytes(b'{"changed":true}')

    audit = audit_bgm_render_audio(result.mix_path, report)

    assert audit["status"] == "failed"
    assert "bgm_metadata_hash_mismatch" in finding_codes(audit)


def test_unknown_rights_are_warning_only(tmp_path, monkeypatch):
    from videocreator.bgm_audit import (
        audit_bgm_render_audio,
        write_bgm_mix_report,
    )
    from videocreator.media import MediaMetadata

    result = make_result(tmp_path)
    object.__setattr__(result.bgm, "rights_status", "unknown")
    report = write_bgm_mix_report(result, tmp_path / "report.json")
    monkeypatch.setattr(
        "videocreator.bgm_audit.probe_media",
        lambda _path: MediaMetadata("audio", "pcm", None, None, 10_020),
    )

    audit = audit_bgm_render_audio(result.mix_path, report)

    assert audit["status"] == "passed"
    assert "rights status is unknown" in " ".join(audit["warnings"])


def test_audit_rejects_non_authoritative_audio_path(tmp_path):
    from videocreator.bgm_audit import (
        audit_bgm_render_audio,
        write_bgm_mix_report,
    )

    result = make_result(tmp_path)
    report = write_bgm_mix_report(result, tmp_path / "report.json")
    copy = tmp_path / "copied-mix.wav"
    copy.write_bytes(result.mix_path.read_bytes())

    audit = audit_bgm_render_audio(copy, report)

    assert audit["status"] == "failed"
    assert "artifact_path_mismatch" in finding_codes(audit)
