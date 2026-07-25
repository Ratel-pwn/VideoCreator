from __future__ import annotations

from dataclasses import dataclass
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
    select_bgm_candidate,
)
from .interactions import InteractionContext, InteractionPort


INTERACTION_KEY = "bgm-online-candidates"


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


def _download_and_select(
    candidates: Iterable[OnlineBgmCandidate],
    request: BgmResolutionRequest,
    warnings: list[str],
):
    tracks: list[BgmTrack] = []
    max_download_bytes = request.provider_config.get(
        "max_download_bytes",
        DEFAULT_MAX_DOWNLOAD_BYTES,
    )
    try:
        max_download_bytes = int(max_download_bytes)
    except (TypeError, ValueError):
        max_download_bytes = DEFAULT_MAX_DOWNLOAD_BYTES
        warnings.append("Invalid BGM max_download_bytes; using the safe default")
    allowed_hosts = request.provider_config.get("download_allowed_hosts")
    if not isinstance(allowed_hosts, list) or not all(
        isinstance(item, str) and item.strip() for item in allowed_hosts
    ):
        allowed_hosts = None

    for candidate in candidates:
        try:
            downloaded = download_candidate(
                candidate,
                request.download_dir,
                max_download_bytes=max_download_bytes,
                allowed_hosts=allowed_hosts,
            )
            tracks.append(candidate_to_track(candidate, downloaded))
        except (BgmSearchError, OSError, ValueError) as exc:
            warnings.append(
                f"BGM candidate {candidate.id} was rejected: {exc}"
            )

    selection = select_bgm_candidate(tracks, request.query, request.policy)
    return selection


def _selected_resolution(
    track: BgmTrack,
    source: str,
    scores: tuple[CandidateScore, ...],
    warnings: list[str],
) -> BgmResolution:
    if track.rights_status.strip().casefold() == "unknown":
        warnings.append(
            f"Selected BGM track {track.id} rights status is unknown"
        )
    return BgmResolution(
        mode="bgm",
        source=source,
        track=track,
        scores=scores,
        warnings=tuple(warnings),
    )


def _narration_only(
    interaction_port: InteractionPort,
    request: BgmResolutionRequest,
    warnings: list[str],
) -> BgmResolution:
    interaction_port.clear(request.context, INTERACTION_KEY)
    warnings.append("Using narration-only audio because no BGM was selected")
    return BgmResolution(
        mode="narration_only",
        source="none",
        track=None,
        scores=(),
        warnings=tuple(warnings),
    )


def resolve_bgm_for_run(
    request: BgmResolutionRequest,
    interaction_port: InteractionPort,
) -> BgmResolution:
    """Resolve exactly one BGM track without mixing or advancing workflow state."""
    warnings: list[str] = []
    if not request.policy.enabled:
        warnings.append("BGM is disabled by template policy")
        return _narration_only(interaction_port, request, warnings)

    local = select_bgm_candidate(
        request.local_tracks,
        request.query,
        request.policy,
    )
    if local.track is not None:
        interaction_port.clear(request.context, INTERACTION_KEY)
        return _selected_resolution(
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
        interaction_port.clear(request.context, INTERACTION_KEY)
        return _selected_resolution(
            provider_selection.track,
            "provider",
            provider_selection.scores,
            warnings,
        )

    if not interaction_port.supports_agent_handoff:
        warnings.append("BGM Agent handoff is unavailable")
        return _narration_only(interaction_port, request, warnings)

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
        return _narration_only(interaction_port, request, warnings)

    agent_selection = _download_and_select(
        agent_candidates,
        request,
        warnings,
    )
    if agent_selection.track is not None:
        interaction_port.clear(request.context, INTERACTION_KEY)
        return _selected_resolution(
            agent_selection.track,
            "agent",
            agent_selection.scores,
            warnings,
        )
    return _narration_only(interaction_port, request, warnings)
