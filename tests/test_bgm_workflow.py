import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from videocreator.bgm_library import BgmTrack
from videocreator.bgm_policy import BgmPolicy
from videocreator.bgm_selection import BgmQuery
from videocreator.interactions import (
    ConsoleInteractionPort,
    DurableInteractionPort,
    InteractionRequired,
)


class Context:
    def __init__(
        self,
        root: Path,
        *,
        project_name: str = "demo",
        run_id: str = "run-1",
    ):
        self.project_name = project_name
        self.run_id = run_id
        self.run_dir = root
        self.state = {"current_stage": "bgm", "status": "running"}

    def save_state(self):
        (self.run_dir / "state.json").write_text(
            json.dumps(self.state),
            encoding="utf-8",
        )


def query() -> BgmQuery:
    return BgmQuery(
        subjects=("technology",),
        moods=("reflective",),
        template_id="documentary",
        terms_zh=("技术",),
        terms_en=("technology", "reflective"),
    )


def track(root: Path, *, track_id: str = "track", rights_status: str = "cleared"):
    path = root / f"{track_id}.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(track_id.encode())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return BgmTrack(
        id=track_id,
        path=path,
        metadata_path=path,
        level="local",
        sha256=digest,
        title=track_id,
        creator=None,
        source_url=None,
        license=None,
        rights_status=rights_status,
        subjects=("technology",),
        moods=("reflective",),
        energy="low-medium",
        tempo_bpm=90,
        instrumental=True,
        template_tags=("documentary",),
        avoid_for=(),
        preferred_start_ms=0,
        loopable=True,
        metadata_sha256=digest,
    )


def request(tmp_path: Path, **overrides):
    from videocreator.bgm_workflow import BgmResolutionRequest

    values = {
        "context": Context(tmp_path),
        "local_tracks": (),
        "query": query(),
        "policy": BgmPolicy(preferred_moods=("reflective",)),
        "provider_config": {"providers": []},
        "download_dir": tmp_path / "visual" / "bgm",
    }
    values.update(overrides)
    return BgmResolutionRequest(**values)


def agent_response() -> str:
    return json.dumps(
        {
            "candidates": [
                {
                    "id": "agent-track",
                    "title": "Agent Track",
                    "source_page_url": "https://example.test/source",
                    "download_url": "https://example.test/track.mp3",
                    "provider": "agent-web-search",
                    "rights_status": "unknown",
                    "subjects": ["technology"],
                    "moods": ["reflective"],
                    "energy": "low-medium",
                    "tempo_bpm": 90,
                    "instrumental": True,
                    "template_tags": ["documentary"],
                }
            ]
        }
    )


def online_candidate(candidate_id="online-track", *, instrumental=True):
    from videocreator.bgm_search import OnlineBgmCandidate

    return OnlineBgmCandidate(
        id=candidate_id,
        title=candidate_id,
        creator=None,
        source_page_url=f"https://example.test/{candidate_id}",
        download_url=f"https://example.test/{candidate_id}.mp3",
        provider="web",
        license="CC BY 4.0",
        rights_status="cleared",
        subjects=("technology",),
        moods=("reflective",),
        energy="low-medium",
        tempo_bpm=90,
        instrumental=instrumental,
        template_tags=("documentary",),
        loopable=True,
    )


def candidate_track(candidate, path: Path) -> BgmTrack:
    value = track(path.parent, track_id=candidate.id, rights_status=candidate.rights_status)
    if value.path != path:
        value.path.unlink(missing_ok=True)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return replace(
        value,
        path=path,
        metadata_path=path,
        level="online",
        sha256=digest,
        metadata_sha256=digest,
        instrumental=candidate.instrumental,
        source_url=candidate.source_page_url,
    )


def test_local_selection_is_first_and_skips_provider(tmp_path, monkeypatch):
    from videocreator.bgm_workflow import resolve_bgm_for_run

    local = track(tmp_path / "local", track_id="local")
    monkeypatch.setattr(
        "videocreator.bgm_workflow.search_configured_providers",
        lambda *_args, **_kwargs: pytest.fail("provider must not run"),
    )

    result = resolve_bgm_for_run(
        request(tmp_path, local_tracks=(local,)),
        ConsoleInteractionPort(),
    )

    assert result.mode == "bgm"
    assert result.source == "local"
    assert result.track.id == local.id
    assert result.track.sha256 == local.sha256
    assert result.track.path.parent == request(
        tmp_path,
        local_tracks=(local,),
    ).download_dir
    assert result.resolution_id
    ledger = json.loads(next(
        (tmp_path / "visual/bgm").glob("bgm-resolution-*.json")
    ).read_text(encoding="utf-8"))
    assert ledger["status"] == "committed"


