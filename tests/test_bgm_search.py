import hashlib
import json
from pathlib import Path

import pytest


class FakeResponse:
    def __init__(self, payload: bytes, url: str = "https://example.test/file.mp3"):
        self.payload = payload
        self.url = url
        self.headers = {"Content-Type": "audio/mpeg"}

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            value, self.payload = self.payload, b""
            return value
        value, self.payload = self.payload[:size], self.payload[size:]
        return value

    def geturl(self) -> str:
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def reflective_query():
    from videocreator.bgm_selection import BgmQuery

    return BgmQuery(
        subjects=("technology",),
        moods=("reflective",),
        template_id="chaos-museum",
        terms_zh=("技术",),
        terms_en=("technology", "reflective"),
    )


def online_candidate(**overrides):
    from videocreator.bgm_search import OnlineBgmCandidate

    values = {
        "id": "online-track",
        "title": "Online Track",
        "creator": "Creator",
        "source_page_url": "https://example.test/source",
        "download_url": "https://example.test/file.mp3",
        "provider": "web",
        "license": None,
        "rights_status": "unknown",
        "subjects": ("technology",),
        "moods": ("reflective",),
        "energy": "low-medium",
        "tempo_bpm": None,
        "instrumental": True,
        "template_tags": (),
        "loopable": True,
    }
    values.update(overrides)
    return OnlineBgmCandidate(**values)


def test_provider_results_are_normalized_without_claiming_unknown_rights():
    from videocreator.bgm_search import search_configured_providers

    payload = {
        "query": {
            "pages": [{
                "title": "File:Reflective audio.ogg",
                "imageinfo": [{
                    "url": "https://upload.wikimedia.org/reflective.ogg",
                    "extmetadata": {
                        "Artist": {"value": "<b>Artist</b>"},
                        "LicenseShortName": {"value": "CC BY 4.0"},
                    },
                }],
            }],
        },
    }

    candidates = search_configured_providers(
        reflective_query(),
        {"providers": [{"type": "wikimedia", "enabled": True}]},
        opener=lambda *_args, **_kwargs: FakeResponse(json.dumps(payload).encode()),
    )

    assert len(candidates) == 1
    assert candidates[0].rights_status == "unknown"
    assert candidates[0].source_page_url.startswith("https://")
    assert candidates[0].download_url.endswith(".ogg")


def test_provider_errors_are_structured_warnings_without_aborting_other_providers():
    from videocreator.bgm_search import search_configured_providers

    values = search_configured_providers(
        reflective_query(),
        {"providers": [{"type": "unsupported", "enabled": True}]},
        opener=lambda *_args, **_kwargs: pytest.fail("opener should not be called"),
    )

    assert values == []
    assert values.warnings == (
        {"provider": "unsupported", "code": "unsupported_provider"},
    )


def test_provider_discards_non_http_download_results():
    from videocreator.bgm_search import search_configured_providers

    payload = {
        "query": {"pages": [{
            "title": "File:Unsafe.mp3",
            "imageinfo": [{"url": "file:///C:/unsafe.mp3", "extmetadata": {}}],
        }]},
    }

    values = search_configured_providers(
        reflective_query(),
        {"providers": [{"type": "wikimedia", "enabled": True}]},
        opener=lambda *_args, **_kwargs: FakeResponse(json.dumps(payload).encode()),
    )

    assert values == []


def test_download_rejects_non_http_url(tmp_path):
    from videocreator.bgm_search import BgmSearchError, download_candidate

    candidate = online_candidate(download_url="file:///C:/secret.mp3")

    with pytest.raises(BgmSearchError, match="http"):
        download_candidate(candidate, tmp_path, opener=lambda *_: None)


def test_candidate_normalizes_non_string_rights_status_to_unknown():
    assert online_candidate(rights_status=42).rights_status == "unknown"


def test_download_rejects_private_network_host(tmp_path):
    from videocreator.bgm_search import BgmSearchError, download_candidate

    candidate = online_candidate(download_url="http://127.0.0.1/secret.mp3")

    with pytest.raises(BgmSearchError, match="public"):
        download_candidate(candidate, tmp_path, opener=lambda *_: None)


def test_download_rejects_redirect_to_non_http_scheme_and_removes_partial_file(tmp_path):
    from videocreator.bgm_search import BgmSearchError, download_candidate

    with pytest.raises(BgmSearchError, match="redirect"):
        download_candidate(
            online_candidate(),
            tmp_path,
            opener=lambda *_args, **_kwargs: FakeResponse(b"audio", "file:///C:/secret.mp3"),
        )

    assert list(tmp_path.iterdir()) == []


def test_download_enforces_size_limit_and_records_sha256(tmp_path):
    from videocreator.bgm_search import BgmSearchError, download_candidate

    oversized = online_candidate()
    with pytest.raises(BgmSearchError, match="maximum size"):
        download_candidate(
            oversized,
            tmp_path,
            opener=lambda *_args, **_kwargs: FakeResponse(b"12345"),
            max_download_bytes=4,
        )
    assert list(tmp_path.iterdir()) == []

    path = download_candidate(
        oversized,
        tmp_path,
        opener=lambda *_args, **_kwargs: FakeResponse(b"audio"),
    )
    assert path.parent == tmp_path
    assert path.name != "file.mp3"
    assert path.read_bytes() == b"audio"


def test_agent_response_must_be_a_bounded_json_candidate_list():
    from videocreator.bgm_search import BgmSearchError, parse_agent_candidates

    value = parse_agent_candidates(json.dumps({"candidates": [{
        "title": "Track",
        "source_page_url": "https://example.test/page",
        "download_url": "https://example.test/file.mp3",
        "provider": "web",
    }]}))

    assert len(value) == 1
    assert value[0].rights_status == "unknown"
    uncertain_license = parse_agent_candidates(json.dumps({"candidates": [{
        "title": "Uncertain Track",
        "source_page_url": "https://example.test/uncertain",
        "download_url": "https://example.test/uncertain.mp3",
        "provider": "web",
        "license": "Unknown",
        "rights_status": "cleared",
    }]}))
    assert uncertain_license[0].rights_status == "unknown"
    with pytest.raises(BgmSearchError, match="unknown top-level"):
        parse_agent_candidates('{"candidates": [], "unexpected": true}')
    with pytest.raises(BgmSearchError, match="at most 20"):
        parse_agent_candidates(json.dumps({"candidates": [{}] * 21}))
    with pytest.raises(BgmSearchError, match="200000"):
        parse_agent_candidates("x" * 200001)


def test_candidate_to_track_preserves_online_provenance(tmp_path):
    from videocreator.bgm_search import candidate_to_track

    path = tmp_path / "safe.mp3"
    path.write_bytes(b"audio")
    candidate = online_candidate(license="CC BY 4.0", rights_status="unknown")

    track = candidate_to_track(candidate, path)

    assert track.level == "online"
    assert track.path == path
    assert track.metadata_path == path
    assert track.source_url == candidate.source_page_url
    assert track.sha256 == hashlib.sha256(b"audio").hexdigest()
