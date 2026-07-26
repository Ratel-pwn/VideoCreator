from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .media import probe_media
from .subtitle_alignment import normalize_visible_chars


SRT_TIME_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*"
    r"(?P<end>\d{2}:\d{2}:\d{2},\d{3})"
)
VISIBLE_CHAR_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


@dataclass(frozen=True)
class SyncThresholds:
    min_exact_match_coverage: float = 0.92
    max_character_error_rate: float = 0.18
    min_timing_coverage: float = 0.98
    max_boundary_drift_ms: int = 700
    max_unresolved_span_ms: int = 2000
    min_block_confidence: float = 0.35

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "SyncThresholds":
        if not value:
            return cls()
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value[key] for key in allowed if key in value})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp_ms(value: str) -> int:
    hours, minutes, remainder = value.split(":")
    seconds, milliseconds = remainder.split(",")
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1000
        + int(milliseconds)
    )


def parse_srt(path: Path) -> list[dict[str, Any]]:
    source = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    captions: list[dict[str, Any]] = []
    for raw_block in re.split(r"\n{2,}", source):
        lines = [line for line in raw_block.splitlines() if line.strip()]
        match = SRT_TIME_RE.search(raw_block)
        if not match or len(lines) < 3:
            continue
        captions.append({
            "index": int(lines[0]) if lines[0].isdigit() else len(captions) + 1,
            "start_ms": _timestamp_ms(match.group("start")),
            "end_ms": _timestamp_ms(match.group("end")),
            "text": " ".join(lines[2:]).strip(),
        })
    return captions


def _finding(
    code: str,
    message: str,
    *,
    evidence: dict[str, Any] | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "severity": "error",
        "code": code,
        "message": message,
    }
    if evidence:
        result["evidence"] = evidence
    if target:
        result["target"] = target
    return result