def test_provider_failure_requests_agent_candidates_in_durable_mode(tmp_path):
    from videocreator.bgm_workflow import resolve_bgm_for_run

    req = request(tmp_path)
    port = DurableInteractionPort()

    with pytest.raises(InteractionRequired) as raised:
        resolve_bgm_for_run(req, port)
    first = raised.value.interaction

    assert first["key"] == "bgm-online-candidates"
    assert first["kind"] == "bgm_candidates"
    assert first["payload"]["schema_version"] == 1
    assert first["payload"]["query"]["template_id"] == "documentary"
    assert first["payload"]["response_schema"]["properties"]["candidates"][
        "maxItems"
    ] == 20
    assert "provider_config" not in first["payload"]

    with pytest.raises(InteractionRequired) as repeated:
        resolve_bgm_for_run(req, port)
    assert repeated.value.interaction == first


def test_console_mode_skips_agent_and_returns_narration_only(tmp_path):
    from videocreator.bgm_workflow import resolve_bgm_for_run

    result = resolve_bgm_for_run(request(tmp_path), ConsoleInteractionPort())

    assert result.mode == "narration_only"
    assert result.track is None
    assert "agent handoff is unavailable" in " ".join(result.warnings).lower()


def test_disabled_policy_returns_narration_only_without_search(tmp_path, monkeypatch):
    from videocreator.bgm_workflow import resolve_bgm_for_run

    monkeypatch.setattr(
        "videocreator.bgm_workflow.search_configured_providers",
        lambda *_args, **_kwargs: pytest.fail("provider must not run"),
    )

    result = resolve_bgm_for_run(
        request(tmp_path, policy=BgmPolicy(enabled=False)),
        DurableInteractionPort(),
    )

    assert result.mode == "narration_only"
    assert result.track is None
    assert any("disabled" in warning.lower() for warning in result.warnings)


def test_agent_response_resumes_through_download_validation_and_scoring(
    tmp_path,
    monkeypatch,
):
    from videocreator import bgm_workflow

    req = request(tmp_path)
    port = DurableInteractionPort()
    with pytest.raises(InteractionRequired) as raised:
        bgm_workflow.resolve_bgm_for_run(req, port)
    port.submit(req.context, raised.value.interaction["id"], agent_response())

    def download(candidate, output_dir, **_kwargs):
        path = output_dir / f"{candidate.id}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return path

    monkeypatch.setattr(bgm_workflow, "download_candidate", download)
    monkeypatch.setattr(bgm_workflow, "candidate_to_track", candidate_track)

    result = bgm_workflow.resolve_bgm_for_run(req, port)

    assert result.mode == "bgm"
    assert result.source == "agent"
    assert result.track.id == "agent-track"
    assert any("rights status is unknown" in warning for warning in result.warnings)
    assert "interaction_answers" in req.context.state
    assert "submitted_interactions" in req.context.state
    assert "pending_interaction" not in req.context.state
    ledger = json.loads(
        bgm_workflow._resolution_ledger_path(req).read_text(encoding="utf-8")
    )
    assert ledger["status"] == "committed"

    assert bgm_workflow.acknowledge_bgm_resolution(
        req,
        port,
        result.resolution_id,
    )
    assert "interaction_answers" not in req.context.state
    assert "submitted_interactions" not in req.context.state
    acknowledged = json.loads(
        bgm_workflow._resolution_ledger_path(req).read_text(encoding="utf-8")
    )
    assert acknowledged["status"] == "acknowledged"
    assert bgm_workflow.acknowledge_bgm_resolution(
        req,
        port,
        result.resolution_id,
    ) is False


def test_invalid_agent_response_falls_back_then_acknowledges_answer(tmp_path):
    from videocreator.bgm_workflow import (
        acknowledge_bgm_resolution,
        resolve_bgm_for_run,
    )

    req = request(tmp_path)
    port = DurableInteractionPort()
    with pytest.raises(InteractionRequired) as raised:
        resolve_bgm_for_run(req, port)
    port.submit(req.context, raised.value.interaction["id"], "not-json")

    result = resolve_bgm_for_run(req, port)

    assert result.mode == "narration_only"
    assert any("agent response" in warning.lower() for warning in result.warnings)
    assert "interaction_answers" in req.context.state
    assert acknowledge_bgm_resolution(req, port, result.resolution_id)
    assert "interaction_answers" not in req.context.state


