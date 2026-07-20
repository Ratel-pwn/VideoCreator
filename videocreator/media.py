from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
