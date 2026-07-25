from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

from .bgm_mix import BgmMixResult, BgmMixSettings, sha256_file
from .media import probe_media


def _finding(code: str, message: str) -> dict[str, str]:
    return {"severity": "error", "code": code, "message": message}


def _measurement_findings(
    *,
    narration_duration_ms: int,
    mix_duration_ms: int,
    measured_lufs: float,
    true_peak_dbtp: float,
    settings: BgmMixSettings,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if abs(mix_duration_ms - narration_duration_ms) > settings.duration_tolerance_ms:
        findings.append(
            _finding(
                "duration_mismatch",
                "Render audio duration differs from narration by more than "
                f"{settings.duration_tolerance_ms}ms",
            )
        )
    if (
        not math.isfinite(measured_lufs)
        or not settings.min_lufs <= measured_lufs <= settings.max_lufs
    ):
        findings.append(
            _finding(
                "integrated_loudness_out_of_range",
                f"Integrated loudness {measured_lufs} LUFS is outside "
                f"{settings.min_lufs} to {settings.max_lufs} LUFS",
            )
        )
    if (
        not math.isfinite(true_peak_dbtp)
        or true_peak_dbtp > settings.max_true_peak_dbtp
    ):
        findings.append(
            _finding(
                "true_peak_too_high",
                f"True peak {true_peak_dbtp} dBTP exceeds "
                f"{settings.max_true_peak_dbtp} dBTP",
            )
        )
    return findings


def _artifact(path: Path, sha256: str, duration_ms: int) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": sha256,
        "duration_ms": duration_ms,
    }


def _write_report(report: dict[str, Any], path: Path) -> dict[str, Any]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return report


def write_bgm_mix_report(
    result: BgmMixResult,
    path: Path,
) -> dict[str, Any]:
    findings = _measurement_findings(
        narration_duration_ms=result.narration_duration_ms,
        mix_duration_ms=result.mix_duration_ms,
        measured_lufs=result.measured_lufs,
        true_peak_dbtp=result.true_peak_dbtp,
        settings=result.settings,
    )
    warnings = list(result.warnings)
    if result.bgm.rights_status.strip().lower() == "unknown" and not any(
        "rights status is unknown" in warning for warning in warnings
    ):
        warnings.append(f"BGM track {result.bgm.id} rights status is unknown")
    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": "bgm",
        "status": "passed" if not findings else "failed",
        "inputs": {
            "narration": _artifact(
                result.narration_path,
                result.narration_sha256,
                result.narration_duration_ms,
            ),
            "bgm": {
                **_artifact(
                    result.bgm.path,
                    result.bgm_sha256,
                    result.bgm_duration_ms,
                ),
                "metadata_path": str(result.bgm.metadata_path),
                "metadata_sha256": result.bgm.metadata_sha256,
                "id": result.bgm.id,
                "title": result.bgm.title,
                "level": result.bgm.level,
            },
        },
        "outputs": {
            "prepared_bgm": _artifact(
                result.prepared_bgm_path,
                result.prepared_bgm_sha256,
                result.prepared_bgm_duration_ms,
            ),
            "render_audio": _artifact(
                result.mix_path,
                result.mix_sha256,
                result.mix_duration_ms,
            ),
        },
        "policy_sha256": result.policy_hash,
        "configuration_sha256": result.configuration_hash,
        "settings": {
            "sample_rate": result.settings.sample_rate,
            "channel_layout": result.settings.channel_layout,
            "crossfade_ms": result.settings.crossfade_ms,
            "bgm_gain_db": result.settings.bgm_gain_db,
            "target_lufs": result.settings.target_lufs,
            "loudness_range": result.settings.loudness_range,
            "target_true_peak_dbtp": result.settings.target_true_peak_dbtp,
            "min_lufs": result.settings.min_lufs,
            "max_lufs": result.settings.max_lufs,
            "max_true_peak_dbtp": result.settings.max_true_peak_dbtp,
            "duration_tolerance_ms": result.settings.duration_tolerance_ms,
            "output_codec": result.settings.output_codec,
        },
        "measurement": {
            "integrated_lufs": result.measured_lufs,
            "true_peak_dbtp": result.true_peak_dbtp,
        },
        "ffmpeg": {
            "version": result.ffmpeg_version,
            "commands": [list(command) for command in result.command_parameters],
        },
        "provenance": {
            "creator": result.bgm.creator,
            "source_url": result.bgm.source_url,
            "license": result.bgm.license,
            "rights_status": result.bgm.rights_status,
        },
        "warnings": warnings,
        "findings": findings,
    }
    return _write_report(report, path)


