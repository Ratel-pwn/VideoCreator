from __future__ import annotations

import hashlib
import json
import math
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .bgm_library import BgmTrack
from .bgm_policy import BgmPolicy
from .media import probe_media


DUCKING = {
    "light": {"threshold": 0.05, "ratio": 4, "attack": 30, "release": 650},
    "medium": {"threshold": 0.03, "ratio": 8, "attack": 20, "release": 500},
    "strong": {"threshold": 0.02, "ratio": 12, "attack": 15, "release": 400},
}


class BgmMixError(RuntimeError):
    pass


@dataclass(frozen=True)
class BgmMixSettings:
    sample_rate: int = 48_000
    channel_layout: str = "stereo"
    crossfade_ms: int = 1_000
    bgm_gain_db: float = -16.0
    target_lufs: float = -16.0
    loudness_range: float = 11.0
    target_true_peak_dbtp: float = -1.5
    min_lufs: float = -18.0
    max_lufs: float = -14.0
    max_true_peak_dbtp: float = -1.0
    duration_tolerance_ms: int = 100
    output_codec: str = "pcm_s24le"


@dataclass(frozen=True)
class BgmMixResult:
    narration_path: Path
    bgm: BgmTrack
    prepared_bgm_path: Path
    mix_path: Path
    narration_sha256: str
    bgm_sha256: str
    prepared_bgm_sha256: str
    mix_sha256: str
    narration_duration_ms: int
    bgm_duration_ms: int
    prepared_bgm_duration_ms: int
    mix_duration_ms: int
    measured_lufs: float
    true_peak_dbtp: float
    policy_hash: str
    configuration_hash: str
    ffmpeg_version: str
    command_parameters: tuple[tuple[str, ...], ...]
    settings: BgmMixSettings
    warnings: tuple[str, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _number(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _standardize_filter(settings: BgmMixSettings) -> str:
    return (
        f"aresample={settings.sample_rate},"
        "aformat=sample_fmts=fltp:"
        f"channel_layouts={settings.channel_layout}"
    )


def build_bgm_filter(
    track_duration_ms: int,
    narration_duration_ms: int,
    policy: BgmPolicy,
) -> str:
    if track_duration_ms <= 0 or narration_duration_ms <= 0:
        raise BgmMixError("audio durations must be positive")

    settings = BgmMixSettings()
    narration_seconds = narration_duration_ms / 1000
    fade_in_ms = min(policy.fade_in_ms, narration_duration_ms)
    fade_out_ms = min(policy.fade_out_ms, narration_duration_ms)
    fade_out_start_ms = max(0, narration_duration_ms - fade_out_ms)
    tail_parts = [
        f"atrim=duration={_number(narration_seconds)}",
        "asetpts=PTS-STARTPTS",
    ]
    if fade_in_ms:
        tail_parts.append(f"afade=t=in:st=0:d={_number(fade_in_ms / 1000)}")
    if fade_out_ms:
        tail_parts.append(
            f"afade=t=out:st={_number(fade_out_start_ms / 1000)}:"
            f"d={_number(fade_out_ms / 1000)}"
        )
    tail = ",".join(tail_parts) + "[bgmout]"
    standardized = _standardize_filter(settings)

    if track_duration_ms >= narration_duration_ms:
        return f"[0:a]{standardized},{tail}"

    if track_duration_ms <= 1:
        raise BgmMixError("track is too short to loop")
    crossfade_ms = min(
        settings.crossfade_ms,
        max(1, min(track_duration_ms - 1, track_duration_ms // 4)),
    )
    repeat_count = math.ceil(
        (narration_duration_ms - crossfade_ms)
        / (track_duration_ms - crossfade_ms)
    )
    repeat_count = max(2, repeat_count)
    labels = "".join(f"[bgm{index}]" for index in range(repeat_count))
    parts = [f"[0:a]{standardized},asplit={repeat_count}{labels}"]
    previous = "[bgm0]"
    crossfade_seconds = _number(crossfade_ms / 1000)
    for index in range(1, repeat_count):
        output = f"[bgm_xf{index}]"
        parts.append(
            f"{previous}[bgm{index}]"
            f"acrossfade=d={crossfade_seconds}:c1=tri:c2=tri{output}"
        )
        previous = output
    parts.append(f"{previous}{tail}")
    return ";".join(parts)


def _run(
    runner: Callable[..., Any],
    command: list[str],
) -> Any:
    try:
        return runner(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        detail = str(stderr).strip()[-1000:]
        suffix = f": {detail}" if detail else ""
        raise BgmMixError(f"FFmpeg command failed{suffix}") from exc


def _parse_loudnorm(stderr: str) -> tuple[float, float]:
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for index, character in enumerate(stderr):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(stderr[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    for payload in reversed(candidates):
        lufs = payload.get("input_i")
        true_peak = payload.get("input_tp")
        try:
            measured_lufs = float(lufs)
            true_peak_dbtp = float(true_peak)
        except (TypeError, ValueError):
            continue
        if math.isfinite(measured_lufs) and math.isfinite(true_peak_dbtp):
            return measured_lufs, true_peak_dbtp
    raise BgmMixError("FFmpeg loudness analysis did not return valid JSON")


def _validate_audio(path: Path, label: str) -> int:
    if not path.is_file():
        raise BgmMixError(f"{label} does not exist: {path}")
    try:
        metadata = probe_media(path)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise BgmMixError(f"{label} is not decodable") from exc
    if metadata.kind != "audio" or metadata.duration_ms <= 0:
        raise BgmMixError(f"{label} is not decodable audio")
    return metadata.duration_ms


def _mix_graph(policy: BgmPolicy, settings: BgmMixSettings) -> str:
    ducking = DUCKING.get(policy.ducking_strength)
    if ducking is None:
        raise BgmMixError(
            f"Unsupported ducking strength: {policy.ducking_strength}"
        )
    narration_filter = _standardize_filter(settings)
    compressor = ":".join(
        (
            f"threshold={ducking['threshold']}",
            f"ratio={ducking['ratio']}",
            f"attack={ducking['attack']}",
            f"release={ducking['release']}",
        )
    )
    return ";".join(
        (
            f"[0:a]{narration_filter},asplit=2[narr_mix][narr_key]",
            f"[1:a]volume={_number(settings.bgm_gain_db)}dB[bgm_level]",
            f"[bgm_level][narr_key]sidechaincompress={compressor}[bgm_ducked]",
            "[narr_mix][bgm_ducked]"
            "amix=inputs=2:duration=first:normalize=0,"
            f"loudnorm=I={_number(settings.target_lufs)}:"
            f"LRA={_number(settings.loudness_range)}:"
            f"TP={_number(settings.target_true_peak_dbtp)}[mixout]",
        )
    )


def mix_bgm(
    narration: Path,
    bgm: BgmTrack,
    prepared_output: Path,
    mix_output: Path,
    policy: BgmPolicy,
    runner: Callable[..., Any],
) -> BgmMixResult:
    settings = BgmMixSettings()
    narration = Path(narration)
    prepared_output = Path(prepared_output)
    mix_output = Path(mix_output)
    narration_duration_ms = _validate_audio(narration, "narration")
    bgm_duration_ms = _validate_audio(bgm.path, "BGM source")

    preferred_start_ms = (
        bgm.preferred_start_ms
        if 0 <= bgm.preferred_start_ms < bgm_duration_ms
        else 0
    )
    available_bgm_duration_ms = bgm_duration_ms - preferred_start_ms
    if (
        available_bgm_duration_ms < narration_duration_ms
        and not bgm.loopable
    ):
        raise BgmMixError("track is too short and is not loopable")

    prepared_output.parent.mkdir(parents=True, exist_ok=True)
    mix_output.parent.mkdir(parents=True, exist_ok=True)
    preparation_filter = build_bgm_filter(
        available_bgm_duration_ms,
        narration_duration_ms,
        policy,
    )
    prepare_command = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{preferred_start_ms / 1000:.3f}",
        "-i",
        str(bgm.path),
        "-filter_complex",
        preparation_filter,
        "-map",
        "[bgmout]",
        "-ar",
        str(settings.sample_rate),
        "-ac",
        "2",
        "-c:a",
        settings.output_codec,
        str(prepared_output),
    ]
    _run(runner, prepare_command)
    prepared_duration_ms = _validate_audio(prepared_output, "prepared BGM")
    if abs(prepared_duration_ms - narration_duration_ms) > settings.duration_tolerance_ms:
        raise BgmMixError(
            "prepared BGM duration differs from narration duration: "
            f"{prepared_duration_ms}ms vs {narration_duration_ms}ms"
        )

    graph = _mix_graph(policy, settings)
    mix_command = [
        "ffmpeg",
        "-y",
        "-i",
        str(narration),
        "-i",
        str(prepared_output),
        "-filter_complex",
        graph,
        "-map",
        "[mixout]",
        "-t",
        f"{narration_duration_ms / 1000:.3f}",
        "-ar",
        str(settings.sample_rate),
        "-ac",
        "2",
        "-c:a",
        settings.output_codec,
        str(mix_output),
    ]
    _run(runner, mix_command)
    mix_duration_ms = _validate_audio(mix_output, "final mix")
    if abs(mix_duration_ms - narration_duration_ms) > settings.duration_tolerance_ms:
        raise BgmMixError(
            "final mix duration differs from narration duration: "
            f"{mix_duration_ms}ms vs {narration_duration_ms}ms"
        )

    version_command = ["ffmpeg", "-version"]
    version = _run(runner, version_command)
    ffmpeg_version = str(getattr(version, "stdout", "")).splitlines()
    ffmpeg_version_line = ffmpeg_version[0].strip() if ffmpeg_version else "unknown"

    analysis_command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(mix_output),
        "-af",
        f"loudnorm=I={_number(settings.target_lufs)}:"
        f"LRA={_number(settings.loudness_range)}:"
        f"TP={_number(settings.target_true_peak_dbtp)}:print_format=json",
        "-f",
        "null",
        "-",
    ]
    analysis = _run(runner, analysis_command)
    measured_lufs, true_peak_dbtp = _parse_loudnorm(
        str(getattr(analysis, "stderr", ""))
    )
    if not settings.min_lufs <= measured_lufs <= settings.max_lufs:
        raise BgmMixError(
            f"measured loudness is outside the allowed range: {measured_lufs} LUFS"
        )
    if true_peak_dbtp > settings.max_true_peak_dbtp:
        raise BgmMixError(
            f"measured true peak exceeds the allowed maximum: {true_peak_dbtp} dBTP"
        )

    warnings: list[str] = []
    if bgm.rights_status.strip().lower() == "unknown":
        warnings.append(f"BGM track {bgm.id} rights status is unknown")

    policy_payload = asdict(policy)
    settings_payload = asdict(settings)
    commands = (
        tuple(prepare_command),
        tuple(mix_command),
        tuple(analysis_command),
    )
    return BgmMixResult(
        narration_path=narration,
        bgm=bgm,
        prepared_bgm_path=prepared_output,
        mix_path=mix_output,
        narration_sha256=sha256_file(narration),
        bgm_sha256=sha256_file(bgm.path),
        prepared_bgm_sha256=sha256_file(prepared_output),
        mix_sha256=sha256_file(mix_output),
        narration_duration_ms=narration_duration_ms,
        bgm_duration_ms=bgm_duration_ms,
        prepared_bgm_duration_ms=prepared_duration_ms,
        mix_duration_ms=mix_duration_ms,
        measured_lufs=measured_lufs,
        true_peak_dbtp=true_peak_dbtp,
        policy_hash=_stable_hash(policy_payload),
        configuration_hash=_stable_hash(settings_payload),
        ffmpeg_version=ffmpeg_version_line,
        command_parameters=commands,
        settings=settings,
        warnings=tuple(warnings),
    )
