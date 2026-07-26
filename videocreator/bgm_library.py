from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .media import probe_media
from .templates import TemplateDefinition


SUPPORTED_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
REQUIRED_METADATA_FIELDS = {
    "schema_version", "id", "title", "subjects", "moods", "energy", "instrumental"
}


@dataclass(frozen=True)
class BgmTrack:
    id: str
    path: Path
    metadata_path: Path
    level: str
    sha256: str
    title: str
    creator: str | None
    source_url: str | None
    license: str | None
    rights_status: str
    subjects: tuple[str, ...]
    moods: tuple[str, ...]
    energy: str
    tempo_bpm: float | None
    instrumental: bool
    template_tags: tuple[str, ...]
    avoid_for: tuple[str, ...]
    preferred_start_ms: int
    loopable: bool
    metadata_sha256: str = ""
    provider: str | None = None
    duration_ms: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.duration_ms, bool)
            or not isinstance(self.duration_ms, int)
            or self.duration_ms < 0
        ):
            raise ValueError("duration_ms must be a non-negative integer")
        object.__setattr__(
            self,
            "provider",
            normalize_bgm_provider(self.provider),
        )
        object.__setattr__(
            self,
            "rights_status",
            normalize_bgm_rights_status(self.rights_status),
        )


@dataclass(frozen=True)
class BgmLibrarySelection:
    level: str
    root: Path | None
    tracks: tuple[BgmTrack, ...]
    warnings: tuple[str, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{field} must be a list of non-empty strings")
    return tuple(value)


def _optional_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value or None


def normalize_bgm_provider(value: Any) -> str | None:
    provider = _optional_string(value, "provider")
    if provider is None:
        return None
    return provider.strip() or None


def normalize_bgm_rights_status(value: Any) -> str:
    return str(value if value is not None else "unknown").strip() or "unknown"


def _source_url(value: Any) -> str | None:
    normalized = _optional_string(value, "source_url")
    if normalized is None:
        return None
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("source_url must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("source_url must not contain userinfo")
    if parsed.query or parsed.fragment:
        raise ValueError("source_url must not contain query or fragment")
    return normalized


def _load_track(audio_path: Path, level: str) -> BgmTrack:
    metadata_path = audio_path.with_suffix(".bgm.json")
    if not metadata_path.is_file():
        raise ValueError("metadata sidecar is missing")
    try:
        raw = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"metadata sidecar is invalid: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("metadata sidecar must contain an object")

    missing = sorted(REQUIRED_METADATA_FIELDS - set(raw))
    if missing:
        raise ValueError(f"metadata sidecar is missing required fields: {', '.join(missing)}")
    if raw["schema_version"] != 1:
        raise ValueError("unsupported metadata schema_version")
    if not isinstance(raw["id"], str) or not raw["id"]:
        raise ValueError("id must be a non-empty string")
    if not isinstance(raw["title"], str) or not raw["title"]:
        raise ValueError("title must be a non-empty string")
    if not isinstance(raw["energy"], str) or not raw["energy"]:
        raise ValueError("energy must be a non-empty string")
    if not isinstance(raw["instrumental"], bool):
        raise ValueError("instrumental must be a boolean")

    metadata = probe_media(audio_path)
    if metadata.kind != "audio" or metadata.duration_ms <= 0:
        raise ValueError("audio file is not a decodable audio track")

    tempo_raw = raw.get("tempo_bpm")
    if tempo_raw is not None and (isinstance(tempo_raw, bool) or not isinstance(tempo_raw, (int, float))):
        raise ValueError("tempo_bpm must be a number or null")
    preferred_start_raw = raw.get("preferred_start_ms", 0)
    if isinstance(preferred_start_raw, bool) or not isinstance(preferred_start_raw, int) or preferred_start_raw < 0:
        raise ValueError("preferred_start_ms must be a non-negative integer")
    loopable = raw.get("loopable", True)
    if not isinstance(loopable, bool):
        raise ValueError("loopable must be a boolean")

    return BgmTrack(
        id=raw["id"],
        path=audio_path,
        metadata_path=metadata_path,
        level=level,
        sha256=_sha256(audio_path),
        title=raw["title"],
        creator=_optional_string(raw.get("creator"), "creator"),
        source_url=_source_url(raw.get("source_url")),
        provider=normalize_bgm_provider(raw.get("provider")),
        license=_optional_string(raw.get("license"), "license"),
        rights_status=normalize_bgm_rights_status(raw.get("rights_status")),
        subjects=_as_string_tuple(raw["subjects"], "subjects"),
        moods=_as_string_tuple(raw["moods"], "moods"),
        energy=raw["energy"],
        tempo_bpm=float(tempo_raw) if tempo_raw is not None else None,
        instrumental=raw["instrumental"],
        template_tags=_as_string_tuple(raw.get("template_tags", []), "template_tags"),
        avoid_for=_as_string_tuple(raw.get("avoid_for", []), "avoid_for"),
        preferred_start_ms=preferred_start_raw,
        loopable=loopable,
        metadata_sha256=_sha256(metadata_path),
        duration_ms=metadata.duration_ms,
    )


def load_bgm_directory(root: Path, level: str) -> tuple[tuple[BgmTrack, ...], tuple[str, ...]]:
    if not root.is_dir():
        return (), ()
    tracks: list[BgmTrack] = []
    warnings: list[str] = []
    for audio_path in sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_AUDIO_SUFFIXES
    ):
        try:
            track = _load_track(audio_path, level)
            tracks.append(track)
            if track.rights_status.strip().lower() == "unknown":
                warnings.append(
                    f"{level} BGM track {track.id} rights status is unknown"
                )
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            warnings.append(f"{level} BGM track {audio_path.name} is ineligible: {exc}")

    tracks_by_id: dict[str, list[BgmTrack]] = {}
    for track in tracks:
        tracks_by_id.setdefault(track.id, []).append(track)
    duplicate_ids = {
        track_id for track_id, grouped_tracks in tracks_by_id.items()
        if len(grouped_tracks) > 1
    }
    for track_id in sorted(duplicate_ids):
        filenames = ", ".join(
            sorted(track.path.name for track in tracks_by_id[track_id])
        )
        warnings.append(
            f"{level} BGM track id {track_id} is duplicated by "
            f"{filenames}; all duplicates are ineligible"
        )
    eligible_tracks = tuple(
        track for track in tracks if track.id not in duplicate_ids
    )
    return eligible_tracks, tuple(warnings)


def resolve_bgm_library(
    repo_root: Path,
    project_root: Path,
    template: TemplateDefinition,
) -> BgmLibrarySelection:
    candidates = (
        ("project", project_root / "library" / "bgm"),
        ("template", template.root / "library" / "bgm"),
        ("global", repo_root / "library" / "bgm" / "default"),
    )
    accumulated_warnings: list[str] = []
    for level, root in candidates:
        tracks, warnings = load_bgm_directory(root, level)
        accumulated_warnings.extend(warnings)
        if tracks:
            return BgmLibrarySelection(
                level, root, tuple(sorted(tracks, key=lambda item: item.id)),
                tuple(accumulated_warnings),
            )
    return BgmLibrarySelection("none", None, (), tuple(accumulated_warnings))
