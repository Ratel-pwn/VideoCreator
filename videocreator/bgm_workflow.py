from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .bgm_library import BgmTrack
from .bgm_policy import BgmPolicy
from .bgm_search import (
    DEFAULT_MAX_DOWNLOAD_BYTES,
    MAX_AGENT_CANDIDATES,
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
    interaction_fingerprint,
)


INTERACTION_KEY = "bgm-online-candidates"
_DOWNLOAD_LEDGER = Path("audio/bgm-downloads.json")
_RESOLUTION_LEDGER = Path("audio/bgm-resolution.json")
_SENSITIVE_CONFIG_TERMS = (
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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

    def __post_init__(self) -> None:
        download_dir = Path(self.download_dir).resolve()
        try:
            download_dir.relative_to(self.context.run_dir.resolve())
        except ValueError as exc:
            raise ValueError("BGM download directory must stay inside the run") from exc
        object.__setattr__(self, "download_dir", download_dir)
        object.__setattr__(self, "local_tracks", tuple(self.local_tracks))


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
        }
    )


def candidate_fingerprint(candidate: OnlineBgmCandidate) -> str:
    return _canonical_hash(asdict(candidate))


def _track_to_dict(track: BgmTrack) -> dict[str, Any]:
    value = asdict(track)
    value["path"] = str(track.path)
    value["metadata_path"] = str(track.metadata_path)
    return value


def _track_from_dict(value: dict[str, Any]) -> BgmTrack:
    tuple_fields = (
        "subjects",
        "moods",
        "template_tags",
        "avoid_for",
    )
    normalized = dict(value)
    normalized["path"] = Path(normalized["path"]).resolve()
    normalized["metadata_path"] = Path(normalized["metadata_path"]).resolve()
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


def _query_payload(query: BgmQuery) -> dict[str, Any]:
    return {
        "schema_version": 1,
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
                    "maxItems": MAX_AGENT_CANDIDATES,
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
    return request.context.run_dir / _DOWNLOAD_LEDGER


def _load_download_ledger(request: BgmResolutionRequest) -> dict[str, Any]:
    path = _download_ledger_path(request)
    value = _read_json(path)
    if not value:
        return {"schema_version": 1, "candidates": {}}
    if value.get("schema_version") != 1 or not isinstance(
        value.get("candidates"),
        dict,
    ):
        raise RuntimeError(f"Invalid BGM download ledger: {path}")
    return value


def _write_download_ledger(
    request: BgmResolutionRequest,
    ledger: dict[str, Any],
) -> None:
    _atomic_json(_download_ledger_path(request), ledger)


def _cached_track(
    candidate: OnlineBgmCandidate,
    entry: dict[str, Any],
) -> BgmTrack | None:
    if entry.get("status") != "validated":
        return None
    try:
        track = _track_from_dict(entry["track"])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        not track.path.is_file()
        or _sha256_file(track.path) != entry.get("sha256")
        or track.sha256 != entry.get("sha256")
        or track.id != candidate.id
    ):
        return None
    return track


