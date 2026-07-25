import json
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
    return BgmTrack(
        id=track_id,
        path=path,
        metadata_path=path,
        level="local",
        sha256=track_id,
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
        metadata_sha256=track_id,
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
    monkeypatch.setattr(
        bgm_workflow,
        "candidate_to_track",
        lambda candidate, path: track(
            path.parent,
            track_id=candidate.id,
            rights_status=candidate.rights_status,
        ),
    )

    result = bgm_workflow.resolve_bgm_for_run(req, port)

    assert result.mode == "bgm"
    assert result.source == "agent"
    assert result.track.id == "agent-track"
    assert any("rights status is unknown" in warning for warning in result.warnings)
    assert "interaction_answers" not in req.context.state
    assert "submitted_interactions" not in req.context.state
    assert "pending_interaction" not in req.context.state


def test_invalid_agent_response_falls_back_and_clears_answer(tmp_path):
    from videocreator.bgm_workflow import resolve_bgm_for_run

    req = request(tmp_path)
    port = DurableInteractionPort()
    with pytest.raises(InteractionRequired) as raised:
        resolve_bgm_for_run(req, port)
    port.submit(req.context, raised.value.interaction["id"], "not-json")

    result = resolve_bgm_for_run(req, port)

    assert result.mode == "narration_only"
    assert any("agent response" in warning.lower() for warning in result.warnings)
    assert "interaction_answers" not in req.context.state
    assert "submitted_interactions" not in req.context.state


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