def test_provider_candidates_are_tried_before_agent_handoff(tmp_path, monkeypatch):
    from videocreator import bgm_workflow
    from videocreator.bgm_search import OnlineBgmCandidate

    candidate = OnlineBgmCandidate(
        id="provider-track",
        title="Provider Track",
        creator=None,
        source_page_url="https://example.test/source",
        download_url="https://example.test/track.mp3",
        provider="wikimedia",
        license="CC BY 4.0",
        rights_status="cleared",
        subjects=("technology",),
        moods=("reflective",),
        energy="low-medium",
        tempo_bpm=90,
        instrumental=True,
        template_tags=("documentary",),
        loopable=True,
    )
    monkeypatch.setattr(
        bgm_workflow,
        "search_configured_providers",
        lambda *_args, **_kwargs: [candidate],
    )
    monkeypatch.setattr(
        bgm_workflow,
        "download_candidate",
        lambda _candidate, output_dir, **_kwargs: track(
            output_dir, track_id="download"
        ).path,
    )
    monkeypatch.setattr(
        bgm_workflow,
        "candidate_to_track",
        lambda _candidate, path: track(path.parent, track_id="provider-track"),
    )

    req = request(tmp_path)
    result = bgm_workflow.resolve_bgm_for_run(req, DurableInteractionPort())

    assert result.mode == "bgm"
    assert result.source == "provider"
    assert "pending_interaction" not in req.context.state


def test_committed_resolution_is_replayed_without_search_or_download(
    tmp_path,
    monkeypatch,
):
    from videocreator import bgm_workflow

    candidate = online_candidate()
    monkeypatch.setattr(
        bgm_workflow,
        "search_configured_providers",
        lambda *_args, **_kwargs: [candidate],
    )

    def download(_candidate, output_dir, **_kwargs):
        path = output_dir / "first.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return path

    monkeypatch.setattr(bgm_workflow, "download_candidate", download)
    monkeypatch.setattr(bgm_workflow, "candidate_to_track", candidate_track)
    req = request(tmp_path)
    first = bgm_workflow.resolve_bgm_for_run(req, DurableInteractionPort())

    monkeypatch.setattr(
        bgm_workflow,
        "search_configured_providers",
        lambda *_args, **_kwargs: pytest.fail("search must not replay"),
    )
    monkeypatch.setattr(
        bgm_workflow,
        "download_candidate",
        lambda *_args, **_kwargs: pytest.fail("download must not replay"),
    )
    repeated = bgm_workflow.resolve_bgm_for_run(req, DurableInteractionPort())

    assert repeated == first
    assert repeated.resolution_id == first.resolution_id


def test_validated_download_is_reused_after_crash_before_resolution_commit(
    tmp_path,
    monkeypatch,
):
    from videocreator import bgm_workflow

    candidate = online_candidate()
    monkeypatch.setattr(
        bgm_workflow,
        "search_configured_providers",
        lambda *_args, **_kwargs: [candidate],
    )
    downloads = []

    def download(_candidate, output_dir, **_kwargs):
        path = output_dir / f"random-{len(downloads)}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        downloads.append(path)
        return path

    monkeypatch.setattr(bgm_workflow, "download_candidate", download)
    monkeypatch.setattr(bgm_workflow, "candidate_to_track", candidate_track)
    original_commit = bgm_workflow._commit_resolution
    monkeypatch.setattr(
        bgm_workflow,
        "_commit_resolution",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("crash before commit")
        ),
    )
    req = request(tmp_path)

    with pytest.raises(RuntimeError, match="crash before commit"):
        bgm_workflow.resolve_bgm_for_run(req, DurableInteractionPort())
    assert len(downloads) == 1
    ledger = json.loads(
        bgm_workflow._download_ledger_path(req).read_text(encoding="utf-8")
    )
    cached_path = req.download_dir / next(
        iter(ledger["candidates"].values())
    )["path"]
    assert cached_path.exists()

    monkeypatch.setattr(bgm_workflow, "_commit_resolution", original_commit)
    monkeypatch.setattr(
        bgm_workflow,
        "download_candidate",
        lambda *_args, **_kwargs: pytest.fail("validated file must be reused"),
    )
    result = bgm_workflow.resolve_bgm_for_run(req, DurableInteractionPort())

    assert result.track.path == cached_path
    assert cached_path.exists()


