import io
import json
import subprocess
from pathlib import Path

import pytest

from videocreator.bgm_search import (
    BgmSearchError,
    OnlineBgmCandidate,
    candidate_to_track,
    download_candidate,
    parse_agent_candidates,
    search_configured_providers,
)
from videocreator.bgm_selection import BgmQuery
from videocreator.media import MediaMetadata


PUBLIC_IP = "93.184.216.34"


class FakeResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ):
        self.payload = payload
        self.status = status
        self.headers = headers or {"Content-Type": "audio/mpeg"}
        self.entered = False
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            value, self.payload = self.payload, b""
            return value
        value, self.payload = self.payload[:size], self.payload[size:]
        return value

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *_):
        self.closed = True
        return False


class RecordingOpener:
    def __init__(self, *responses: FakeResponse):
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        url: str,
        *,
        resolved_ip: str,
        server_hostname: str,
        timeout: float,
    ) -> FakeResponse:
        self.calls.append({
            "url": url,
            "resolved_ip": resolved_ip,
            "server_hostname": server_hostname,
            "timeout": timeout,
        })
        return self.responses.pop(0)


class FakeProcess:
    def __init__(
        self,
        stdout: bytes,
        stderr: bytes = b"",
        *,
        returncode: int = 0,
    ):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode
        self.killed = False

    def wait(self, timeout=None):
        del timeout
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


def public_resolver(host: str, port: int) -> tuple[str, ...]:
    del host, port
    return (PUBLIC_IP,)


def query() -> BgmQuery:
    return BgmQuery(
        subjects=("technology",),
        moods=("reflective",),
        template_id="chaos-museum",
        terms_zh=("技术",),
        terms_en=("technology", "reflective"),
    )


