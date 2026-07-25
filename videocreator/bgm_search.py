from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import urlopen

from .bgm_library import BgmTrack, SUPPORTED_AUDIO_SUFFIXES
from .bgm_selection import BgmQuery


DEFAULT_MAX_CANDIDATES = 8
DEFAULT_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_AGENT_CANDIDATES = 20
MAX_AGENT_RESPONSE_BYTES = 200_000
_ALLOWED_RIGHTS_STATUS = {"unknown", "cleared", "public_domain"}
_CONTENT_TYPE_SUFFIXES = {
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "application/ogg": ".ogg",
}
_TAG_RE = re.compile(r"<[^>]+>")


class BgmSearchError(ValueError):
    """Raised when an online BGM candidate is unsafe or malformed."""


@dataclass(frozen=True)
class OnlineBgmCandidate:
    id: str
    title: str
    creator: str | None
    source_page_url: str
    download_url: str
    provider: str
    license: str | None
    rights_status: str
    subjects: tuple[str, ...]
    moods: tuple[str, ...]
    energy: str
    tempo_bpm: float | None
    instrumental: bool
    template_tags: tuple[str, ...]
    loopable: bool

    def __post_init__(self) -> None:
        if not self.id or not self.title or not self.provider:
            raise BgmSearchError("candidate id, title, and provider are required")
        if (
            not isinstance(self.rights_status, str)
            or self.rights_status.strip().lower() not in _ALLOWED_RIGHTS_STATUS
        ):
            object.__setattr__(self, "rights_status", "unknown")


@dataclass(frozen=True)
class ProviderWarning:
    provider: str
    code: str


class OnlineBgmCandidates(list[OnlineBgmCandidate]):
    """Provider result list with non-fatal adapter failures for later reporting."""

    def __init__(
        self,
        values: Iterable[OnlineBgmCandidate] = (),
        warnings: Iterable[ProviderWarning] = (),
    ) -> None:
        super().__init__(values)
        self.warnings = tuple(
            {"provider": warning.provider, "code": warning.code}
            for warning in warnings
        )