def test_unselected_files_are_cleaned_only_after_durable_commit(
    tmp_path,
    monkeypatch,
):
    from videocreator import bgm_workflow

    selected = online_candidate("selected")
    rejected = online_candidate("rejected", instrumental=False)
    monkeypatch.setattr(
        bgm_workflow,
        "search_configured_providers",
        lambda *_args, **_kwargs: [selected, rejected],
    )

    def download(candidate, output_dir, **_kwargs):
        path = output_dir / f"random-{candidate.id}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(candidate.id.encode())
        return path

    monkeypatch.setattr(bgm_workflow, "download_candidate", download)
    monkeypatch.setattr(bgm_workflow, "candidate_to_track", candidate_track)
    original_commit = bgm_workflow._commit_resolution
    monkeypatch.setattr(
        bgm_workflow,
        "_commit_resolution",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("crash")),
    )
    req = request(tmp_path)

    with pytest.raises(RuntimeError, match="crash"):
        bgm_workflow.resolve_bgm_for_run(req, DurableInteractionPort())
    download_ledger = json.loads(
        bgm_workflow._download_ledger_path(req).read_text(encoding="utf-8")
    )
    candidate_paths = [
        req.download_dir / item["path"]
        for item in download_ledger["candidates"].values()
    ]
    assert all(path.exists() for path in candidate_paths)

    monkeypatch.setattr(bgm_workflow, "_commit_resolution", original_commit)
    result = bgm_workflow.resolve_bgm_for_run(req, DurableInteractionPort())

    assert result.track.path.exists()
    assert sum(path.exists() for path in candidate_paths) == 1


def test_cleanup_resumes_after_commit_before_cleanup_crash(tmp_path, monkeypatch):
    from videocreator import bgm_workflow

    selected = online_candidate("selected")
    unselected = online_candidate("unselected", instrumental=False)
    monkeypatch.setattr(
        bgm_workflow,
        "search_configured_providers",
        lambda *_args, **_kwargs: [selected, unselected],
    )

    def download(candidate, output_dir, **_kwargs):
        path = output_dir / f"random-{candidate.id}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(candidate.id.encode())
        return path

    monkeypatch.setattr(bgm_workflow, "download_candidate", download)
    monkeypatch.setattr(bgm_workflow, "candidate_to_track", candidate_track)
    original_cleanup = bgm_workflow._cleanup_downloads
    monkeypatch.setattr(
        bgm_workflow,
        "_cleanup_downloads",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("crash after commit")
        ),
    )
    req = request(tmp_path)

    with pytest.raises(RuntimeError, match="crash after commit"):
        bgm_workflow.resolve_bgm_for_run(req, DurableInteractionPort())
    assert bgm_workflow._resolution_ledger_path(req).is_file()
    download_ledger = json.loads(
        bgm_workflow._download_ledger_path(req).read_text(encoding="utf-8")
    )
    paths = [
        req.download_dir / item["path"]
        for item in download_ledger["candidates"].values()
    ]
    assert all(path.exists() for path in paths)

    monkeypatch.setattr(bgm_workflow, "_cleanup_downloads", original_cleanup)
    monkeypatch.setattr(
        bgm_workflow,
        "search_configured_providers",
        lambda *_args, **_kwargs: pytest.fail("committed result must resume"),
    )
    result = bgm_workflow.resolve_bgm_for_run(req, DurableInteractionPort())

    assert result.track.path.exists()
    assert sum(path.exists() for path in paths) == 1


def test_old_resolution_ack_does_not_clear_new_query_interaction(
    tmp_path,
    monkeypatch,
):
    from videocreator import bgm_workflow

    old_request = request(tmp_path)
    port = DurableInteractionPort()
    with pytest.raises(InteractionRequired) as raised:
        bgm_workflow.resolve_bgm_for_run(old_request, port)
    port.submit(
        old_request.context,
        raised.value.interaction["id"],
        "not-json",
        fingerprint=raised.value.interaction["fingerprint"],
    )
    old_result = bgm_workflow.resolve_bgm_for_run(old_request, port)

    changed_query = replace(
        query(),
        subjects=("biology",),
        terms_en=("biology",),
    )
    new_request = request(
        tmp_path,
        context=old_request.context,
        query=changed_query,
    )
    with pytest.raises(InteractionRequired) as changed:
        bgm_workflow.resolve_bgm_for_run(new_request, port)
    new_interaction = changed.value.interaction

    assert bgm_workflow.acknowledge_bgm_resolution(
        old_request,
        port,
        old_result.resolution_id,
    )
    assert (
        old_request.context.state["pending_interaction"]["id"]
        == new_interaction["id"]
    )


