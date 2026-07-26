from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from .bgm_library import BgmTrack, SUPPORTED_AUDIO_SUFFIXES
from .durable_io import atomic_write_json, fsync_directory
from .bgm_policy import BgmPolicy
from .bgm_search import (
    DEFAULT_MAX_DOWNLOAD_BYTES,
    MAX_AGENT_CANDIDATES,
    MAX_AGENT_RESPONSE_BYTES,
    BgmSearchError,
    OnlineBgmCandidate,
    candidate_to_track,
    download_candidate,
    parse_agent_candidates,
    search_configured_providers,
)
from .bgm_selection import (
    BgmQuery,
    CandidateScore,
    SelectionResult,
    select_bgm_candidate,
)
from .interactions import (
    InteractionContext,
    InteractionPort,
    InteractionRequired,
    clear_interaction_state,
    interaction_fingerprint,
)


INTERACTION_KEY = "bgm-online-candidates"
_SENSITIVE_CONFIG_TERMS = (
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
)
_CANDIDATE_ARTIFACT = re.compile(
    r"^(?:candidate-(?P<final>[0-9a-f]{64})(?P<suffix>\.[a-z0-9]+)"
    r"|\.(?:candidate)-(?P<partial>[0-9a-f]{64})"
    r"(?P<partial_suffix>\.[a-z0-9]+)\.part)$"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_json(path, value)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid BGM durability ledger: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid BGM durability ledger: {path}")
    return value


def _safe_provider_config(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _safe_provider_config(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not any(
                term in str(key).casefold() for term in _SENSITIVE_CONFIG_TERMS
            )
        }
    if isinstance(value, list):
        return [_safe_provider_config(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(type(value).__name__)


@dataclass(frozen=True)
class BgmResolutionRequest:
    context: InteractionContext
    local_tracks: tuple[BgmTrack, ...]
    query: BgmQuery
    policy: BgmPolicy
    provider_config: dict[str, Any]
    download_dir: Path
    max_agent_candidates: int = MAX_AGENT_CANDIDATES
    max_agent_response_bytes: int = MAX_AGENT_RESPONSE_BYTES

    def __post_init__(self) -> None:
        download_dir = Path(self.download_dir).resolve()
        try:
            download_dir.relative_to(self.context.run_dir.resolve())
        except ValueError as exc:
            raise ValueError("BGM download directory must stay inside the run") from exc
        object.__setattr__(self, "download_dir", download_dir)
        object.__setattr__(self, "local_tracks", tuple(self.local_tracks))
        if not 1 <= self.max_agent_candidates <= MAX_AGENT_CANDIDATES:
            raise ValueError("Invalid BGM Agent candidate limit")
        if not 1 <= self.max_agent_response_bytes <= MAX_AGENT_RESPONSE_BYTES:
            raise ValueError("Invalid BGM Agent response byte limit")


@dataclass(frozen=True)
class BgmResolution:
    mode: str
    source: str
    track: BgmTrack | None
    scores: tuple[CandidateScore, ...]
    warnings: tuple[str, ...]
    resolution_id: str
    request_fingerprint: str
    interaction_id: str | None
    interaction_fingerprint: str | None


def _request_fingerprint(request: BgmResolutionRequest) -> str:
    return _canonical_hash(
        {
            "project_name": request.context.project_name,
            "run_id": request.context.run_id,
            "query": asdict(request.query),
            "policy": asdict(request.policy),
            "local_tracks": [
                {
                    "id": track.id,
                    "sha256": track.sha256,
                    "metadata_sha256": track.metadata_sha256,
                    "level": track.level,
                }
                for track in sorted(
                    request.local_tracks,
                    key=lambda item: (
                        item.id,
                        item.sha256,
                        item.metadata_sha256,
                    ),
                )
            ],
            "provider_config": _safe_provider_config(request.provider_config),
            "agent_limits": {
                "max_candidates": request.max_agent_candidates,
                "max_response_bytes": request.max_agent_response_bytes,
            },
        }
    )


def candidate_fingerprint(candidate: OnlineBgmCandidate) -> str:
    return _canonical_hash(asdict(candidate))


def _redact_url(value: str | None) -> str | None:
    if not value:
        return value
    parsed = urlsplit(value)
    hostname = parsed.hostname
    if not hostname:
        return None
    host = f"[{hostname}]" if ":" in hostname else hostname
    try:
        port = parsed.port
    except ValueError:
        port = None
    authority = f"{host}:{port}" if port is not None else host
    return urlunsplit((parsed.scheme, authority, parsed.path, "", ""))


def _relative_artifact(request: BgmResolutionRequest, path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(request.download_dir).as_posix()
    except ValueError as exc:
        raise RuntimeError("BGM ledger artifact must stay inside download_dir") from exc


def _resolve_artifact(request: BgmResolutionRequest, value: str) -> Path:
    raw = Path(value)
    if raw.is_absolute():
        raise RuntimeError("BGM ledger path must be relative")
    resolved = (request.download_dir / raw).resolve()
    try:
        resolved.relative_to(request.download_dir)
    except ValueError as exc:
        raise RuntimeError("BGM ledger path escapes download_dir") from exc
    return resolved


def _track_to_dict(
    request: BgmResolutionRequest,
    track: BgmTrack,
) -> dict[str, Any]:
    value = asdict(track)
    value["path"] = _relative_artifact(request, track.path)
    value["metadata_path"] = _relative_artifact(request, track.metadata_path)
    value["source_url"] = _redact_url(track.source_url)
    return value


def _track_from_dict(
    request: BgmResolutionRequest,
    value: dict[str, Any],
) -> BgmTrack:
    tuple_fields = (
        "subjects",
        "moods",
        "template_tags",
        "avoid_for",
    )
    normalized = dict(value)
    normalized["path"] = _resolve_artifact(request, normalized["path"])
    normalized["metadata_path"] = _resolve_artifact(
        request,
        normalized["metadata_path"],
    )
    normalized.setdefault("provider", None)
    for field in tuple_fields:
        normalized[field] = tuple(normalized.get(field, ()))
    return BgmTrack(**normalized)


def _score_to_dict(score: CandidateScore) -> dict[str, Any]:
    return {
        "track_id": score.track_id,
        "total": score.total,
        "eligible": score.eligible,
        "components": score.components,
        "rejection_reasons": list(score.rejection_reasons),
    }


def _score_from_dict(value: dict[str, Any]) -> CandidateScore:
    return CandidateScore(
        track_id=str(value["track_id"]),
        total=float(value["total"]),
        eligible=bool(value["eligible"]),
        components={
            str(key): float(component)
            for key, component in value.get("components", {}).items()
        },
        rejection_reasons=tuple(value.get("rejection_reasons", ())),
    )


def _query_payload(
    query: BgmQuery,
    max_candidates: int = MAX_AGENT_CANDIDATES,
    max_response_bytes: int = MAX_AGENT_RESPONSE_BYTES,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "limits": {"max_response_bytes": max_response_bytes},
        "query": {
            "subjects": list(query.subjects),
            "moods": list(query.moods),
            "template_id": query.template_id,
            "terms_zh": list(query.terms_zh),
            "terms_en": list(query.terms_en),
        },
        "response_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["candidates"],
            "properties": {
                "candidates": {
                    "type": "array",
                    "maxItems": max_candidates,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "title",
                            "source_page_url",
                            "download_url",
                            "provider",
                        ],
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "creator": {"type": ["string", "null"]},
                            "source_page_url": {
                                "type": "string",
                                "pattern": "^https?://",
                            },
                            "download_url": {
                                "type": "string",
                                "pattern": "^https?://",
                            },
                            "provider": {"type": "string"},
                            "license": {"type": ["string", "null"]},
                            "rights_status": {
                                "type": "string",
                                "enum": ["unknown", "cleared", "public_domain"],
                            },
                            "subjects": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "moods": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "energy": {"type": "string"},
                            "tempo_bpm": {"type": ["number", "null"]},
                            "instrumental": {"type": "boolean"},
                            "template_tags": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "loopable": {"type": "boolean"},
                        },
                    },
                }
            },
        },
    }


def _provider_warning(value: Any) -> str:
    if isinstance(value, dict):
        provider = str(value.get("provider", "unknown"))
        code = str(value.get("code", "provider_error"))
        return f"BGM provider {provider} returned {code}"
    return f"BGM provider warning: {value}"


def _download_ledger_path(request: BgmResolutionRequest) -> Path:
    return request.download_dir / (
        f"bgm-downloads-{_request_fingerprint(request)}.json"
    )


def _request_candidate_dir(request: BgmResolutionRequest) -> Path:
    return request.download_dir / f"request-{_request_fingerprint(request)}"


def _resolution_ledger_path(request: BgmResolutionRequest) -> Path:
    return request.download_dir / (
        f"bgm-resolution-{_request_fingerprint(request)}.json"
    )


def _current_resolution_path(request: BgmResolutionRequest) -> Path:
    return request.download_dir / "bgm-current-resolution.json"


def _load_download_ledger(request: BgmResolutionRequest) -> dict[str, Any]:
    path = _download_ledger_path(request)
    value = _read_json(path)
    if not value:
        return {
            "schema_version": 1,
            "request_fingerprint": _request_fingerprint(request),
            "candidates": {},
        }
    if value.get("schema_version") != 1 or not isinstance(
        value.get("candidates"),
        dict,
    ) or value.get("request_fingerprint") != _request_fingerprint(request):
        raise RuntimeError(f"Invalid BGM download ledger: {path}")
    value = _migrate_legacy_download_ledger(request, value, path)
    for fingerprint, entry in value["candidates"].items():
        if not isinstance(entry, dict) or not _valid_fingerprint(fingerprint):
            raise RuntimeError(f"Invalid BGM download ledger: {path}")
        _validate_candidate_entry(request, fingerprint, entry)
    return value


def _migrate_legacy_download_ledger(
    request: BgmResolutionRequest,
    ledger: dict[str, Any],
    ledger_path: Path,
) -> dict[str, Any]:
    changed = False
    owned_dir = _request_candidate_dir(request)
    candidates = ledger["candidates"]
    for fingerprint, entry in candidates.items():
        if not isinstance(entry, dict) or not _valid_fingerprint(fingerprint):
            continue
        raw_path = entry.get("path")
        if raw_path is not None:
            source = _legacy_candidate_path(
                request,
                fingerprint,
                raw_path,
            )
            if source is not None:
                destination = owned_dir / source.name
                _verify_legacy_candidate_entry(
                    request,
                    fingerprint,
                    entry,
                    source,
                    destination,
                )
                _move_legacy_candidate(source, destination)
                relative = _relative_artifact(request, destination)
                entry["path"] = relative
                track = entry.get("track")
                if isinstance(track, dict):
                    track["path"] = relative
                    track["metadata_path"] = relative
                changed = True
        for suffix in sorted(SUPPORTED_AUDIO_SUFFIXES):
            for name in (
                f"candidate-{fingerprint}{suffix}",
                f".candidate-{fingerprint}{suffix}.part",
            ):
                source = request.download_dir / name
                destination = owned_dir / name
                if not source.is_file():
                    continue
                if entry.get("status") == "validated" and name.startswith(
                    "candidate-"
                ):
                    # Validated final media was checked through entry.path.
                    if Path(str(raw_path)).name != name:
                        raise RuntimeError(
                            "Invalid legacy validated BGM candidate"
                        )
                    continue
                _move_legacy_candidate(source, destination)
                changed = True
    cleanup = ledger.get("cleanup")
    if isinstance(cleanup, dict):
        paths = cleanup.get("paths")
        if isinstance(paths, list):
            migrated_paths = []
            for raw in paths:
                if not isinstance(raw, str):
                    raise RuntimeError("Invalid legacy BGM cleanup path")
                migrated_paths.append(
                    _migrate_legacy_cleanup_path(
                        request,
                        raw,
                        set(candidates),
                    )
                )
            if migrated_paths != paths:
                cleanup["paths"] = migrated_paths
                changed = True
        selected = cleanup.get("selected")
        if isinstance(selected, str):
            migrated_selected = _migrate_legacy_cleanup_path(
                request,
                selected,
                set(candidates),
            )
            if migrated_selected != selected:
                cleanup["selected"] = migrated_selected
                changed = True
    if changed:
        _atomic_json(ledger_path, ledger)
    return ledger


def _legacy_candidate_path(
    request: BgmResolutionRequest,
    fingerprint: str,
    raw_path: str,
) -> Path | None:
    raw = Path(raw_path)
    if raw.is_absolute():
        return None
    resolved = _resolve_artifact(request, raw_path)
    if resolved.parent == _request_candidate_dir(request):
        return None
    if raw.parent != Path("."):
        return None
    if raw.name != _candidate_relative_path(fingerprint, raw.suffix):
        return None
    return resolved


def _verify_legacy_candidate_entry(
    request: BgmResolutionRequest,
    fingerprint: str,
    entry: dict[str, Any],
    source: Path,
    destination: Path,
) -> None:
    del request
    if entry.get("status") != "validated":
        return
    track = entry.get("track")
    digest = entry.get("sha256")
    if (
        not isinstance(track, dict)
        or entry.get("candidate_id") != track.get("id")
        or track.get("path") != source.name
        or track.get("metadata_path") != source.name
        or track.get("sha256") != digest
        or track.get("metadata_sha256") != digest
        or source.name
        != _candidate_relative_path(fingerprint, source.suffix)
    ):
        raise RuntimeError("Invalid legacy validated BGM candidate ledger")
    media = source if source.is_file() else destination
    if not media.is_file() or _sha256_file(media) != digest:
        raise RuntimeError("Legacy BGM candidate hash mismatch")


def _move_legacy_candidate(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        if source.is_file():
            _copy_durable(source, destination)
        else:
            fsync_directory(destination.parent)
        return
    if not source.is_file():
        # Cleaned/rejected tombstones may outlive their deterministic file.
        return
    _copy_durable(source, destination)


def _migrate_legacy_cleanup_path(
    request: BgmResolutionRequest,
    raw_path: str,
    fingerprints: set[str],
) -> str:
    path = _resolve_artifact(request, raw_path)
    if path.parent == _request_candidate_dir(request):
        return raw_path
    if Path(raw_path).parent != Path("."):
        raise RuntimeError("Invalid legacy BGM cleanup path")
    fingerprint = _candidate_artifact_fingerprint(raw_path)
    if fingerprint is None or fingerprint not in fingerprints:
        raise RuntimeError("Unowned legacy BGM cleanup path")
    return (
        Path(_request_candidate_dir(request).name) / Path(raw_path).name
    ).as_posix()


def _write_download_ledger(
    request: BgmResolutionRequest,
    ledger: dict[str, Any],
) -> None:
    _atomic_json(_download_ledger_path(request), ledger)


def _valid_fingerprint(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _candidate_relative_path(fingerprint: str, suffix: str) -> str:
    normalized = suffix.casefold()
    if normalized not in SUPPORTED_AUDIO_SUFFIXES:
        raise RuntimeError("BGM candidate has an unsupported suffix")
    return f"candidate-{fingerprint}{normalized}"


def _validate_candidate_entry(
    request: BgmResolutionRequest,
    fingerprint: str,
    entry: dict[str, Any],
) -> None:
    status = entry.get("status")
    if status not in {"provisional", "validated", "rejected", "cleaned"}:
        raise RuntimeError("Invalid BGM download ledger status")
    path_value = entry.get("path")
    if path_value is not None:
        path = _resolve_artifact(request, path_value)
        if (
            path.parent != _request_candidate_dir(request)
            or path.name != _candidate_relative_path(fingerprint, path.suffix)
        ):
            raise RuntimeError("Invalid BGM download ledger candidate path")
    if status != "validated":
        return
    if not isinstance(path_value, str) or not isinstance(entry.get("track"), dict):
        raise RuntimeError("Invalid validated BGM ledger entry")
    track = _track_from_dict(request, entry["track"])
    path = _resolve_artifact(request, path_value)
    digest = entry.get("sha256")
    if (
        track.path != path
        or track.metadata_path != path
        or track.sha256 != digest
        or track.metadata_sha256 != digest
        or not path.is_file()
        or _sha256_file(path) != digest
    ):
        raise RuntimeError("BGM download ledger path, metadata, or hash mismatch")


def _cached_track(
    candidate: OnlineBgmCandidate,
    request: BgmResolutionRequest,
    fingerprint: str,
    entry: dict[str, Any],
) -> BgmTrack | None:
    if entry.get("status") != "validated":
        return None
    _validate_candidate_entry(request, fingerprint, entry)
    track = _track_from_dict(request, entry["track"])
    if (
        track.id != candidate.id
        or entry.get("candidate_id") != candidate.id
    ):
        raise RuntimeError("BGM download ledger candidate identity mismatch")
    return track


def _deterministic_candidate_path(
    request: BgmResolutionRequest,
    fingerprint: str,
    suffix: str,
) -> Path:
    normalized_suffix = suffix.casefold() if suffix else ".audio"
    return (
        _request_candidate_dir(request)
        / f"candidate-{fingerprint}{normalized_suffix}"
    )


def _validated_candidate(
    candidate: OnlineBgmCandidate,
    request: BgmResolutionRequest,
    warnings: list[str],
) -> BgmTrack | None:
    ledger = _load_download_ledger(request)
    fingerprint = candidate_fingerprint(candidate)
    entries = ledger["candidates"]
    existing = entries.get(fingerprint)
    if isinstance(existing, dict):
        cached = _cached_track(candidate, request, fingerprint, existing)
        if cached is not None:
            return cached
        if existing.get("status") == "rejected":
            warnings.append(
                f"BGM candidate {candidate.id} remains rejected: "
                f"{existing.get('reason', 'validation failed')}"
            )
            return None

    candidate_dir = _request_candidate_dir(request)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    discovered_paths = [
        path
        for path in sorted(
            candidate_dir.glob(f"candidate-{fingerprint}.*")
        )
        if path.suffix.casefold() in SUPPORTED_AUDIO_SUFFIXES
    ]
    for discovered in discovered_paths:
        try:
            discovered_track = candidate_to_track(candidate, discovered)
        except (BgmSearchError, OSError, ValueError):
            continue
        digest = _sha256_file(discovered)
        track = replace(
            discovered_track,
            path=discovered.resolve(),
            metadata_path=discovered.resolve(),
            sha256=digest,
            metadata_sha256=digest,
        )
        entries[fingerprint] = {
            "candidate_id": candidate.id,
            "status": "validated",
            "path": _relative_artifact(request, track.path),
            "sha256": digest,
            "track": _track_to_dict(request, track),
            "validated_at": _now(),
        }
        _write_download_ledger(request, ledger)
        return track
    if discovered_paths:
        entries[fingerprint] = {
            "candidate_id": candidate.id,
            "status": "rejected",
            "path": _relative_artifact(request, discovered_paths[0]),
            "reason": "cached candidate failed validation",
            "rejected_at": _now(),
        }
        _write_download_ledger(request, ledger)
        warnings.append(
            f"BGM candidate {candidate.id} was rejected: "
            "cached candidate failed validation"
        )
        return None

    entries[fingerprint] = {
        "candidate_id": candidate.id,
        "status": "provisional",
        "path": None,
        "path_stem": f"candidate-{fingerprint}",
        "registered_at": _now(),
    }
    _write_download_ledger(request, ledger)
    downloaded: Path | None = None
    try:
        max_download_bytes = request.provider_config.get(
            "max_download_bytes",
            DEFAULT_MAX_DOWNLOAD_BYTES,
        )
        try:
            max_download_bytes = int(max_download_bytes)
        except (TypeError, ValueError):
            max_download_bytes = DEFAULT_MAX_DOWNLOAD_BYTES
            warnings.append(
                "Invalid BGM max_download_bytes; using the safe default"
            )
        allowed_hosts = request.provider_config.get("download_allowed_hosts")
        if not isinstance(allowed_hosts, list) or not all(
            isinstance(item, str) and item.strip() for item in allowed_hosts
        ):
            allowed_hosts = None
        downloaded = download_candidate(
            candidate,
            candidate_dir,
            max_download_bytes=max_download_bytes,
            allowed_hosts=allowed_hosts,
            output_name=f"candidate-{fingerprint}",
        )
        deterministic = _deterministic_candidate_path(
            request,
            fingerprint,
            downloaded.suffix,
        )
        if downloaded.resolve() != deterministic.resolve():
            os.replace(downloaded, deterministic)
        downloaded = deterministic
        entries[fingerprint]["path"] = _relative_artifact(
            request,
            deterministic,
        )
        _write_download_ledger(request, ledger)
        track = candidate_to_track(candidate, downloaded)
        digest = _sha256_file(deterministic)
        track = replace(
            track,
            path=deterministic.resolve(),
            metadata_path=deterministic.resolve(),
            sha256=digest,
            metadata_sha256=digest,
        )
        entries[fingerprint] = {
            "candidate_id": candidate.id,
            "status": "validated",
            "path": _relative_artifact(request, track.path),
            "sha256": digest,
            "track": _track_to_dict(request, track),
            "validated_at": _now(),
        }
        _write_download_ledger(request, ledger)
        return track
    except (BgmSearchError, OSError, ValueError) as exc:
        entries[fingerprint] = {
            "candidate_id": candidate.id,
            "status": "rejected",
            "path": (
                _relative_artifact(request, downloaded)
                if downloaded
                else None
            ),
            "reason": str(exc),
            "rejected_at": _now(),
        }
        _write_download_ledger(request, ledger)
        warnings.append(f"BGM candidate {candidate.id} was rejected: {exc}")
        return None


def _download_and_select(
    candidates: Iterable[OnlineBgmCandidate],
    request: BgmResolutionRequest,
    warnings: list[str],
) -> SelectionResult:
    tracks = [
        track
        for candidate in candidates
        if (track := _validated_candidate(candidate, request, warnings))
        is not None
    ]
    return select_bgm_candidate(tracks, request.query, request.policy)


def _resolution_id(
    request_fingerprint: str,
    mode: str,
    source: str,
    track: BgmTrack | None,
) -> str:
    return _canonical_hash(
        {
            "request_fingerprint": request_fingerprint,
            "mode": mode,
            "source": source,
            "track_id": track.id if track else None,
            "track_sha256": track.sha256 if track else None,
        }
    )[:32]


def _interaction_binding(
    request: BgmResolutionRequest,
) -> tuple[str | None, str | None]:
    expected = interaction_fingerprint(
        "bgm_candidates",
        _query_payload(request.query),
    )
    consumed = request.context.state.get("consumed_interactions", {})
    answer_fingerprints = request.context.state.get(
        "interaction_answer_fingerprints",
        {},
    )
    interaction_id = consumed.get(INTERACTION_KEY)
    if interaction_id and answer_fingerprints.get(INTERACTION_KEY) == expected:
        return str(interaction_id), expected
    pending = request.context.state.get("pending_interaction")
    if (
        pending
        and pending.get("key") == INTERACTION_KEY
        and pending.get("fingerprint") == expected
    ):
        return str(pending["id"]), expected
    return None, None


def _new_resolution(
    request: BgmResolutionRequest,
    mode: str,
    source: str,
    track: BgmTrack | None,
    scores: tuple[CandidateScore, ...],
    warnings: list[str],
) -> BgmResolution:
    request_fingerprint = _request_fingerprint(request)
    interaction_id, bound_fingerprint = _interaction_binding(request)
    return BgmResolution(
        mode=mode,
        source=source,
        track=track,
        scores=scores,
        warnings=tuple(warnings),
        resolution_id=_resolution_id(
            request_fingerprint,
            mode,
            source,
            track,
        ),
        request_fingerprint=request_fingerprint,
        interaction_id=interaction_id,
        interaction_fingerprint=bound_fingerprint,
    )


def _resolution_to_record(
    request: BgmResolutionRequest,
    resolution: BgmResolution,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "committed",
        "resolution_id": resolution.resolution_id,
        "request_fingerprint": resolution.request_fingerprint,
        "mode": resolution.mode,
        "source": resolution.source,
        "track": (
            _track_to_dict(request, resolution.track)
            if resolution.track
            else None
        ),
        "scores": [_score_to_dict(score) for score in resolution.scores],
        "warnings": list(resolution.warnings),
        "interaction": {
            "id": resolution.interaction_id,
            "fingerprint": resolution.interaction_fingerprint,
        }
        if resolution.interaction_id
        else None,
        "committed_at": _now(),
    }


def _resolution_from_record(
    request: BgmResolutionRequest,
    value: dict[str, Any],
) -> BgmResolution:
    track_value = value.get("track")
    track = _track_from_dict(request, track_value) if track_value else None
    if track is not None:
        if (
            not track.path.is_file()
            or _sha256_file(track.path) != track.sha256
            or not track.metadata_path.is_file()
            or _sha256_file(track.metadata_path) != track.metadata_sha256
        ):
            raise RuntimeError("Committed BGM resolution track is missing or changed")
        if value.get("source") in {"provider", "agent"}:
            download_ledger = _load_download_ledger(request)
            matching = [
                entry
                for entry in download_ledger["candidates"].values()
                if entry.get("status") == "validated"
                and entry.get("candidate_id") == track.id
                and entry.get("path") == _relative_artifact(request, track.path)
                and entry.get("sha256") == track.sha256
            ]
            if len(matching) != 1:
                raise RuntimeError(
                    "Committed BGM resolution does not match download ledger"
                )
    interaction = value.get("interaction") or {}
    return BgmResolution(
        mode=str(value["mode"]),
        source=str(value["source"]),
        track=track,
        scores=tuple(_score_from_dict(item) for item in value.get("scores", ())),
        warnings=tuple(str(item) for item in value.get("warnings", ())),
        resolution_id=str(value["resolution_id"]),
        request_fingerprint=str(value["request_fingerprint"]),
        interaction_id=(
            str(interaction["id"]) if interaction.get("id") else None
        ),
        interaction_fingerprint=(
            str(interaction["fingerprint"])
            if interaction.get("fingerprint")
            else None
        ),
    )


def _load_committed_resolution(
    request: BgmResolutionRequest,
) -> BgmResolution | None:
    value = _read_json(_resolution_ledger_path(request))
    if not value:
        return None
    if value.get("schema_version") != 1:
        raise RuntimeError("Unsupported BGM resolution ledger")
    if value.get("request_fingerprint") != _request_fingerprint(request):
        return None
    if value.get("status") not in {"committed", "acknowledged"}:
        return None
    value = _migrate_legacy_resolution_ledger(request, value)
    return _resolution_from_record(request, value)


def _migrate_legacy_resolution_ledger(
    request: BgmResolutionRequest,
    value: dict[str, Any],
) -> dict[str, Any]:
    if value.get("source") not in {"provider", "agent"}:
        return value
    track = value.get("track")
    if not isinstance(track, dict):
        return value
    raw_path = track.get("path")
    raw_metadata = track.get("metadata_path")
    if not isinstance(raw_path, str) or not isinstance(raw_metadata, str):
        raise RuntimeError("Invalid legacy BGM resolution track")
    path = _resolve_artifact(request, raw_path)
    if path.parent == _request_candidate_dir(request):
        return value
    if (
        Path(raw_path).parent != Path(".")
        or raw_metadata != raw_path
        or _candidate_artifact_fingerprint(raw_path) is None
    ):
        raise RuntimeError("Invalid legacy BGM resolution path")
    download_ledger = _load_download_ledger(request)
    matching = [
        entry
        for entry in download_ledger["candidates"].values()
        if entry.get("status") == "validated"
        and entry.get("candidate_id") == track.get("id")
        and entry.get("sha256") == track.get("sha256")
        and isinstance(entry.get("track"), dict)
        and entry["track"].get("metadata_sha256")
        == track.get("metadata_sha256")
    ]
    if len(matching) != 1:
        raise RuntimeError("Legacy BGM resolution does not match download ledger")
    migrated_path = matching[0]["path"]
    track["path"] = migrated_path
    track["metadata_path"] = migrated_path
    _atomic_json(_resolution_ledger_path(request), value)
    return value


def _commit_resolution(
    request: BgmResolutionRequest,
    resolution: BgmResolution,
) -> BgmResolution:
    path = _resolution_ledger_path(request)
    existing = _read_json(path)
    if (
        existing.get("request_fingerprint") == resolution.request_fingerprint
        and existing.get("status") in {"committed", "acknowledged"}
    ):
        committed = _resolution_from_record(request, existing)
        _claim_resolution_authority(request, committed)
        return committed
    _atomic_json(path, _resolution_to_record(request, resolution))
    _atomic_json(
        _current_resolution_path(request),
        {
            "schema_version": 1,
            "request_fingerprint": resolution.request_fingerprint,
            "resolution_id": resolution.resolution_id,
            "committed_at": _now(),
        },
    )
    return resolution


def _claim_resolution_authority(
    request: BgmResolutionRequest,
    resolution: BgmResolution,
) -> bool:
    path = _current_resolution_path(request)
    current = _read_json(path)
    if current:
        return (
            current.get("schema_version") == 1
            and current.get("request_fingerprint")
            == resolution.request_fingerprint
            and current.get("resolution_id") == resolution.resolution_id
        )
    _atomic_json(
        path,
        {
            "schema_version": 1,
            "request_fingerprint": resolution.request_fingerprint,
            "resolution_id": resolution.resolution_id,
            "committed_at": _now(),
        },
    )
    return True


def _cleanup_downloads(
    request: BgmResolutionRequest,
    selected_path: Path | None,
) -> None:
    ledger = _load_download_ledger(request)
    selected = selected_path.resolve() if selected_path else None
    selected_relative = (
        _relative_artifact(request, selected) if selected is not None else None
    )
    cleanup = ledger.get("cleanup")
    if not isinstance(cleanup, dict) or cleanup.get("status") != "pending":
        paths = [
            _relative_artifact(request, path)
            for path in _candidate_artifacts(request)
            if selected is None or path.resolve() != selected
        ]
        cleanup = {
            "status": "pending",
            "paths": paths,
            "selected": selected_relative,
            "planned_at": _now(),
        }
        ledger["cleanup"] = cleanup
        _write_download_ledger(request, ledger)

    paths = cleanup.get("paths")
    if not isinstance(paths, list) or not all(
        isinstance(path, str) for path in paths
    ):
        raise RuntimeError("Invalid BGM cleanup ledger")
    for relative in paths:
        _unlink_candidate_artifact(request, relative, selected)

    for entry in ledger["candidates"].values():
        raw_path = entry.get("path")
        is_selected = (
            selected_relative is not None and raw_path == selected_relative
        )
        if not is_selected:
            entry["status"] = "cleaned"
            entry["cleaned_at"] = _now()
    cleanup["status"] = "completed"
    cleanup["completed_at"] = _now()
    _write_download_ledger(request, ledger)


def _candidate_artifact_fingerprint(name: str) -> str | None:
    match = _CANDIDATE_ARTIFACT.fullmatch(Path(name).name)
    if not match:
        return None
    suffix = match.group("suffix") or match.group("partial_suffix")
    if suffix.casefold() not in SUPPORTED_AUDIO_SUFFIXES:
        return None
    return match.group("final") or match.group("partial")


def _candidate_artifacts(request: BgmResolutionRequest) -> tuple[Path, ...]:
    candidate_dir = _request_candidate_dir(request)
    if not candidate_dir.is_dir():
        return ()
    return tuple(
        path
        for path in sorted(candidate_dir.iterdir())
        if path.is_file()
        and _candidate_artifact_fingerprint(path.name) is not None
    )


def _unlink_candidate_artifact(
    request: BgmResolutionRequest,
    relative: str,
    selected: Path | None,
) -> None:
    if _candidate_artifact_fingerprint(relative) is None:
        raise RuntimeError("Invalid BGM cleanup artifact")
    path = _resolve_artifact(request, relative)
    if path.parent != _request_candidate_dir(request):
        raise RuntimeError("BGM cleanup artifact belongs to another request")
    if selected is not None and path == selected:
        return
    path.unlink(missing_ok=True)


def _finalize_resolution(
    request: BgmResolutionRequest,
    resolution: BgmResolution,
) -> BgmResolution:
    committed = _commit_resolution(request, resolution)
    _cleanup_downloads(
        request,
        committed.track.path if committed.track and committed.source != "local" else None,
    )
    return committed


def _copy_durable(source: Path, destination: Path) -> None:
    source_digest = _sha256_file(source)
    if destination.is_file():
        if _sha256_file(destination) != source_digest:
            raise RuntimeError("Conflicting durable copy destination")
        fsync_directory(destination.parent)
        return
    temporary = destination.with_name(f".{destination.name}.part")
    temporary.unlink(missing_ok=True)
    try:
        with source.open("rb") as input_stream, temporary.open("xb") as output:
            shutil.copyfileobj(input_stream, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _materialize_selected_track(
    request: BgmResolutionRequest,
    track: BgmTrack,
) -> BgmTrack:
    try:
        track.path.resolve().relative_to(request.download_dir)
        track.metadata_path.resolve().relative_to(request.download_dir)
        return track
    except ValueError:
        pass
    request.download_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"selected-{_request_fingerprint(request)}-{track.sha256}"
    audio_path = request.download_dir / f"{prefix}{track.path.suffix.casefold()}"
    _copy_durable(track.path, audio_path)
    if track.metadata_path.resolve() == track.path.resolve():
        metadata_path = audio_path
    else:
        metadata_path = request.download_dir / (
            f"{prefix}.metadata{track.metadata_path.suffix.casefold()}"
        )
        _copy_durable(track.metadata_path, metadata_path)
    return replace(
        track,
        path=audio_path.resolve(),
        metadata_path=metadata_path.resolve(),
        sha256=_sha256_file(audio_path),
        metadata_sha256=_sha256_file(metadata_path),
    )


def _selected_resolution(
    request: BgmResolutionRequest,
    track: BgmTrack,
    source: str,
    scores: tuple[CandidateScore, ...],
    warnings: list[str],
) -> BgmResolution:
    track = _materialize_selected_track(request, track)
    if track.rights_status.strip().casefold() == "unknown":
        warnings.append(
            f"Selected BGM track {track.id} rights status is unknown"
        )
    return _finalize_resolution(
        request,
        _new_resolution(
            request,
            "bgm",
            source,
            track,
            scores,
            warnings,
        ),
    )


def _narration_only(
    request: BgmResolutionRequest,
    warnings: list[str],
) -> BgmResolution:
    warnings.append("Using narration-only audio because no BGM was selected")
    return _finalize_resolution(
        request,
        _new_resolution(
            request,
            "narration_only",
            "none",
            None,
            (),
            warnings,
        ),
    )


def acknowledge_bgm_resolution(
    request: BgmResolutionRequest,
    interaction_port: InteractionPort,
    resolution_id: str,
) -> bool:
    path = _resolution_ledger_path(request)
    value = _read_json(path)
    if (
        value.get("resolution_id") != resolution_id
        or value.get("request_fingerprint") != _request_fingerprint(request)
    ):
        raise ValueError("BGM resolution acknowledgement is stale")
    acknowledged = value.get("status") == "acknowledged"
    if not acknowledged:
        value["status"] = "acknowledged"
        value["acknowledged_at"] = _now()
        _atomic_json(path, value)
    interaction = value.get("interaction") or {}
    interaction_id = interaction.get("id")
    interaction_fp = interaction.get("fingerprint")
    consumed = request.context.state.get("consumed_interactions", {})
    answer_fingerprints = request.context.state.get(
        "interaction_answer_fingerprints",
        {},
    )
    pending = request.context.state.get("pending_interaction")
    consumed_matches = (
        interaction_id
        and consumed.get(INTERACTION_KEY) == interaction_id
        and answer_fingerprints.get(INTERACTION_KEY) == interaction_fp
    )
    pending_matches = (
        interaction_id
        and pending
        and pending.get("key") == INTERACTION_KEY
        and pending.get("id") == interaction_id
        and pending.get("fingerprint") == interaction_fp
    )
    if consumed_matches or pending_matches:
        interaction_port.clear(request.context, INTERACTION_KEY)
    return not acknowledged


def resolve_bgm_for_run(
    request: BgmResolutionRequest,
    interaction_port: InteractionPort,
) -> BgmResolution:
    """Resolve and durably commit one BGM decision without mixing or stage changes."""
    committed = _load_committed_resolution(request)
    if committed is not None:
        _claim_resolution_authority(request, committed)
        _cleanup_downloads(
            request,
            committed.track.path
            if committed.track and committed.source != "local"
            else None,
        )
        return committed

    warnings: list[str] = []
    if not request.policy.enabled:
        warnings.append("BGM is disabled by template policy")
        return _narration_only(request, warnings)

    local = select_bgm_candidate(
        request.local_tracks,
        request.query,
        request.policy,
    )
    if local.track is not None:
        return _selected_resolution(
            request,
            local.track,
            "local",
            local.scores,
            warnings,
        )

    try:
        provider_candidates = search_configured_providers(
            request.query,
            request.provider_config,
        )
        warnings.extend(
            _provider_warning(item)
            for item in getattr(provider_candidates, "warnings", ())
        )
    except (BgmSearchError, OSError, TypeError, ValueError) as exc:
        provider_candidates = ()
        warnings.append(f"BGM provider search failed: {exc}")

    provider_selection = _download_and_select(
        provider_candidates,
        request,
        warnings,
    )
    if provider_selection.track is not None:
        return _selected_resolution(
            request,
            provider_selection.track,
            "provider",
            provider_selection.scores,
            warnings,
        )

    payload = _query_payload(
        request.query,
        request.max_agent_candidates,
        request.max_agent_response_bytes,
    )
    expected_fingerprint = interaction_fingerprint("bgm_candidates", payload)
    pending = request.context.state.get("pending_interaction")
    if pending and pending.get("key") == INTERACTION_KEY:
        stored_fingerprint = pending.get("fingerprint") or interaction_fingerprint(
            str(pending.get("kind", "text")),
            pending.get("payload"),
        )
        if stored_fingerprint == expected_fingerprint:
            if not interaction_port.supports_agent_handoff:
                raise InteractionRequired(pending)
        else:
            clear_interaction_state(request.context, INTERACTION_KEY)

    if not interaction_port.supports_agent_handoff:
        warnings.append("BGM Agent handoff is unavailable")
        return _narration_only(request, warnings)

    response = interaction_port.ask(
        request.context,
        INTERACTION_KEY,
        (
            "Find public, downloadable instrumental BGM candidates matching "
            "the query and submit one JSON object matching response_schema"
        ),
        kind="bgm_candidates",
        payload=payload,
    )
    try:
        agent_candidates = parse_agent_candidates(
            response,
            max_candidates=request.max_agent_candidates,
            max_response_bytes=request.max_agent_response_bytes,
        )
    except BgmSearchError as exc:
        warnings.append(f"BGM Agent response was rejected: {exc}")
        return _narration_only(request, warnings)

    agent_selection = _download_and_select(
        agent_candidates,
        request,
        warnings,
    )
    if agent_selection.track is not None:
        return _selected_resolution(
            request,
            agent_selection.track,
            "agent",
            agent_selection.scores,
            warnings,
        )
    return _narration_only(request, warnings)