def _audit_segment_manifest(
    path: Path,
    audio_sha256: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    segments = payload.get("segments") or []
    ids = [str(item.get("id", "")) for item in segments]
    if len(ids) != len(set(ids)):
        findings.append(_finding(
            "segment_duplicate",
            "TTS segment manifest contains duplicate IDs",
        ))
    ordinals = [int(item.get("ordinal", 0)) for item in segments]
    if ordinals != sorted(ordinals) or ordinals != list(range(1, len(segments) + 1)):
        findings.append(_finding(
            "segment_order_mismatch",
            "TTS segment ordinals are missing or out of order",
            evidence={"ordinals": ordinals},
        ))
    for item in segments:
        segment_path = Path(str(item.get("audio_path", "")))
        if not segment_path.is_file():
            findings.append(_finding(
                "segment_missing",
                f"TTS segment audio is missing: {segment_path}",
                target=str(item.get("id", "")),
            ))
            continue
        expected = str(item.get("audio_sha256", ""))
        actual = sha256_file(segment_path)
        if expected and expected != actual:
            findings.append(_finding(
                "audio_decode_failure",
                f"TTS segment hash changed: {segment_path}",
                target=str(item.get("id", "")),
                evidence={"expected": expected, "actual": actual},
            ))
    expected_output = str(payload.get("output_sha256", ""))
    if expected_output and expected_output != audio_sha256:
        findings.append(_finding(
            "artifact_hash_mismatch",
            "Assembled narration does not match the TTS segment manifest",
            evidence={"expected": expected_output, "actual": audio_sha256},
        ))
    return findings


def _segment_for_source_index(
    manifest_path: Path | None,
    source_index: int | None,
) -> str | None:
    if manifest_path is None or source_index is None:
        return None
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    cursor = 0
    for segment in sorted(
        payload.get("segments") or [],
        key=lambda item: int(item.get("ordinal", 0)),
    ):
        count = len(VISIBLE_CHAR_RE.findall(str(segment.get("text", ""))))
        if cursor <= source_index < cursor + count:
            return str(segment.get("id", "")) or None
        cursor += count
    return None


def audit_subtitle_sync(
    audio: Path,
    srt: Path,
    alignment_report: Path,
    *,
    thresholds: SyncThresholds,
    segment_manifest: Path | None = None,
    approved_text: Path | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    audio_hash = sha256_file(audio)
    srt_hash = sha256_file(srt)
    audio_duration_ms: int | None = None
    try:
        audio_duration_ms = probe_media(audio).duration_ms
    except Exception as exc:
        findings.append(_finding(
            "audio_decode_failure",
            "Narration audio could not be decoded",
            evidence={"error": str(exc)},
            target="audio",
        ))
    report = json.loads(alignment_report.read_text(encoding="utf-8-sig"))
    if report.get("audio_sha256") != audio_hash:
        findings.append(_finding(
            "artifact_hash_mismatch",
            "Alignment report was not generated from the current narration audio",
            evidence={
                "expected": report.get("audio_sha256"),
                "actual": audio_hash,
            },
            target="audio",
        ))
    if report.get("srt_sha256") != srt_hash:
        findings.append(_finding(
            "artifact_hash_mismatch",
            "Alignment report was not generated from the current SRT",
            evidence={"expected": report.get("srt_sha256"), "actual": srt_hash},
            target="srt",
        ))

    captions = parse_srt(srt)
    approved_path = (
        Path(approved_text)
        if approved_text is not None
        else Path(str(report.get("approved_text_path", "")))
        if report.get("approved_text_path")
        else None
    )
    expected_approved_hash = report.get("approved_text_sha256")
    if approved_path is not None:
        if not approved_path.is_file():
            findings.append(_finding(
                "approved_text_missing",
                "Approved narration text is missing",
                target="approved_text",
            ))
        else:
            actual_approved_hash = sha256_file(approved_path)
            if expected_approved_hash != actual_approved_hash:
                findings.append(_finding(
                    "approved_text_hash_mismatch",
                    "Alignment report was not generated from the current approved text",
                    evidence={
                        "expected": expected_approved_hash,
                        "actual": actual_approved_hash,
                    },
                    target="approved_text",
                ))
            approved_normalized = "".join(
                normalize_visible_chars(
                    approved_path.read_text(encoding="utf-8-sig")
                )
            )
            subtitle_normalized = "".join(
                normalize_visible_chars(
                    " ".join(caption["text"] for caption in captions)
                )
            )
            if approved_normalized != subtitle_normalized:
                findings.append(_finding(
                    "approved_text_mismatch",
                    "Final SRT text does not contain the complete approved narration",
                    evidence={
                        "approved_character_count": len(approved_normalized),
                        "subtitle_character_count": len(subtitle_normalized),
                    },
                    target="srt",
                ))
    elif expected_approved_hash is not None:
        findings.append(_finding(
            "approved_text_missing",
            "Alignment report does not identify its approved narration text",
            target="approved_text",
        ))
    previous_end = 0
    for caption in captions:
        if caption["end_ms"] <= caption["start_ms"]:
            findings.append(_finding(
                "subtitle_boundary_drift",
                "Subtitle has an empty or negative timing range",
                target=str(caption["index"]),
            ))
        if caption["start_ms"] < previous_end:
            findings.append(_finding(
                "subtitle_overlap",
                "Subtitle ranges overlap",
                target=str(caption["index"]),
                evidence={
                    "start_ms": caption["start_ms"],
                    "previous_end_ms": previous_end,
                },
            ))
        previous_end = max(previous_end, caption["end_ms"])
        if (
            audio_duration_ms is not None
            and caption["end_ms"] > audio_duration_ms + 100
        ):
            findings.append(_finding(
                "subtitle_boundary_drift",
                "Subtitle extends beyond the narration audio",
                target=str(caption["index"]),
                evidence={
                    "subtitle_end_ms": caption["end_ms"],
                    "audio_duration_ms": audio_duration_ms,
                },
            ))

    exact_coverage = float(report.get("exact_match_coverage", 0.0))
    character_error_rate = float(report.get("character_error_rate", 1.0))
    timing_coverage = float(report.get("timing_coverage", 0.0))
    unresolved_span = report.get("max_unresolved_span_ms")
    if (
        isinstance(unresolved_span, bool)
        or not isinstance(unresolved_span, (int, float))
        or not math.isfinite(float(unresolved_span))
        or float(unresolved_span) < 0
    ):
        if approved_path is not None:
            findings.append(_finding(
                "alignment_evidence_missing",
                "Alignment report does not declare maximum unresolved timing span",
            ))
        unresolved_span = 0
    if float(unresolved_span) > thresholds.max_unresolved_span_ms:
        findings.append(_finding(
            "unresolved_span_too_long",
            "Approved text has an unresolved timing span beyond the allowed maximum",
            evidence={
                "actual_ms": int(unresolved_span),
                "maximum_ms": thresholds.max_unresolved_span_ms,
            },
        ))
    unmatched_spans = report.get("unmatched_approved_spans") or []
    mismatch_source_index = (
        int(unmatched_spans[0]["start_index"]) if unmatched_spans else None
    )
    mismatch_target = _segment_for_source_index(
        segment_manifest,
        mismatch_source_index,
    )
    if exact_coverage < thresholds.min_exact_match_coverage:
        findings.append(_finding(
            "text_content_mismatch",
            "Recognized narration does not cover enough approved text",
            evidence={
                "actual": exact_coverage,
                "required": thresholds.min_exact_match_coverage,
            },
            target=mismatch_target,
        ))
    if character_error_rate > thresholds.max_character_error_rate:
        findings.append(_finding(
            "text_content_mismatch",
            "Narration character error rate exceeds the allowed threshold",
            evidence={
                "actual": character_error_rate,
                "maximum": thresholds.max_character_error_rate,
            },
            target=mismatch_target,
        ))
    if timing_coverage < thresholds.min_timing_coverage:
        findings.append(_finding(
            "subtitle_boundary_drift",
            "Too much approved text lacks resolved speech timing",
            evidence={
                "actual": timing_coverage,
                "required": thresholds.min_timing_coverage,
            },
        ))
    for block in report.get("blocks") or []:
        drift = abs(int(block.get("boundary_drift_ms", 0)))
        if drift > thresholds.max_boundary_drift_ms:
            findings.append(_finding(
                "subtitle_boundary_drift",
                "Subtitle boundary drift exceeds the allowed threshold",
                target=str(block.get("index", "")),
                evidence={
                    "actual_ms": drift,
                    "maximum_ms": thresholds.max_boundary_drift_ms,
                },
            ))
        confidence = float(block.get("confidence", 1.0))
        if confidence < thresholds.min_block_confidence:
            findings.append(_finding(
                "asr_low_confidence",
                "Speech recognition confidence is too low for this subtitle",
                target=str(block.get("index", "")),
                evidence={
                    "actual": confidence,
                    "minimum": thresholds.min_block_confidence,
                },
            ))

    if segment_manifest:
        findings.extend(_audit_segment_manifest(segment_manifest, audio_hash))

    return {
        "schema_version": 1,
        "status": "failed" if findings else "passed",
        "thresholds": asdict(thresholds),
        "inputs": {
            "audio_path": str(audio),
            "audio_sha256": audio_hash,
            "srt_path": str(srt),
            "srt_sha256": srt_hash,
            "alignment_report_path": str(alignment_report),
            "alignment_report_sha256": sha256_file(alignment_report),
            "segment_manifest_path": (
                str(segment_manifest) if segment_manifest else None
            ),
        },
        "metrics": {
            "audio_duration_ms": audio_duration_ms,
            "subtitle_count": len(captions),
            "exact_match_coverage": exact_coverage,
            "character_error_rate": character_error_rate,
            "timing_coverage": timing_coverage,
            "max_unresolved_span_ms": int(unresolved_span),
        },
        "findings": findings,
    }