def test_ledgers_use_relative_paths_inside_download_dir(tmp_path, monkeypatch):
    from videocreator import bgm_workflow

    candidate = online_candidate()
    monkeypatch.setattr(
        bgm_workflow,
        "search_configured_providers",
        lambda *_args, **_kwargs: [candidate],
    )

    def download(item, output_dir, **kwargs):
        path = output_dir / f"{kwargs['output_name']}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return path

    monkeypatch.setattr(bgm_workflow, "download_candidate", download)
    monkeypatch.setattr(bgm_workflow, "candidate_to_track", candidate_track)
    req = request(tmp_path)

    result = bgm_workflow.resolve_bgm_for_run(req, DurableInteractionPort())

    ledgers = list(req.download_dir.glob("bgm-*.json"))
    assert len(ledgers) == 3
    assert all(path.parent == req.download_dir for path in ledgers)
    for ledger_path in ledgers:
        serialized = ledger_path.read_text(encoding="utf-8")
        assert str(req.download_dir) not in serialized
    assert result.track.path.parent == bgm_workflow._request_candidate_dir(req)


@pytest.mark.parametrize("bad_path", ["../state.json", "C:/outside/state.json"])
def test_malformed_download_ledger_path_is_rejected_without_unlinking_run_state(
    tmp_path,
    monkeypatch,
    bad_path,
):
    from videocreator import bgm_workflow

    candidate = online_candidate()
    req = request(tmp_path)
    state_path = tmp_path / "state.json"
    state_path.write_text('{"sentinel":true}', encoding="utf-8")
    fingerprint = bgm_workflow.candidate_fingerprint(candidate)
    ledger_path = bgm_workflow._download_ledger_path(req)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request_fingerprint": bgm_workflow._request_fingerprint(req),
                "candidates": {
                    fingerprint: {
                        "candidate_id": candidate.id,
                        "status": "validated",
                        "path": bad_path,
                        "sha256": "0" * 64,
                        "track": {
                                **bgm_workflow._track_to_dict(
                                    req,
                                    track(
                                        req.download_dir,
                                        track_id=candidate.id,
                                    ),
                                ),
                            "path": bad_path,
                            "metadata_path": bad_path,
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        bgm_workflow,
        "search_configured_providers",
        lambda *_args, **_kwargs: [candidate],
    )

    with pytest.raises(RuntimeError, match="ledger"):
        bgm_workflow.resolve_bgm_for_run(req, DurableInteractionPort())

    assert state_path.read_text(encoding="utf-8") == '{"sentinel":true}'


def test_provisional_download_is_recovered_after_crash_before_probe(
    tmp_path,
    monkeypatch,
):
    from videocreator import bgm_workflow

    candidate = online_candidate()
    req = request(tmp_path)
    monkeypatch.setattr(
        bgm_workflow,
        "search_configured_providers",
        lambda *_args, **_kwargs: [candidate],
    )
    downloads = 0

    def download(item, output_dir, **kwargs):
        nonlocal downloads
        downloads += 1
        path = output_dir / f"{kwargs['output_name']}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return path

    probes = 0

    def crash_then_probe(item, path):
        nonlocal probes
        probes += 1
        if probes == 1:
            raise KeyboardInterrupt("crash after download")
        return candidate_track(item, path)

    monkeypatch.setattr(bgm_workflow, "download_candidate", download)
    monkeypatch.setattr(bgm_workflow, "candidate_to_track", crash_then_probe)

    with pytest.raises(KeyboardInterrupt, match="crash after download"):
        bgm_workflow.resolve_bgm_for_run(req, DurableInteractionPort())
    provisional = json.loads(
        bgm_workflow._download_ledger_path(req).read_text(encoding="utf-8")
    )
    assert next(iter(provisional["candidates"].values()))["status"] == "provisional"

    result = bgm_workflow.resolve_bgm_for_run(req, DurableInteractionPort())

    assert downloads == 1
    assert result.track.path.name.startswith("candidate-")


def test_acknowledgement_removes_signed_agent_url_from_runtime_state_and_ledgers(
    tmp_path,
    monkeypatch,
):
    from videocreator import bgm_workflow

    req = request(tmp_path)
    port = DurableInteractionPort()
    with pytest.raises(InteractionRequired) as raised:
        bgm_workflow.resolve_bgm_for_run(req, port)
    response = agent_response().replace(
        "track.mp3",
        "track.mp3?X-Amz-Signature=DO-NOT-PERSIST",
    )
    port.submit(req.context, raised.value.interaction["id"], response)

    def download(item, output_dir, **kwargs):
        path = output_dir / f"{kwargs['output_name']}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return path

    monkeypatch.setattr(bgm_workflow, "download_candidate", download)
    monkeypatch.setattr(bgm_workflow, "candidate_to_track", candidate_track)
    result = bgm_workflow.resolve_bgm_for_run(req, port)
    assert bgm_workflow.acknowledge_bgm_resolution(
        req, port, result.resolution_id
    )

    durable_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in req.download_dir.glob("*.json")
    )
    durable_text += (tmp_path / "session/interactions.jsonl").read_text(
        encoding="utf-8"
    )
    durable_text += (tmp_path / "state.json").read_text(encoding="utf-8")
    assert "DO-NOT-PERSIST" not in durable_text


def test_copied_resolution_ledger_without_matching_artifact_is_rejected(
    tmp_path,
):
    from videocreator import bgm_workflow

    first_root = tmp_path / "first"
    first_req = request(
        first_root,
        local_tracks=(track(first_root / "local", track_id="local"),),
    )
    bgm_workflow.resolve_bgm_for_run(first_req, ConsoleInteractionPort())
    copied = bgm_workflow._resolution_ledger_path(first_req).read_text(
        encoding="utf-8"
    )

    second_root = tmp_path / "second"
    second_req = request(
        second_root,
        local_tracks=(track(second_root / "local", track_id="local"),),
    )
    second_ledger = bgm_workflow._resolution_ledger_path(second_req)
    second_ledger.parent.mkdir(parents=True, exist_ok=True)
    second_ledger.write_text(copied, encoding="utf-8")

    with pytest.raises(RuntimeError, match="missing or changed"):
        bgm_workflow.resolve_bgm_for_run(
            second_req,
            ConsoleInteractionPort(),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("metadata_path", "../state.json"),
        ("metadata_sha256", "0" * 64),
    ],
)
def test_resolution_ledger_requires_metadata_path_and_hash_agreement(
    tmp_path,
    field,
    value,
):
    from videocreator import bgm_workflow

    local = track(tmp_path / "local", track_id="local")
    req = request(tmp_path, local_tracks=(local,))
    bgm_workflow.resolve_bgm_for_run(req, ConsoleInteractionPort())
    ledger_path = bgm_workflow._resolution_ledger_path(req)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["track"][field] = value
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    with pytest.raises(RuntimeError, match="ledger|missing or changed"):
        bgm_workflow.resolve_bgm_for_run(req, ConsoleInteractionPort())