def write_narration_only_report(
    narration: Path,
    path: Path,
    warnings: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    narration = Path(narration)
    if not narration.is_file():
        raise ValueError(f"Narration does not exist: {narration}")
    try:
        metadata = probe_media(narration)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise ValueError("Narration is not decodable") from exc
    if metadata.kind != "audio" or metadata.duration_ms <= 0:
        raise ValueError("Narration is not decodable audio")
    artifact = _artifact(
        narration,
        sha256_file(narration),
        metadata.duration_ms,
    )
    report = {
        "schema_version": 1,
        "mode": "narration_only",
        "status": "passed",
        "inputs": {"narration": dict(artifact)},
        "outputs": {"render_audio": dict(artifact)},
        "warnings": list(warnings),
        "findings": [],
    }
    return _write_report(report, path)


def _load_report(report: dict[str, Any] | Path) -> dict[str, Any]:
    if isinstance(report, dict):
        return report
    try:
        value = json.loads(Path(report).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("BGM mix report is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("BGM mix report must contain an object")
    return value


def _get_artifact(
    report: dict[str, Any],
    section: str,
    name: str,
) -> dict[str, Any] | None:
    container = report.get(section)
    if not isinstance(container, dict):
        return None
    artifact = container.get(name)
    return artifact if isinstance(artifact, dict) else None


def audit_bgm_render_audio(
    render_audio: Path,
    report: dict[str, Any] | Path,
) -> dict[str, Any]:
    payload = _load_report(report)
    findings: list[dict[str, str]] = []
    mode = payload.get("mode")
    if mode not in {"bgm", "narration_only"}:
        findings.append(_finding("invalid_mode", "Report mode is invalid"))
    if payload.get("status") != "passed":
        findings.append(
            _finding(
                "mix_report_failed",
                "BGM mix report did not pass its own validation",
            )
        )

    expected_render = _get_artifact(payload, "outputs", "render_audio")
    narration_artifact = _get_artifact(payload, "inputs", "narration")
    if mode == "narration_only":
        narration_path = (
            narration_artifact.get("path")
            if narration_artifact is not None
            else None
        )
        render_path = (
            expected_render.get("path") if expected_render is not None else None
        )
        narration_hash = (
            narration_artifact.get("sha256")
            if narration_artifact is not None
            else None
        )
        render_hash = (
            expected_render.get("sha256")
            if expected_render is not None
            else None
        )
        same_path = (
            isinstance(narration_path, str)
            and isinstance(render_path, str)
            and Path(narration_path).resolve() == Path(render_path).resolve()
        )
        if not same_path or narration_hash != render_hash:
            findings.append(
                _finding(
                    "narration_only_not_narration",
                    "Narration-only render audio must be the audited narration stem",
                )
            )
    if expected_render is None:
        findings.append(
            _finding(
                "missing_render_audio",
                "Report does not declare an authoritative render audio artifact",
            )
        )
    else:
        render_audio = Path(render_audio)
        declared_path = expected_render.get("path")
        if (
            not isinstance(declared_path, str)
            or render_audio.resolve() != Path(declared_path).resolve()
        ):
            findings.append(
                _finding(
                    "artifact_path_mismatch",
                    "Render audio path is not the authoritative audited path",
                )
            )
        if not render_audio.is_file():
            findings.append(
                _finding("render_audio_missing", "Render audio does not exist")
            )
        else:
            expected_hash = expected_render.get("sha256")
            actual_hash = sha256_file(render_audio)
            if expected_hash != actual_hash:
                findings.append(
                    _finding(
                        "artifact_hash_mismatch",
                        "Render audio hash does not match the audited artifact",
                    )
                )
            else:
                try:
                    metadata = probe_media(render_audio)
                except (OSError, ValueError, subprocess.CalledProcessError):
                    findings.append(
                        _finding(
                            "render_audio_decode_failed",
                            "Render audio is not decodable",
                        )
                    )
                else:
                    if metadata.kind != "audio" or metadata.duration_ms <= 0:
                        findings.append(
                            _finding(
                                "render_audio_decode_failed",
                                "Render audio is not decodable audio",
                            )
                        )
                    expected_duration = expected_render.get("duration_ms")
                    if (
                        isinstance(expected_duration, int)
                        and abs(metadata.duration_ms - expected_duration) > 100
                    ):
                        findings.append(
                            _finding(
                                "artifact_duration_mismatch",
                                "Render audio duration differs from its report",
                            )
                        )

    artifact_locations = (
        ("inputs", "narration", "narration_hash_mismatch"),
        ("inputs", "bgm", "bgm_hash_mismatch"),
        ("outputs", "prepared_bgm", "prepared_bgm_hash_mismatch"),
    )
    for section, name, code in artifact_locations:
        artifact = _get_artifact(payload, section, name)
        if artifact is None:
            if mode == "bgm" or name == "narration":
                findings.append(
                    _finding(f"missing_{name}", f"Report is missing {name}")
                )
            continue
        artifact_path = artifact.get("path")
        expected_hash = artifact.get("sha256")
        if not isinstance(artifact_path, str) or not isinstance(expected_hash, str):
            findings.append(
                _finding(f"invalid_{name}", f"Report has invalid {name} metadata")
            )
            continue
        source = Path(artifact_path)
        if not source.is_file() or sha256_file(source) != expected_hash:
            findings.append(_finding(code, f"{name} hash does not match report"))

    if mode == "bgm":
        bgm_artifact = _get_artifact(payload, "inputs", "bgm") or {}
        metadata_path = bgm_artifact.get("metadata_path")
        metadata_sha256 = bgm_artifact.get("metadata_sha256")
        if not isinstance(metadata_path, str) or not isinstance(
            metadata_sha256, str
        ):
            findings.append(
                _finding(
                    "missing_bgm_metadata",
                    "BGM report does not bind its metadata sidecar",
                )
            )
        else:
            metadata = Path(metadata_path)
            if not metadata.is_file() or sha256_file(metadata) != metadata_sha256:
                findings.append(
                    _finding(
                        "bgm_metadata_hash_mismatch",
                        "BGM metadata hash does not match report",
                    )
                )
        provenance = payload.get("provenance")
        if not isinstance(provenance, dict) or not isinstance(
            provenance.get("rights_status"), str
        ):
            findings.append(
                _finding(
                    "missing_bgm_provenance",
                    "BGM report does not contain rights provenance",
                )
            )
        elif bgm_artifact.get("level") == "online" and not provenance.get(
            "source_url"
        ):
            findings.append(
                _finding(
                    "missing_online_source",
                    "Online BGM does not declare its public source URL",
                )
            )
        if not isinstance(payload.get("policy_sha256"), str) or not isinstance(
            payload.get("configuration_sha256"), str
        ):
            findings.append(
                _finding(
                    "missing_mix_configuration",
                    "BGM report does not bind policy and core configuration",
                )
            )
        ffmpeg = payload.get("ffmpeg")
        if (
            not isinstance(ffmpeg, dict)
            or not isinstance(ffmpeg.get("version"), str)
            or not isinstance(ffmpeg.get("commands"), list)
        ):
            findings.append(
                _finding(
                    "missing_ffmpeg_evidence",
                    "BGM report does not contain FFmpeg evidence",
                )
            )
        narration = _get_artifact(payload, "inputs", "narration") or {}
        measurement = payload.get("measurement")
        if not isinstance(measurement, dict) or expected_render is None:
            findings.append(
                _finding(
                    "missing_measurement",
                    "BGM report does not contain loudness measurement",
                )
            )
        else:
            try:
                raw_settings = payload.get("settings")
                if not isinstance(raw_settings, dict):
                    raise ValueError("missing settings")
                settings = BgmMixSettings(
                    sample_rate=int(raw_settings["sample_rate"]),
                    channel_layout=str(raw_settings["channel_layout"]),
                    crossfade_ms=int(raw_settings["crossfade_ms"]),
                    bgm_gain_db=float(raw_settings["bgm_gain_db"]),
                    target_lufs=float(raw_settings["target_lufs"]),
                    loudness_range=float(raw_settings["loudness_range"]),
                    target_true_peak_dbtp=float(
                        raw_settings["target_true_peak_dbtp"]
                    ),
                    min_lufs=float(raw_settings["min_lufs"]),
                    max_lufs=float(raw_settings["max_lufs"]),
                    max_true_peak_dbtp=float(
                        raw_settings["max_true_peak_dbtp"]
                    ),
                    duration_tolerance_ms=int(
                        raw_settings["duration_tolerance_ms"]
                    ),
                    output_codec=str(raw_settings["output_codec"]),
                )
                findings.extend(
                    _measurement_findings(
                        narration_duration_ms=int(narration["duration_ms"]),
                        mix_duration_ms=int(expected_render["duration_ms"]),
                        measured_lufs=float(measurement["integrated_lufs"]),
                        true_peak_dbtp=float(measurement["true_peak_dbtp"]),
                        settings=settings,
                    )
                )
            except (KeyError, TypeError, ValueError):
                findings.append(
                    _finding(
                        "invalid_measurement",
                        "BGM report contains invalid measurement values",
                    )
                )

    warnings = list(payload.get("warnings", []))
    provenance = payload.get("provenance")
    if (
        mode == "bgm"
        and isinstance(provenance, dict)
        and str(provenance.get("rights_status", "")).strip().lower() == "unknown"
        and not any("rights status is unknown" in warning for warning in warnings)
    ):
        warnings.append("BGM rights status is unknown")
    return {
        "schema_version": 1,
        "mode": mode,
        "status": "passed" if not findings else "failed",
        "render_audio": str(render_audio),
        "findings": findings,
        "warnings": warnings,
    }
