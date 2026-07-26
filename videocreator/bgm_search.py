from __future__ import annotations

import hashlib
import html
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit

from .bgm_library import BgmTrack, SUPPORTED_AUDIO_SUFFIXES
from .durable_io import fsync_directory
from .execution_fence import (
    ProcessOutputLimitError,
    run_managed_process,
)
from .bgm_selection import BgmQuery
from .media import MediaMetadata, parse_ffprobe_json


DEFAULT_MAX_CANDIDATES = 8
DEFAULT_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_AGENT_CANDIDATES = 20
MAX_AGENT_RESPONSE_BYTES = 200_000
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_PROBE_OUTPUT_BYTES = 1024 * 1024
_PROBE_OUTPUT_CHUNK_BYTES = 64 * 1024
_PROBE_TIMEOUT_SECONDS = 15
_ALLOWED_RIGHTS_STATUS = {"unknown", "cleared", "public_domain"}
_AUDIO_DEMUXERS = {
    ".aac": "aac",
    ".flac": "flac",
    ".m4a": "mov",
    ".mp3": "mp3",
    ".ogg": "ogg",
    ".wav": "wav",
}
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
        for field in ("id", "title", "provider"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise BgmSearchError("candidate id, title, and provider are required")
            object.__setattr__(self, field, value.strip())
        for field, label in (
            ("source_page_url", "source page"),
            ("download_url", "download"),
        ):
            value = getattr(self, field)
            if not isinstance(value, str):
                raise BgmSearchError(f"{label} URL must use http or https")
            normalized_url = value.strip()
            _validate_http_url(normalized_url, label)
            object.__setattr__(self, field, normalized_url)
        for field in ("creator", "license"):
            value = getattr(self, field)
            if value is not None and not isinstance(value, str):
                raise BgmSearchError(f"{field} must be a string")
            normalized_value = (value.strip() or None) if value else None
            object.__setattr__(self, field, normalized_value)
        for field in ("subjects", "moods", "template_tags"):
            value = getattr(self, field)
            if not isinstance(value, (tuple, list)):
                raise BgmSearchError(f"{field} must be a sequence of strings")
            object.__setattr__(self, field, _normalize_tags(value, field))
        if not isinstance(self.energy, str):
            raise BgmSearchError("energy must be a string")
        object.__setattr__(self, "energy", self.energy.strip() or "low-medium")
        if self.tempo_bpm is not None and (
            isinstance(self.tempo_bpm, bool)
            or not isinstance(self.tempo_bpm, (int, float))
            or not math.isfinite(float(self.tempo_bpm))
        ):
            raise BgmSearchError("tempo_bpm must be a finite number or null")
        if self.tempo_bpm is not None:
            object.__setattr__(self, "tempo_bpm", float(self.tempo_bpm))
        if not isinstance(self.instrumental, bool) or not isinstance(self.loopable, bool):
            raise BgmSearchError("instrumental and loopable must be booleans")
        rights_status = _normalized_rights(self.rights_status)
        if not _has_known_license(self.license):
            rights_status = "unknown"
        object.__setattr__(self, "rights_status", rights_status)


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


class ResponseLike(Protocol):
    status: int
    headers: Any

    def read(self, size: int = -1) -> bytes: ...
    def __enter__(self) -> "ResponseLike": ...
    def __exit__(self, *args: Any) -> bool | None: ...


Resolver = Callable[[str, int], tuple[str, ...]]
Opener = Callable[..., ResponseLike]


class _PinnedResponse:
    def __init__(
        self,
        response: http.client.HTTPResponse,
        connection: http.client.HTTPConnection,
    ) -> None:
        self._response = response
        self._connection = connection
        self.status = response.status
        self.headers = response.headers

    def read(self, size: int = -1) -> bytes:
        return self._response.read(size)

    def __enter__(self) -> "_PinnedResponse":
        return self

    def __exit__(self, *_: Any) -> bool:
        try:
            self._response.close()
        finally:
            self._connection.close()
        return False


def _system_resolver(host: str, port: int) -> tuple[str, ...]:
    try:
        records = socket.getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise BgmSearchError(f"could not resolve request host: {host}") from exc
    addresses: list[str] = []
    for record in records:
        address = str(record[4][0])
        if address not in addresses:
            addresses.append(address)
    return tuple(addresses)


def _default_pinned_opener(
    url: str,
    *,
    resolved_ip: str,
    server_hostname: str,
    timeout: float,
) -> ResponseLike:
    parsed = urlsplit(url)
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    connection = http.client.HTTPConnection(
        server_hostname,
        port=port,
        timeout=timeout,
    )
    raw_socket: socket.socket | None = None
    try:
        raw_socket = socket.create_connection((resolved_ip, port), timeout=timeout)
        if parsed.scheme.lower() == "https":
            context = ssl.create_default_context()
            raw_socket = context.wrap_socket(
                raw_socket,
                server_hostname=server_hostname,
            )
        connection.sock = raw_socket
        target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        try:
            is_ipv6 = ipaddress.ip_address(server_hostname).version == 6
        except ValueError:
            is_ipv6 = False
        header_host = f"[{server_hostname}]" if is_ipv6 else server_hostname
        host_header = (
            header_host if port == default_port else f"{header_host}:{port}"
        )
        connection.request(
            "GET",
            target,
            headers={
                "Accept-Encoding": "identity",
                "Connection": "close",
                "Host": host_header,
            },
        )
        return _PinnedResponse(connection.getresponse(), connection)
    except Exception:
        connection.close()
        if raw_socket is not None:
            raw_socket.close()
        raise


def _validate_http_url(value: str, label: str) -> None:
    if not isinstance(value, str):
        raise BgmSearchError(f"{label} URL must use http or https")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise BgmSearchError(f"{label} URL must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise BgmSearchError(f"{label} URL must not contain userinfo")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    if not address.is_global:
        raise BgmSearchError(f"{label} URL host must be public")


def _header(response: ResponseLike, name: str) -> str | None:
    headers = response.headers
    if hasattr(headers, "get"):
        value = headers.get(name)
        if value is None and isinstance(headers, dict):
            for key, candidate in headers.items():
                if str(key).casefold() == name.casefold():
                    value = candidate
                    break
        return str(value) if value is not None else None
    return None


def _validated_address(
    url: str,
    resolver: Resolver,
    allowed_hosts: set[str],
) -> tuple[str, str]:
    _validate_http_url(url, "request")
    parsed = urlsplit(url)
    host = parsed.hostname
    assert host is not None
    if host.casefold() not in allowed_hosts:
        raise BgmSearchError("request host is not approved")
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        addresses = tuple(resolver(host, port))
    except BgmSearchError:
        raise
    except Exception as exc:
        raise BgmSearchError(f"could not resolve request host: {host}") from exc
    if not addresses:
        raise BgmSearchError(f"request host resolved no addresses: {host}")
    for address in addresses:
        try:
            parsed_address = ipaddress.ip_address(address)
        except ValueError as exc:
            raise BgmSearchError(
                f"request host resolved an invalid address: {host}"
            ) from exc
        if not parsed_address.is_global:
            raise BgmSearchError(
                f"request host resolved a non-global address: {host}"
            )
    return host, addresses[0]


def _request_with_redirects(
    url: str,
    *,
    opener: Opener,
    resolver: Resolver,
    allowed_hosts: set[str],
    timeout: float,
    consume: Callable[[ResponseLike, str], Any],
) -> Any:
    current_url = url
    for redirect_count in range(_MAX_REDIRECTS + 1):
        host, resolved_ip = _validated_address(
            current_url,
            resolver,
            allowed_hosts,
        )
        response = opener(
            current_url,
            resolved_ip=resolved_ip,
            server_hostname=host,
            timeout=timeout,
        )
        with response as active_response:
            status = int(getattr(active_response, "status", 200))
            if status in _REDIRECT_STATUSES:
                location = _header(active_response, "Location")
                if not location:
                    raise BgmSearchError("redirect response is missing Location")
                if redirect_count >= _MAX_REDIRECTS:
                    raise BgmSearchError("too many redirects")
                current_url = urljoin(current_url, location)
                _validate_http_url(current_url, "redirect")
                continue
            if status < 200 or status >= 300:
                raise BgmSearchError(f"HTTP request failed with status {status}")
            return consume(active_response, current_url)
    raise BgmSearchError("too many redirects")


def _read_bounded(response: ResponseLike, max_bytes: int) -> bytes:
    content_length = _header(response, "Content-Length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise BgmSearchError("invalid Content-Length") from exc
        if declared_length < 0 or declared_length > max_bytes:
            raise BgmSearchError("response exceeds maximum size")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(1024 * 1024, max_bytes - total + 1))
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise BgmSearchError("response must contain bytes")
        total += len(chunk)
        if total > max_bytes:
            raise BgmSearchError("response exceeds maximum size")
        chunks.append(chunk)
    return b"".join(chunks)


def _normalize_tags(value: Iterable[Any], field: str) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise BgmSearchError(f"{field} must contain strings")
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return tuple(normalized)


def _string_tuple(
    value: Any,
    field: str,
    default: Iterable[str] = (),
) -> tuple[str, ...]:
    if value is None:
        return tuple(default)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
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


def _candidate_from_mapping(
    value: dict[str, Any],
    *,
    default_provider: str | None = None,
) -> OnlineBgmCandidate:
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
    candidate_id = value.get("id")
    if candidate_id is not None and (
        not isinstance(candidate_id, str) or not candidate_id.strip()
    ):
        raise BgmSearchError("candidate id must be a non-empty string")
    energy = value.get("energy", "low-medium")
    if not isinstance(energy, str):
        raise BgmSearchError("candidate energy must be a string")
    tempo = value.get("tempo_bpm")
    if tempo is not None and (
        isinstance(tempo, bool)
        or not isinstance(tempo, (int, float))
        or not math.isfinite(float(tempo))
    ):
        raise BgmSearchError("tempo_bpm must be a finite number or null")
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
        id=candidate_id
        or _candidate_id(provider.strip(), source_page_url, download_url),
        title=title.strip(),
        creator=_optional_string(value.get("creator"), "creator"),
        source_page_url=source_page_url,
        download_url=download_url,
        provider=provider.strip(),
        license=license_name,
        rights_status=rights_status,
        subjects=_string_tuple(value.get("subjects"), "subjects"),
        moods=_string_tuple(value.get("moods"), "moods"),
        energy=energy,
        tempo_bpm=float(tempo) if tempo is not None else None,
        instrumental=instrumental,
        template_tags=_string_tuple(value.get("template_tags"), "template_tags"),
        loopable=loopable,
    )


def _wikimedia_search(
    query: BgmQuery,
    provider: dict[str, Any],
    opener: Opener,
    resolver: Resolver,
    max_response_bytes: int,
) -> list[OnlineBgmCandidate]:
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
    payload_bytes = _request_with_redirects(
        endpoint,
        opener=opener,
        resolver=resolver,
        allowed_hosts={"commons.wikimedia.org"},
        timeout=15,
        consume=lambda response, _: _read_bounded(response, max_response_bytes),
    )
    payload = json.loads(
        payload_bytes.decode("utf-8"),
        parse_constant=lambda _: _reject_non_finite_json_constant(),
    )
    if not isinstance(payload, dict):
        raise BgmSearchError("provider response must be an object")
    query_payload = payload.get("query")
    if not isinstance(query_payload, dict):
        raise BgmSearchError("provider query response must be an object")
    pages = query_payload.get("pages", {})
    if not isinstance(pages, (dict, list)):
        raise BgmSearchError("provider pages must be an object or list")
    records = pages.values() if isinstance(pages, dict) else pages
    candidates: list[OnlineBgmCandidate] = []
    for page in records:
        if not isinstance(page, dict):
            raise BgmSearchError("provider page must be an object")
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
        license_name = _clean_metadata_value(
            license_value.get("value")
            if isinstance(license_value, dict)
            else None
        )
        source_page_url = "https://commons.wikimedia.org/wiki/" + quote(
            title.replace(" ", "_"),
            safe="_:()",
        )
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
    opener: Opener = _default_pinned_opener,
    *,
    resolver: Resolver = _system_resolver,
) -> OnlineBgmCandidates:
    """Query enabled core providers without allowing one provider failure to abort search."""
    providers = config.get("providers", [])
    if not isinstance(providers, list):
        raise BgmSearchError("providers must be a list")
    candidates: list[OnlineBgmCandidate] = []
    warnings: list[ProviderWarning] = []
    configured_max = int(config.get("max_candidates", DEFAULT_MAX_CANDIDATES))
    max_response_bytes = int(
        config.get(
            "max_provider_response_bytes",
            DEFAULT_MAX_PROVIDER_RESPONSE_BYTES,
        )
    )
    if max_response_bytes < 1:
        raise BgmSearchError("max_provider_response_bytes must be positive")
    for provider in providers:
        if not isinstance(provider, dict) or not provider.get("enabled", False):
            continue
        provider_type = str(provider.get("type", ""))
        if provider_type != "wikimedia":
            warnings.append(ProviderWarning(provider_type or "unknown", "unsupported_provider"))
            continue
        try:
            provider_candidates = _wikimedia_search(
                query,
                provider,
                opener,
                resolver,
                max_response_bytes,
            )
        except (
            BgmSearchError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ):
            warnings.append(ProviderWarning(provider_type, "provider_error"))
            continue
        candidates.extend(provider_candidates)
        if len(candidates) >= configured_max:
            break
    return OnlineBgmCandidates(candidates[:max(0, configured_max)], warnings)