def test_cleanup_removes_rejected_provisional_partial_and_orphan_candidates(
    tmp_path,
):
    from videocreator import bgm_workflow

    local = track(tmp_path / "local", track_id="local")
    req = request(tmp_path, local_tracks=(local,))
    rejected_fp = "a" * 64
    provisional_fp = "b" * 64
    orphan_fp = "c" * 64
    owned = bgm_workflow._request_candidate_dir(req)
    owned.mkdir(parents=True, exist_ok=True)
    rejected = owned / f"candidate-{rejected_fp}.mp3"
    partial = owned / f".candidate-{provisional_fp}.mp3.part"
    orphan = owned / f"candidate-{orphan_fp}.mp3"
    foreign_owned = req.download_dir / f"request-{'e' * 64}"
    foreign_owned.mkdir(parents=True, exist_ok=True)
    foreign_orphan = foreign_owned / f"candidate-{'f' * 64}.mp3"
    rejected.write_bytes(b"rejected")
    partial.write_bytes(b"partial")
    orphan.write_bytes(b"orphan")
    foreign_orphan.write_bytes(b"foreign")
    bgm_workflow._write_download_ledger(
        req,
        {
            "schema_version": 1,
            "request_fingerprint": bgm_workflow._request_fingerprint(req),
            "candidates": {
                rejected_fp: {
                    "candidate_id": "rejected",
                    "status": "rejected",
                    "path": rejected.relative_to(
                        req.download_dir
                    ).as_posix(),
                    "reason": "probe failed",
                },
                provisional_fp: {
                    "candidate_id": "provisional",
                    "status": "provisional",
                    "path": None,
                    "path_stem": f"candidate-{provisional_fp}",
                },
            },
        },
    )

    result = bgm_workflow.resolve_bgm_for_run(
        req,
        ConsoleInteractionPort(),
    )

    assert result.track.path.exists()
    assert not rejected.exists()
    assert not partial.exists()
    assert not orphan.exists()
    assert foreign_orphan.exists()
    ledger = bgm_workflow._load_download_ledger(req)
    assert {
        entry["status"] for entry in ledger["candidates"].values()
    } == {"cleaned"}
    assert ledger["cleanup"]["status"] == "completed"