def _validate_http_url(value: str, label: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise BgmSearchError(f"{label} URL must use http or https")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    if not address.is_global:
        raise BgmSearchError(f"{label} URL host must be public")


def _string_tuple(value: Any, field: str, default: Iterable[str] = ()) -> tuple[str, ...]:
    if value is None:
        return tuple(default)
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise BgmSearchError(f"{field} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


def _optional_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BgmSearchError(f"{field} must be a string")
    return value.strip() or None


def _normalized_rights(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().lower()
    return normalized if normalized in _ALLOWED_RIGHTS_STATUS else "unknown"


def _has_known_license(value: str | None) -> bool:
    return value is not None and value.casefold() not in {"unknown", "n/a", "none"}


def _candidate_id(provider: str, source_page_url: str, download_url: str) -> str:
    digest = hashlib.sha256(f"{provider}\n{source_page_url}\n{download_url}".encode("utf-8"))
    return f"{provider}-{digest.hexdigest()[:16]}"


def _candidate_from_mapping(value: dict[str, Any], *, default_provider: str | None = None) -> OnlineBgmCandidate:
    source_page_url = value.get("source_page_url")
    download_url = value.get("download_url")
    if not isinstance(source_page_url, str) or not source_page_url:
        raise BgmSearchError("candidate source_page_url is required")
    if not isinstance(download_url, str) or not download_url:
        raise BgmSearchError("candidate download_url is required")
    _validate_http_url(source_page_url, "source page")
    _validate_http_url(download_url, "download")
    provider = value.get("provider", default_provider)
    if not isinstance(provider, str) or not provider.strip():
        raise BgmSearchError("candidate provider is required")
    title = value.get("title")
    if not isinstance(title, str) or not title.strip():
        raise BgmSearchError("candidate title is required")
    tempo = value.get("tempo_bpm")
    if tempo is not None and (isinstance(tempo, bool) or not isinstance(tempo, (int, float))):
        raise BgmSearchError("tempo_bpm must be a number or null")
    instrumental = value.get("instrumental", True)
    if not isinstance(instrumental, bool):
        raise BgmSearchError("instrumental must be a boolean")
    loopable = value.get("loopable", True)
    if not isinstance(loopable, bool):
        raise BgmSearchError("loopable must be a boolean")
    license_name = _optional_string(value.get("license"), "license")
    rights_status = _normalized_rights(value.get("rights_status"))
    if not _has_known_license(license_name):
        rights_status = "unknown"
    return OnlineBgmCandidate(
        id=str(value.get("id") or _candidate_id(provider.strip(), source_page_url, download_url)),
        title=title.strip(),
        creator=_optional_string(value.get("creator"), "creator"),
        source_page_url=source_page_url,
        download_url=download_url,
        provider=provider.strip(),
        license=license_name,
        rights_status=rights_status,
        subjects=_string_tuple(value.get("subjects"), "subjects"),
        moods=_string_tuple(value.get("moods"), "moods"),
        energy=str(value.get("energy", "low-medium")).strip() or "low-medium",
        tempo_bpm=float(tempo) if tempo is not None else None,
        instrumental=instrumental,
        template_tags=_string_tuple(value.get("template_tags"), "template_tags"),
        loopable=loopable,
    )


def _response_payload(response: Any) -> bytes:
    with response as active_response:
        value = active_response.read()
    if not isinstance(value, bytes):
        raise BgmSearchError("provider response must be bytes")
    return value


def _wikimedia_search(query: BgmQuery, provider: dict[str, Any], opener: Callable[..., Any]) -> list[OnlineBgmCandidate]:
    max_candidates = int(provider.get("max_candidates", DEFAULT_MAX_CANDIDATES))
    if max_candidates < 1:
        return []
    terms = " ".join((*query.terms_en, *query.terms_zh))
    parameters = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrnamespace": "6",
        "gsrlimit": str(max_candidates),
        "gsrsearch": terms,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
    }
    endpoint = "https://commons.wikimedia.org/w/api.php?" + urlencode(parameters)
    payload = json.loads(_response_payload(opener(endpoint, timeout=15)).decode("utf-8"))
    pages = (payload.get("query") or {}).get("pages") or {}
    records = pages.values() if isinstance(pages, dict) else pages if isinstance(pages, list) else []
    candidates: list[OnlineBgmCandidate] = []
    for page in records:
        if not isinstance(page, dict):
            continue
        title = page.get("title")
        image_info = page.get("imageinfo")
        if not isinstance(title, str) or not isinstance(image_info, list) or not image_info:
            continue
        info = image_info[0]
        if not isinstance(info, dict) or not isinstance(info.get("url"), str):
            continue
        download_url = info["url"]
        suffix = Path(urlsplit(download_url).path).suffix.lower()
        if suffix not in SUPPORTED_AUDIO_SUFFIXES:
            continue
        metadata = info.get("extmetadata") if isinstance(info.get("extmetadata"), dict) else {}
        artist = metadata.get("Artist") or metadata.get("ArtistName") or {}
        license_value = metadata.get("LicenseShortName") or metadata.get("UsageTerms") or {}
        creator = _clean_metadata_value(artist.get("value") if isinstance(artist, dict) else None)
        license_name = _clean_metadata_value(license_value.get("value") if isinstance(license_value, dict) else None)
        source_page_url = "https://commons.wikimedia.org/wiki/" + quote(title.replace(" ", "_"), safe="_:()")
        try:
            _validate_http_url(download_url, "download")
            _validate_http_url(source_page_url, "source page")
        except BgmSearchError:
            continue
        candidates.append(
            OnlineBgmCandidate(
                id=_candidate_id("wikimedia", source_page_url, download_url),
                title=title.removeprefix("File:").strip() or title,
                creator=creator,
                source_page_url=source_page_url,
                download_url=download_url,
                provider="wikimedia",
                license=license_name,
                rights_status="unknown",
                subjects=query.subjects,
                moods=query.moods,
                energy="low-medium",
                tempo_bpm=None,
                instrumental=True,
                template_tags=(query.template_id,),
                loopable=True,
            )
        )
        if len(candidates) >= max_candidates:
            break
    return candidates


def _clean_metadata_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = html.unescape(_TAG_RE.sub("", value)).strip()
    return cleaned or None


def search_configured_providers(
    query: BgmQuery,
    config: dict[str, Any],
    opener: Callable[..., Any] = urlopen,
) -> OnlineBgmCandidates:
    """Query enabled core providers without allowing one provider failure to abort search."""
    providers = config.get("providers", [])
    if not isinstance(providers, list):
        raise BgmSearchError("providers must be a list")
    candidates: list[OnlineBgmCandidate] = []
    warnings: list[ProviderWarning] = []
    configured_max = int(config.get("max_candidates", DEFAULT_MAX_CANDIDATES))
    for provider in providers:
        if not isinstance(provider, dict) or not provider.get("enabled", False):
            continue
        provider_type = str(provider.get("type", ""))
        if provider_type != "wikimedia":
            warnings.append(ProviderWarning(provider_type or "unknown", "unsupported_provider"))
            continue
        try:
            provider_candidates = _wikimedia_search(query, provider, opener)
        except (BgmSearchError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            warnings.append(ProviderWarning(provider_type, "provider_error"))
            continue
        candidates.extend(provider_candidates)
        if len(candidates) >= configured_max:
            break
    return OnlineBgmCandidates(candidates[:max(0, configured_max)], warnings)


def _approved_host(url: str, allowed_hosts: set[str]) -> None:
    hostname = urlsplit(url).hostname
    if hostname is None or hostname.casefold() not in allowed_hosts:
        raise BgmSearchError("download host is not approved")


def _response_content_type(response: Any) -> str:
    headers = getattr(response, "headers", {})
    if hasattr(headers, "get_content_type"):
        return str(headers.get_content_type()).casefold()
    if hasattr(headers, "get"):
        return str(headers.get("Content-Type", "")).split(";", 1)[0].strip().casefold()
    return ""


def _suffix_for_download(url: str, content_type: str) -> str:
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in SUPPORTED_AUDIO_SUFFIXES:
        return suffix
    return _CONTENT_TYPE_SUFFIXES.get(content_type, "")


def download_candidate(
    candidate: OnlineBgmCandidate,
    output_dir: Path,
    opener: Callable[..., Any] = urlopen,
    *,
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    allowed_hosts: Iterable[str] | None = None,
) -> Path:
    """Download one candidate into a run-owned directory with bounded IO."""
    if max_download_bytes < 1:
        raise BgmSearchError("max_download_bytes must be positive")
    _validate_http_url(candidate.download_url, "download")
    initial_host = urlsplit(candidate.download_url).hostname
    assert initial_host is not None
    approved_hosts = {host.casefold() for host in (allowed_hosts or (initial_host,))}
    _approved_host(candidate.download_url, approved_hosts)
    root = output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        response = opener(candidate.download_url, timeout=30)
        final_url = str(response.geturl()) if hasattr(response, "geturl") else candidate.download_url
        _validate_http_url(final_url, "download redirect")
        _approved_host(final_url, approved_hosts)
        content_type = _response_content_type(response)
        suffix = _suffix_for_download(final_url, content_type)
        if not suffix or content_type not in _CONTENT_TYPE_SUFFIXES and not content_type.startswith("audio/"):
            raise BgmSearchError("download content is not supported audio")
        temporary_path = root / f".bgm-{uuid.uuid4().hex}{suffix}.part"
        output_path = root / f"bgm-{uuid.uuid4().hex}{suffix}"
        digest = hashlib.sha256()
        total = 0
        with response as active_response, temporary_path.open("xb") as handle:
            while chunk := active_response.read(1024 * 1024):
                if not isinstance(chunk, bytes):
                    raise BgmSearchError("download response must contain bytes")
                total += len(chunk)
                if total > max_download_bytes:
                    raise BgmSearchError("download exceeds maximum size")
                digest.update(chunk)
                handle.write(chunk)
        if total == 0:
            raise BgmSearchError("download is empty")
        # Reading the completed file catches truncated writes before later media validation.
        if digest.hexdigest() != hashlib.sha256(temporary_path.read_bytes()).hexdigest():
            raise BgmSearchError("download hash verification failed")
        temporary_path.replace(output_path)
        return output_path
    except Exception as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if isinstance(exc, BgmSearchError):
            raise
        raise BgmSearchError(f"download failed: {exc}") from exc


def parse_agent_candidates(response: str) -> list[OnlineBgmCandidate]:
    encoded = response.encode("utf-8")
    if len(encoded) > MAX_AGENT_RESPONSE_BYTES:
        raise BgmSearchError(f"agent response exceeds {MAX_AGENT_RESPONSE_BYTES} bytes")
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as exc:
        raise BgmSearchError("agent response must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise BgmSearchError("agent response must be an object")
    unknown_keys = set(payload) - {"candidates"}
    if unknown_keys:
        raise BgmSearchError("agent response contains unknown top-level keys")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise BgmSearchError("agent candidates must be a list")
    if len(candidates) > MAX_AGENT_CANDIDATES:
        raise BgmSearchError(f"agent response accepts at most {MAX_AGENT_CANDIDATES} candidates")
    parsed: list[OnlineBgmCandidate] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise BgmSearchError("agent candidate must be an object")
        parsed.append(_candidate_from_mapping(candidate))
    return parsed


def candidate_to_track(candidate: OnlineBgmCandidate, downloaded_path: Path) -> BgmTrack:
    """Create a selectable BGM track while retaining online provenance."""
    path = downloaded_path.resolve()
    if not path.is_file():
        raise BgmSearchError("downloaded candidate file is missing")
    if path.suffix.lower() not in SUPPORTED_AUDIO_SUFFIXES:
        raise BgmSearchError("downloaded candidate has unsupported audio suffix")
    return BgmTrack(
        id=candidate.id,
        path=path,
        metadata_path=path,
        level="online",
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        title=candidate.title,
        creator=candidate.creator,
        source_url=candidate.source_page_url,
        license=candidate.license,
        rights_status=candidate.rights_status,
        subjects=candidate.subjects,
        moods=candidate.moods,
        energy=candidate.energy,
        tempo_bpm=candidate.tempo_bpm,
        instrumental=candidate.instrumental,
        template_tags=candidate.template_tags,
        avoid_for=(),
        preferred_start_ms=0,
        loopable=candidate.loopable,
    )
