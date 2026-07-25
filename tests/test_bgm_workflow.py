import hashlib
import json
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
    def __init__(self, root: Path):
        self.run_id = "run-1"
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
    assert result.track == local
    assert result.resolution_id
    ledger = json.loads(
        (tmp_path / "audio/bgm-resolution.json").read_text(encoding="utf-8")
    )
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
        (tmp_path / "audio/bgm-resolution.json").read_text(encoding="utf-8")
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
        (tmp_path / "audio/bgm-resolution.json").read_text(encoding="utf-8")
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
        (tmp_path / "audio/bgm-downloads.json").read_text(encoding="utf-8")
    )
    cached_path = Path(next(iter(ledger["candidates"].values()))["path"])
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
        (tmp_path / "audio/bgm-downloads.json").read_text(encoding="utf-8")
    )
    candidate_paths = [
        Path(item["path"]) for item in download_ledger["candidates"].values()
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
    assert (tmp_path / "audio/bgm-resolution.json").is_file()
    download_ledger = json.loads(
        (tmp_path / "audio/bgm-downloads.json").read_text(encoding="utf-8")
    )
    paths = [Path(item["path"]) for item in download_ledger["candidates"].values()]
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