def test_cleanup_plan_resumes_after_unlink_failure(tmp_path, monkeypatch):
    from videocreator import bgm_workflow

    local = track(tmp_path / "local", track_id="local")
    req = request(tmp_path, local_tracks=(local,))
    fingerprint = "d" * 64
    owned = bgm_workflow._request_candidate_dir(req)
    owned.mkdir(parents=True, exist_ok=True)
    stale = owned / f"candidate-{fingerprint}.mp3"
    stale.write_bytes(b"stale")
    bgm_workflow._write_download_ledger(
        req,
        {
            "schema_version": 1,
            "request_fingerprint": bgm_workflow._request_fingerprint(req),
            "candidates": {
                fingerprint: {
                    "candidate_id": "stale",
                    "status": "rejected",
                    "path": stale.relative_to(
                        req.download_dir
                    ).as_posix(),
                    "reason": "probe failed",
                },
            },
        },
    )
    original_unlink = bgm_workflow._unlink_candidate_artifact
    monkeypatch.setattr(
        bgm_workflow,
        "_unlink_candidate_artifact",
        lambda *_args: (_ for _ in ()).throw(OSError("crash during cleanup")),
    )

    with pytest.raises(OSError, match="crash during cleanup"):
        bgm_workflow.resolve_bgm_for_run(req, ConsoleInteractionPort())
    pending = bgm_workflow._load_download_ledger(req)
    assert pending["cleanup"]["status"] == "pending"
    assert stale.exists()

    monkeypatch.setattr(
        bgm_workflow,
        "_unlink_candidate_artifact",
        original_unlink,
    )
    resumed = bgm_workflow.resolve_bgm_for_run(req, ConsoleInteractionPort())

    assert resumed.track.path.exists()
    assert not stale.exists()
    assert bgm_workflow._load_download_ledger(req)["cleanup"]["status"] == "completed"