def candidate(**overrides) -> OnlineBgmCandidate:
    values = {
        "id": "online-track",
        "title": "Online Track",
        "creator": "Creator",
        "source_page_url": "https://media.example.test/source",
        "download_url": "https://media.example.test/file.mp3",
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


def test_download_resolves_and_pins_validated_address_with_tls_hostname(tmp_path):
    response = FakeResponse(b"ID3audio")
    opener = RecordingOpener(response)

    path = download_candidate(
        candidate(),
        tmp_path,
        opener=opener,
        resolver=public_resolver,
    )

    assert path.is_file()
    assert opener.calls == [{
        "url": "https://media.example.test/file.mp3",
        "resolved_ip": PUBLIC_IP,
        "server_hostname": "media.example.test",
        "timeout": 30,
    }]
    assert response.closed


def test_download_rejects_dns_resolution_containing_non_global_address_before_open(tmp_path):
    opener = RecordingOpener(FakeResponse(b"unused"))

    with pytest.raises(BgmSearchError, match="non-global"):
        download_candidate(
            candidate(),
            tmp_path,
            opener=opener,
            resolver=lambda *_: (PUBLIC_IP, "10.0.0.7"),
        )

    assert opener.calls == []


def test_redirect_hop_is_resolved_before_following_and_first_response_closes(tmp_path):
    redirect = FakeResponse(
        b"",
        status=302,
        headers={"Location": "https://private.example.test/secret.mp3"},
    )
    opener = RecordingOpener(redirect, FakeResponse(b"must-not-open"))

    def resolver(host: str, _port: int) -> tuple[str, ...]:
        return ("10.0.0.7",) if host == "private.example.test" else (PUBLIC_IP,)

    with pytest.raises(BgmSearchError, match="non-global"):
        download_candidate(
            candidate(),
            tmp_path,
            opener=opener,
            resolver=resolver,
            allowed_hosts=("media.example.test", "private.example.test"),
        )

    assert len(opener.calls) == 1
    assert redirect.closed


def test_download_rejects_html_signature_despite_audio_mime_and_closes_response(tmp_path):
    response = FakeResponse(b"<!doctype html><title>login</title>")

    with pytest.raises(BgmSearchError, match="signature"):
        download_candidate(
            candidate(),
            tmp_path,
            opener=RecordingOpener(response),
            resolver=public_resolver,
        )

    assert response.closed
    assert list(tmp_path.iterdir()) == []


def test_candidate_promotion_requires_successful_audio_probe(tmp_path):
    path = tmp_path / "candidate.mp3"
    path.write_bytes(b"ID3audio")

    with pytest.raises(BgmSearchError, match="media probe"):
        candidate_to_track(
            candidate(),
            path,
            probe=lambda _: (_ for _ in ()).throw(RuntimeError("bad media")),
        )

    track = candidate_to_track(
        candidate(),
        path,
        probe=lambda _: MediaMetadata("audio", "mp3", None, None, 1000),
    )
    assert track.path == path


def test_candidate_promotion_rejects_archive_signature_before_probe(tmp_path):
    path = tmp_path / "candidate.mp3"
    path.write_bytes(b"PK\x03\x04archive")
    probed = False

    def probe(_: Path) -> MediaMetadata:
        nonlocal probed
        probed = True
        return MediaMetadata("audio", "mp3", None, None, 1000)

    with pytest.raises(BgmSearchError, match="signature"):
        candidate_to_track(candidate(), path, probe=probe)

    assert not probed


def test_default_media_probe_bounds_probe_size_analysis_time_and_runtime(
    tmp_path, monkeypatch
):
    path = tmp_path / "disguised-playlist.mp3"
    path.write_bytes(b"#EXTM3U\nhttps://private.example.test/audio.ts\n")
    calls = []
    payload = {
        "streams": [{
            "codec_type": "audio",
            "codec_name": "mp3",
            "duration": "1.0",
        }],
        "format": {"duration": "1.0"},
    }
    stdout = json.dumps(payload).encode()

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return type("Completed", (), {"stdout": stdout})()

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess(stdout)

    monkeypatch.setattr("videocreator.bgm_search.subprocess.run", runner)
    monkeypatch.setattr("videocreator.bgm_search.subprocess.Popen", popen)

    track = candidate_to_track(candidate(), path)

    command, kwargs = calls[0]
    assert command[command.index("-protocol_whitelist") + 1] == "file"
    assert command[command.index("-f") + 1] == "mp3"
    assert command.index("-f") < command.index("-i")
    assert "-show_entries" in command
    assert command[command.index("-probesize") + 1] == "5000000"
    assert command[command.index("-analyzeduration") + 1] == "5000000"
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert track.path == path


@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
def test_media_probe_terminates_while_output_exceeds_limit(
    tmp_path, monkeypatch, stream_name
):
    path = tmp_path / "candidate.mp3"
    path.write_bytes(b"ID3audio")
    processes = []

    def runner(*_args, **_kwargs):
        return type(
            "Completed",
            (),
            {"stdout": b"x" * (1024 * 1024 + 1)},
        )()

    def popen(*_args, **_kwargs):
        oversized = b"x" * (1024 * 1024 + 1)
        process = FakeProcess(
            oversized if stream_name == "stdout" else b"",
            oversized if stream_name == "stderr" else b"",
        )
        processes.append(process)
        return process

    monkeypatch.setattr("videocreator.bgm_search.subprocess.run", runner)
    monkeypatch.setattr("videocreator.bgm_search.subprocess.Popen", popen)

    with pytest.raises(BgmSearchError, match="output exceeds"):
        candidate_to_track(candidate(), path)

    assert processes
    assert processes[0].killed


def test_pinned_https_opener_brackets_ipv6_host_and_preserves_sni(monkeypatch):
    from videocreator import bgm_search

    ipv6 = "2606:4700:4700::1111"
    sockets = []
    contexts = []
    connections = []

    class FakeSocket:
        def close(self):
            return None

    class FakeContext:
        def wrap_socket(self, value, *, server_hostname):
            self.server_hostname = server_hostname
            return value

    class FakeRawResponse:
        status = 200
        headers = {}

        def read(self, _size=-1):
            return b""

        def close(self):
            return None

    class FakeConnection:
        def __init__(self, host, *, port, timeout):
            self.host = host
            self.port = port
            self.timeout = timeout
            self.sock = None
            self.request_headers = None
            connections.append(self)

        def request(self, _method, _target, *, headers):
            self.request_headers = headers

        def getresponse(self):
            return FakeRawResponse()

        def close(self):
            return None

    def connect(address, *, timeout):
        sockets.append((address, timeout))
        return FakeSocket()

    def create_context():
        context = FakeContext()
        contexts.append(context)
        return context

    monkeypatch.setattr(bgm_search.socket, "create_connection", connect)
    monkeypatch.setattr(bgm_search.ssl, "create_default_context", create_context)
    monkeypatch.setattr(bgm_search.http.client, "HTTPConnection", FakeConnection)

    response = bgm_search._default_pinned_opener(
        f"https://[{ipv6}]:8443/audio.mp3",
        resolved_ip=ipv6,
        server_hostname=ipv6,
        timeout=30,
    )
    with response:
        pass

    assert sockets == [((ipv6, 8443), 30)]
    assert contexts[0].server_hostname == ipv6
    assert connections[0].request_headers["Host"] == f"[{ipv6}]:8443"


@pytest.mark.parametrize("payload", [b"[]", b'{"query": []}'])
def test_malformed_provider_shapes_become_structured_warnings(payload):
    response = FakeResponse(payload, headers={"Content-Type": "application/json"})
    result = search_configured_providers(
        query(),
        {"providers": [{"type": "wikimedia", "enabled": True}]},
        opener=RecordingOpener(response),
        resolver=public_resolver,
    )

    assert result == []
    assert result.warnings == (
        {"provider": "wikimedia", "code": "provider_error"},
    )
    assert response.closed


def test_provider_content_length_limit_rejects_before_body_read_and_closes():
    response = FakeResponse(
        b'{"query": {"pages": []}}',
        headers={"Content-Type": "application/json", "Content-Length": "999"},
    )
    result = search_configured_providers(
        query(),
        {
            "max_provider_response_bytes": 16,
            "providers": [{"type": "wikimedia", "enabled": True}],
        },
        opener=RecordingOpener(response),
        resolver=public_resolver,
    )

    assert result == []
    assert result.warnings[0]["code"] == "provider_error"
    assert response.payload
    assert response.closed


def test_provider_stream_limit_is_enforced_without_content_length():
    response = FakeResponse(
        b"x" * 17,
        headers={"Content-Type": "application/json"},
    )
    result = search_configured_providers(
        query(),
        {
            "max_provider_response_bytes": 16,
            "providers": [{"type": "wikimedia", "enabled": True}],
        },
        opener=RecordingOpener(response),
        resolver=public_resolver,
    )

    assert result == []
    assert result.warnings[0]["code"] == "provider_error"
    assert response.closed


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_agent_json_rejects_non_standard_numeric_constants(constant):
    response = (
        '{"candidates":[{"title":"Track",'
        '"source_page_url":"https://example.test/page",'
        '"download_url":"https://example.test/file.mp3",'
        '"provider":"web","tempo_bpm":' + constant + "}]}"
    )

    with pytest.raises(BgmSearchError, match="finite"):
        parse_agent_candidates(response)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("id", {"not": "a string"}),
        ("energy", {"not": "a string"}),
    ],
)
def test_agent_candidate_does_not_coerce_invalid_invariant_field_types(
    field, invalid_value
):
    payload = {
        "candidates": [{
            "id": "track-id",
            "title": "Track",
            "source_page_url": "https://example.test/page",
            "download_url": "https://example.test/file.mp3",
            "provider": "web",
            "energy": "low-medium",
        }],
    }
    payload["candidates"][0][field] = invalid_value

    with pytest.raises(BgmSearchError, match=field):
        parse_agent_candidates(json.dumps(payload))