def _deterministic_candidate_path(
    request: BgmResolutionRequest,
    fingerprint: str,
    suffix: str,
) -> Path:
    normalized_suffix = suffix.casefold() if suffix else ".audio"
    return request.download_dir / f"candidate-{fingerprint}{normalized_suffix}"


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
        cached = _cached_track(candidate, existing)
        if cached is not None:
            return cached
        if existing.get("status") == "rejected":
            warnings.append(
                f"BGM candidate {candidate.id} remains rejected: "
                f"{existing.get('reason', 'validation failed')}"
            )
            return None

    request.download_dir.mkdir(parents=True, exist_ok=True)
    discovered_paths = sorted(
        request.download_dir.glob(f"candidate-{fingerprint}.*")
    )
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
            "path": str(track.path),
            "sha256": digest,
            "track": _track_to_dict(track),
            "validated_at": _now(),
        }
        _write_download_ledger(request, ledger)
        return track
    if discovered_paths:
        entries[fingerprint] = {
            "candidate_id": candidate.id,
            "status": "rejected",
            "path": str(discovered_paths[0].resolve()),
            "reason": "cached candidate failed validation",
            "rejected_at": _now(),
        }
        _write_download_ledger(request, ledger)
        warnings.append(
            f"BGM candidate {candidate.id} was rejected: "
            "cached candidate failed validation"
        )
        return None

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
            request.download_dir,
            max_download_bytes=max_download_bytes,
            allowed_hosts=allowed_hosts,
        )
        track = candidate_to_track(candidate, downloaded)
        deterministic = _deterministic_candidate_path(
            request,
            fingerprint,
            downloaded.suffix,
        )
        if downloaded.resolve() != deterministic.resolve():
            os.replace(downloaded, deterministic)
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
            "path": str(track.path),
            "sha256": digest,
            "track": _track_to_dict(track),
            "validated_at": _now(),
        }
        _write_download_ledger(request, ledger)
        return track
    except (BgmSearchError, OSError, ValueError) as exc:
        entries[fingerprint] = {
            "candidate_id": candidate.id,
            "status": "rejected",
            "path": str(downloaded.resolve()) if downloaded else None,
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


def _resolution_to_record(resolution: BgmResolution) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "committed",
        "resolution_id": resolution.resolution_id,
        "request_fingerprint": resolution.request_fingerprint,
        "mode": resolution.mode,
        "source": resolution.source,
        "track": _track_to_dict(resolution.track) if resolution.track else None,
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


def _resolution_from_record(value: dict[str, Any]) -> BgmResolution:
    track_value = value.get("track")
    track = _track_from_dict(track_value) if track_value else None
    if track is not None:
        if not track.path.is_file() or _sha256_file(track.path) != track.sha256:
            raise RuntimeError("Committed BGM resolution track is missing or changed")
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
    value = _read_json(request.context.run_dir / _RESOLUTION_LEDGER)
    if not value:
        return None
    if value.get("schema_version") != 1:
        raise RuntimeError("Unsupported BGM resolution ledger")
    if value.get("request_fingerprint") != _request_fingerprint(request):
        return None
    if value.get("status") not in {"committed", "acknowledged"}:
        return None
    return _resolution_from_record(value)


def _commit_resolution(
    request: BgmResolutionRequest,
    resolution: BgmResolution,
) -> BgmResolution:
    path = request.context.run_dir / _RESOLUTION_LEDGER
    existing = _read_json(path)
    if (
        existing.get("request_fingerprint") == resolution.request_fingerprint
        and existing.get("status") in {"committed", "acknowledged"}
    ):
        return _resolution_from_record(existing)
    _atomic_json(path, _resolution_to_record(resolution))
    return resolution


def _cleanup_downloads(
    request: BgmResolutionRequest,
    selected_path: Path | None,
) -> None:
    ledger = _load_download_ledger(request)
    changed = False
    selected = selected_path.resolve() if selected_path else None
    run_root = request.context.run_dir.resolve()
    for entry in ledger["candidates"].values():
        raw_path = entry.get("path")
        if not raw_path or entry.get("status") == "cleaned":
            continue
        path = Path(raw_path).resolve()
        try:
            path.relative_to(run_root)
        except ValueError:
            continue
        if selected is not None and path == selected:
            continue
        path.unlink(missing_ok=True)
        entry["status"] = "cleaned"
        entry["cleaned_at"] = _now()
        changed = True
    if changed:
        _write_download_ledger(request, ledger)


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


def _selected_resolution(
    request: BgmResolutionRequest,
    track: BgmTrack,
    source: str,
    scores: tuple[CandidateScore, ...],
    warnings: list[str],
) -> BgmResolution:
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
    path = request.context.run_dir / _RESOLUTION_LEDGER
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
        payload=_query_payload(request.query),
    )
    try:
        agent_candidates = parse_agent_candidates(response)
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