def test_copied_complete_bundle_does_not_replay_in_another_run(
    tmp_path,
    monkeypatch,
):
    from videocreator import bgm_workflow

    candidate = online_candidate()
    provider_calls = 0

    def search(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return [candidate]

    def download(item, output_dir, **kwargs):
        path = output_dir / f"{kwargs['output_name']}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return path

    monkeypatch.setattr(bgm_workflow, "search_configured_providers", search)
    monkeypatch.setattr(bgm_workflow, "download_candidate", download)
    monkeypatch.setattr(bgm_workflow, "candidate_to_track", candidate_track)
    first_root = tmp_path / "projects/demo/runs/run-1"
    first_req = request(
        first_root,
        context=Context(first_root, project_name="demo", run_id="run-1"),
    )
    first = bgm_workflow.resolve_bgm_for_run(
        first_req,
        DurableInteractionPort(),
    )

    second_root = tmp_path / "projects/demo/runs/run-2"
    second_download = second_root / "visual/bgm"
    shutil.copytree(first_req.download_dir, second_download)
    second_req = request(
        second_root,
        context=Context(second_root, project_name="demo", run_id="run-2"),
        download_dir=second_download,
    )
    second = bgm_workflow.resolve_bgm_for_run(
        second_req,
        DurableInteractionPort(),
    )

    assert first.request_fingerprint != second.request_fingerprint
    assert provider_calls == 2


def test_local_media_directory_is_fsynced_before_resolution_commit(
    tmp_path,
    monkeypatch,
):
    from videocreator import bgm_workflow

    req = request(
        tmp_path,
        local_tracks=(track(tmp_path / "local", track_id="local"),),
    )
    events = []
    original_commit = bgm_workflow._commit_resolution
    monkeypatch.setattr(
        bgm_workflow,
        "fsync_directory",
        lambda _path: events.append("media_fsync"),
    )

    def commit(request_value, resolution):
        assert events == ["media_fsync"]
        events.append("resolution_commit")
        return original_commit(request_value, resolution)

    monkeypatch.setattr(bgm_workflow, "_commit_resolution", commit)

    bgm_workflow.resolve_bgm_for_run(req, ConsoleInteractionPort())

    assert events == ["media_fsync", "resolution_commit"]


def test_defensive_source_url_redaction_removes_userinfo_and_signed_query():
    from videocreator.bgm_workflow import _redact_url

    redacted = _redact_url(
        "https://user:SUPER-SECRET@example.test:8443/source"
        "?X-Amz-Signature=SIGNED"
    )

    assert redacted == "https://example.test:8443/source"
    assert "SUPER-SECRET" not in redacted
    assert "SIGNED" not in redacted


def test_stale_request_cleanup_cannot_delete_new_request_selected_media(
    tmp_path,
    monkeypatch,
):
    from videocreator import bgm_workflow

    local = track(tmp_path / "local", track_id="local")
    request_a = request(tmp_path, local_tracks=(local,))
    fingerprint_a = bgm_workflow._request_fingerprint(request_a)
    candidate_a = "a" * 64
    owned_a = request_a.download_dir / f"request-{fingerprint_a}"
    owned_a.mkdir(parents=True, exist_ok=True)
    stale_a = owned_a / f"candidate-{candidate_a}.mp3"
    stale_a.write_bytes(b"request-a-orphan")
    bgm_workflow._write_download_ledger(
        request_a,
        {
            "schema_version": 1,
            "request_fingerprint": fingerprint_a,
            "candidates": {
                candidate_a: {
                    "candidate_id": "request-a-stale",
                    "status": "rejected",
                    "path": stale_a.relative_to(
                        request_a.download_dir
                    ).as_posix(),
                    "reason": "probe failed",
                },
            },
        },
    )
    original_unlink = bgm_workflow._unlink_candidate_artifact
    monkeypatch.setattr(
        bgm_workflow,
        "_unlink_candidate_artifact",
        lambda *_args: (_ for _ in ()).throw(OSError("pause A cleanup")),
    )
    with pytest.raises(OSError, match="pause A cleanup"):
        bgm_workflow.resolve_bgm_for_run(
            request_a,
            ConsoleInteractionPort(),
        )
    assert bgm_workflow._load_download_ledger(request_a)["cleanup"][
        "status"
    ] == "pending"

    online = online_candidate("request-b-selected")
    monkeypatch.setattr(
        bgm_workflow,
        "search_configured_providers",
        lambda *_args, **_kwargs: [online],
    )

    def download(item, output_dir, **kwargs):
        path = output_dir / f"{kwargs['output_name']}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"request-b-media")
        return path

    monkeypatch.setattr(bgm_workflow, "download_candidate", download)
    monkeypatch.setattr(bgm_workflow, "candidate_to_track", candidate_track)
    monkeypatch.setattr(
        bgm_workflow,
        "_unlink_candidate_artifact",
        original_unlink,
    )
    request_b = request(
        tmp_path,
        query=replace(query(), subjects=("history",)),
    )
    result_b = bgm_workflow.resolve_bgm_for_run(
        request_b,
        DurableInteractionPort(),
    )
    media_b = result_b.track.path
    resolution_b = bgm_workflow._resolution_ledger_path(request_b)

    assert media_b.parent == (
        request_b.download_dir
        / f"request-{result_b.request_fingerprint}"
    )
    assert media_b.exists()
    assert resolution_b.exists()

    with pytest.raises(RuntimeError, match="stale cleanup"):
        bgm_workflow.resolve_bgm_for_run(
            request_a,
            ConsoleInteractionPort(),
        )

    assert media_b.exists()
    assert resolution_b.exists()
    replay_b = bgm_workflow.resolve_bgm_for_run(
        request_b,
        DurableInteractionPort(),
    )
    assert replay_b.resolution_id == result_b.resolution_id
    assert replay_b.track.path == media_b