def test_direct_candidate_enforces_url_and_normalizes_fields():
    with pytest.raises(BgmSearchError, match="http"):
        candidate(download_url="file:///C:/secret.mp3")

    value = candidate(
        id="  track-id  ",
        title="  Track  ",
        provider="  web  ",
        source_page_url="  https://media.example.test/source  ",
        download_url="  https://media.example.test/file.mp3  ",
        creator="  Creator  ",
        license="  Unknown  ",
        rights_status="CLEARED",
        subjects=(" technology ", "technology", ""),
        moods=(" reflective ",),
        energy=" low-medium ",
        template_tags=(" chaos-museum ",),
    )

    assert value.id == "track-id"
    assert value.title == "Track"
    assert value.provider == "web"
    assert value.source_page_url == "https://media.example.test/source"
    assert value.download_url == "https://media.example.test/file.mp3"
    assert value.creator == "Creator"
    assert value.license == "Unknown"
    assert value.rights_status == "unknown"
    assert value.subjects == ("technology",)
    assert value.moods == ("reflective",)
    assert value.energy == "low-medium"
    assert value.template_tags == ("chaos-museum",)


@pytest.mark.parametrize(
    "field",
    ["source_page_url", "download_url"],
)
def test_candidate_rejects_url_userinfo(field):
    values = {
        field: "https://user:password@media.example.test/file.mp3",
    }

    with pytest.raises(BgmSearchError, match="userinfo"):
        candidate(**values)


def test_download_can_use_deterministic_candidate_filename(tmp_path):
    path = download_candidate(
        candidate(),
        tmp_path,
        opener=RecordingOpener(FakeResponse(b"ID3audio")),
        resolver=public_resolver,
        output_name="candidate-" + "a" * 64,
    )

    assert path.name == f"candidate-{'a' * 64}.mp3"
    assert not list(tmp_path.glob("bgm-*"))