def _response_content_type(response: ResponseLike) -> str:
    headers = getattr(response, "headers", {})
    if hasattr(headers, "get_content_type"):
        return str(headers.get_content_type()).casefold()
    return str(_header(response, "Content-Type") or "").split(
        ";", 1
    )[0].strip().casefold()


def _suffix_for_download(url: str, content_type: str) -> str:
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in SUPPORTED_AUDIO_SUFFIXES:
        return suffix
    return _CONTENT_TYPE_SUFFIXES.get(content_type, "")


def _reject_obvious_non_audio_signature(path: Path) -> None:
    with path.open("rb") as handle:
        prefix = handle.read(512)
    lowered = prefix.lstrip().lower()
    signatures = (
        b"<!doctype html",
        b"<html",
        b"<?xml",
        b"pk\x03\x04",
        b"rar!\x1a\x07",
        b"7z\xbc\xaf\x27\x1c",
        b"\x1f\x8b",
    )
    if any(lowered.startswith(signature) for signature in signatures):
        raise BgmSearchError("download has a non-audio signature")
    if len(prefix) >= 262 and prefix[257:262] == b"ustar":
        raise BgmSearchError("download has an archive signature")


def download_candidate(
    candidate: OnlineBgmCandidate,
    output_dir: Path,
    opener: Opener = _default_pinned_opener,
    *,
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    allowed_hosts: Iterable[str] | None = None,
    resolver: Resolver = _system_resolver,
    output_name: str | None = None,
) -> Path:
    """Download one candidate into a run-owned directory with bounded IO."""
    if max_download_bytes < 1:
        raise BgmSearchError("max_download_bytes must be positive")
    if output_name is not None and not re.fullmatch(
        r"candidate-[0-9a-f]{64}",
        output_name,
    ):
        raise BgmSearchError("output_name must be a candidate fingerprint")
    _validate_http_url(candidate.download_url, "download")
    initial_host = urlsplit(candidate.download_url).hostname
    assert initial_host is not None
    approved_hosts = {host.casefold() for host in (allowed_hosts or (initial_host,))}
    root = output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        output_path: Path | None = None

        def consume(response: ResponseLike, final_url: str) -> Path:
            nonlocal temporary_path, output_path
            content_type = _response_content_type(response)
            suffix = _suffix_for_download(final_url, content_type)
            if (
                not suffix
                or content_type not in _CONTENT_TYPE_SUFFIXES
                and not content_type.startswith("audio/")
            ):
                raise BgmSearchError("download content is not supported audio")
            basename = output_name or f"bgm-{uuid.uuid4().hex}"
            temporary_path = root / f".{basename}{suffix}.part"
            output_path = root / f"{basename}{suffix}"
            temporary_path.unlink(missing_ok=True)
            digest = hashlib.sha256()
            content_length = _header(response, "Content-Length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError as exc:
                    raise BgmSearchError("invalid Content-Length") from exc
                if declared_length < 0 or declared_length > max_download_bytes:
                    raise BgmSearchError("download exceeds maximum size")
            total = 0
            with temporary_path.open("xb") as handle:
                while chunk := response.read(
                    min(1024 * 1024, max_download_bytes - total + 1)
                ):
                    if not isinstance(chunk, bytes):
                        raise BgmSearchError(
                            "download response must contain bytes"
                        )
                    total += len(chunk)
                    if total > max_download_bytes:
                        raise BgmSearchError("download exceeds maximum size")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if total == 0:
                raise BgmSearchError("download is empty")
            _reject_obvious_non_audio_signature(temporary_path)
            if digest.hexdigest() != hashlib.sha256(
                temporary_path.read_bytes()
            ).hexdigest():
                raise BgmSearchError("download hash verification failed")
            os.replace(temporary_path, output_path)
            fsync_directory(root)
            return output_path

        downloaded_path = _request_with_redirects(
            candidate.download_url,
            opener=opener,
            resolver=resolver,
            allowed_hosts=approved_hosts,
            timeout=30,
            consume=consume,
        )
        if output_path is None or downloaded_path != output_path:
            raise BgmSearchError("download did not produce an output file")
        return downloaded_path
    except Exception as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if isinstance(exc, BgmSearchError):
            raise
        raise BgmSearchError(f"download failed: {exc}") from exc


def parse_agent_candidates(
    response: str,
    *,
    max_candidates: int = MAX_AGENT_CANDIDATES,
    max_response_bytes: int = MAX_AGENT_RESPONSE_BYTES,
) -> list[OnlineBgmCandidate]:
    encoded = response.encode("utf-8")
    if len(encoded) > max_response_bytes:
        raise BgmSearchError(f"agent response exceeds {max_response_bytes} bytes")
    try:
        payload = json.loads(
            response,
            parse_constant=lambda _: _reject_non_finite_json_constant(),
        )
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
    if len(candidates) > max_candidates:
        raise BgmSearchError(f"agent response accepts at most {max_candidates} candidates")
    parsed: list[OnlineBgmCandidate] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise BgmSearchError("agent candidate must be an object")
        parsed_candidate = _candidate_from_mapping(candidate)
        for label, raw_url in (
            ("source page", parsed_candidate.source_page_url),
            ("download", parsed_candidate.download_url),
        ):
            parsed_url = urlsplit(raw_url)
            if parsed_url.username is not None or parsed_url.password is not None:
                raise BgmSearchError(
                    f"agent {label} URL must not contain userinfo"
                )
            if parsed_url.query:
                raise BgmSearchError(
                    f"agent {label} URL must not contain a query"
                )
            if parsed_url.fragment:
                raise BgmSearchError(
                    f"agent {label} URL must not contain a fragment"
                )
        parsed.append(parsed_candidate)
    return parsed


def _reject_non_finite_json_constant() -> None:
    raise BgmSearchError("agent numeric fields must be finite")


def _run_bounded_process(
    command: list[str],
    *,
    max_output_bytes: int = _MAX_PROBE_OUTPUT_BYTES,
    timeout_seconds: int = _PROBE_TIMEOUT_SECONDS,
) -> tuple[bytes, bytes]:
    try:
        completed = run_managed_process(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            max_output_bytes=max_output_bytes,
            timeout=timeout_seconds,
            check=True,
        )
    except ProcessOutputLimitError as exc:
        raise BgmSearchError(
            "candidate media probe output exceeds maximum size"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise BgmSearchError("candidate media probe timed out") from exc
    except subprocess.CalledProcessError as exc:
        raw_stderr = exc.stderr or b""
        detail = (
            raw_stderr.decode("utf-8", errors="replace").strip()
            if isinstance(raw_stderr, bytes)
            else str(raw_stderr).strip()
        )
        message = "candidate media probe failed"
        if detail:
            message += f": {detail[:500]}"
        raise BgmSearchError(message) from exc
    except OSError as exc:
        raise BgmSearchError("candidate media probe failed") from exc
    stdout = completed.stdout
    stderr = completed.stderr
    if not isinstance(stdout, bytes) or not isinstance(stderr, bytes):
        raise BgmSearchError(
            "candidate media probe output must be bytes"
        )
    return stdout, stderr


def _bounded_probe_media(path: Path) -> MediaMetadata:
    try:
        demuxer = _AUDIO_DEMUXERS[path.suffix.lower()]
    except KeyError as exc:
        raise BgmSearchError(
            "candidate media probe has unsupported audio suffix"
        ) from exc
    stdout, _ = _run_bounded_process(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-probesize",
            "5000000",
            "-analyzeduration",
            "5000000",
            "-f",
            demuxer,
            "-i",
            str(path),
            "-show_entries",
            "stream=codec_type,codec_name,duration:format=duration",
            "-of",
            "json",
        ],
    )
    try:
        payload = json.loads(
            stdout.decode("utf-8"),
            parse_constant=lambda _: _reject_non_finite_json_constant(),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, BgmSearchError) as exc:
        raise BgmSearchError("candidate media probe returned invalid data") from exc
    if not isinstance(payload, dict):
        raise BgmSearchError("candidate media probe returned invalid data")
    return parse_ffprobe_json(payload)


def candidate_to_track(
    candidate: OnlineBgmCandidate,
    downloaded_path: Path,
    *,
    probe: Callable[[Path], MediaMetadata] = _bounded_probe_media,
) -> BgmTrack:
    """Create a selectable BGM track while retaining online provenance."""
    path = downloaded_path.resolve()
    if not path.is_file():
        raise BgmSearchError("downloaded candidate file is missing")
    if path.suffix.lower() not in SUPPORTED_AUDIO_SUFFIXES:
        raise BgmSearchError("downloaded candidate has unsupported audio suffix")
    _reject_obvious_non_audio_signature(path)
    try:
        metadata = probe(path)
    except BgmSearchError:
        raise
    except Exception as exc:
        raise BgmSearchError("candidate media probe failed") from exc
    if metadata.kind != "audio" or metadata.duration_ms <= 0:
        raise BgmSearchError("candidate media probe did not find decodable audio")
    return BgmTrack(
        id=candidate.id,
        path=path,
        metadata_path=path,
        level="online",
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        title=candidate.title,
        creator=candidate.creator,
        source_url=candidate.source_page_url,
        provider=candidate.provider,
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
        metadata_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        duration_ms=metadata.duration_ms,
    )
