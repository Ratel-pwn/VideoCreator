from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class MediaMetadata:
    kind: str
    codec: str
    width: int | None
    height: int | None
    duration_ms: int


def parse_ffprobe_json(payload: dict[str, Any]) -> MediaMetadata:
    streams = payload.get("streams", [])
    stream = next((value for value in streams if value.get("codec_type") == "video"), None)
    kind = "video" if stream else "audio"
    source = stream or next(
        (value for value in streams if value.get("codec_type") == "audio"), {}
    )
    duration = (
        source.get("duration")
        or (payload.get("format") or {}).get("duration")
        or 0
    )
    return MediaMetadata(
        kind=kind,
        codec=str(source.get("codec_name", "")),
        width=source.get("width"),
        height=source.get("height"),
        duration_ms=round(float(duration) * 1000),
    )


def probe_media(path: Path) -> MediaMetadata:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    metadata = parse_ffprobe_json(json.loads(completed.stdout))
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return MediaMetadata(
            kind="image",
            codec=metadata.codec,
            width=metadata.width,
            height=metadata.height,
            duration_ms=0,
        )
    return metadata


_SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)")
_SRT_TIME_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2},\d{3})"
)


def parse_trailing_silence(
    log: str, *, analysis_offset_ms: int, total_duration_ms: int
) -> int | None:
    starts = [float(value) for value in _SILENCE_START_RE.findall(log)]
    ends = [float(value) for value in _SILENCE_END_RE.findall(log)]
    if not starts or not ends:
        return None

    absolute_end_ms = analysis_offset_ms + round(ends[-1] * 1000)
    if abs(total_duration_ms - absolute_end_ms) > 500:
        return None
    return analysis_offset_ms + round(starts[-1] * 1000)


def detect_trailing_silence(
    audio_path: Path,
    *,
    total_duration_ms: int,
    analysis_window_ms: int = 120_000,
    runner: Callable[..., Any] = subprocess.run,
) -> int | None:
    analysis_offset_ms = max(0, total_duration_ms - analysis_window_ms)
    completed = runner(
        [
            "ffmpeg",
            "-hide_banner",
            "-ss",
            f"{analysis_offset_ms / 1000:.3f}",
            "-i",
            str(audio_path),
            "-af",
            "silencedetect=noise=-40dB:d=1.0",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return parse_trailing_silence(
        completed.stderr,
        analysis_offset_ms=analysis_offset_ms,
        total_duration_ms=total_duration_ms,
    )


def _timestamp_to_ms(value: str) -> int:
    hours, minutes, remainder = value.split(":")
    seconds, milliseconds = remainder.split(",")
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1000
        + int(milliseconds)
    )


def _ms_to_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def _clamp_srt(source: str, spoken_end_ms: int) -> str:
    normalized = source.replace("\r\n", "\n").strip()
    output: list[str] = []
    for block in re.split(r"\n{2,}", normalized):
        match = _SRT_TIME_RE.search(block)
        if match is None:
            continue
        start_ms = _timestamp_to_ms(match.group("start"))
        if start_ms >= spoken_end_ms:
            continue
        end_ms = min(_timestamp_to_ms(match.group("end")), spoken_end_ms)
        timing = f"{match.group('start')} --> {_ms_to_timestamp(end_ms)}"
        output.append(_SRT_TIME_RE.sub(timing, block, count=1))
    return "\n\n".join(output) + "\n"


def clean_audio_and_srt(
    audio_path: Path,
    subtitle_path: Path,
    output_dir: Path,
    *,
    spoken_end_ms: int,
    runner: Callable[..., Any] = subprocess.run,
    probe_duration: Callable[[Path], int] | None = None,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cleaned_audio = output_dir / "voice.cleaned.mp3"
    cleaned_srt = output_dir / "voice.cleaned.srt"

    duration_probe = probe_duration or (lambda path: probe_media(path).duration_ms)
    requested_duration_ms = spoken_end_ms
    actual_duration_ms = 0
    for _ in range(3):
        runner(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(audio_path),
                "-af",
                "apad=pad_dur=1",
                "-t",
                f"{requested_duration_ms / 1000:.3f}",
                "-c:a",
                "libmp3lame",
                str(cleaned_audio),
            ],
            check=True,
            capture_output=True,
        )
        actual_duration_ms = duration_probe(cleaned_audio)
        difference_ms = spoken_end_ms - actual_duration_ms
        if abs(difference_ms) <= 40:
            break
        requested_duration_ms = max(1, requested_duration_ms + difference_ms)
    else:
        raise RuntimeError(
            "Cleaned audio duration differs from spoken boundary: "
            f"{actual_duration_ms}ms vs {spoken_end_ms}ms"
        )
    source_srt = subtitle_path.read_text(encoding="utf-8-sig")
    cleaned_srt.write_text(_clamp_srt(source_srt, spoken_end_ms), encoding="utf-8")
    return cleaned_audio, cleaned_srt
