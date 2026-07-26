from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import main
from main import WorkflowContext
from videocreator.bgm_audit import write_bgm_mix_report
from videocreator.bgm_library import (
    BgmTrack,
    load_bgm_directory,
    resolve_bgm_library,
)
from videocreator.bgm_mix import BgmMixError, mix_bgm
from videocreator.bgm_policy import BgmPolicy
from videocreator.bgm_search import OnlineBgmCandidate
from videocreator.bgm_selection import BgmQuery
from videocreator.bgm_workflow import (
    BgmResolutionRequest,
    acknowledge_bgm_resolution,
    resolve_bgm_for_run,
)
from videocreator.interactions import (
    ConsoleInteractionPort,
    DurableInteractionPort,
    InteractionRequired,
)
from videocreator.project_layout import (
    create_run,
    initialize_project,
)
from videocreator.render_contract import ensure_bgm_mix_gate
from videocreator.subtitle_sync import sha256_file
from videocreator.templates import load_template


REPO_ROOT = Path(__file__).resolve().parents[2]
FFMPEG_AVAILABLE = all(
    shutil.which(tool) is not None for tool in ("ffmpeg", "ffprobe")
)
RENDERER_AVAILABLE = FFMPEG_AVAILABLE and all(
    shutil.which(tool) is not None for tool in ("node", "npm")
)


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def generate_audio(path: Path, duration: float, frequency: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )


def write_sidecar(
    audio: Path,
    track_id: str,
    *,
    source_url: str = "https://example.test/music/source",
    provider: str | None = None,
    rights_status: str = "cleared",
) -> Path:
    sidecar = audio.with_suffix(".bgm.json")
    sidecar.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": track_id,
                "title": f"Track {track_id}",
                "creator": "Fixture Composer",
                "source_url": source_url,
                "provider": provider,
                "license": "CC BY 4.0",
                "rights_status": rights_status,
                "subjects": ["technology", "education"],
                "moods": ["curious", "calm", "technological"],
                "energy": "low-medium",
                "tempo_bpm": 90,
                "instrumental": True,
                "template_tags": ["science-explainer"],
                "avoid_for": [],
                "preferred_start_ms": 0,
                "loopable": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return sidecar


def ffprobe(path: Path) -> dict[str, Any]:
    completed = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    return json.loads(completed.stdout)


def ffprobe_stream_types(path: Path) -> set[str]:
    return {
        str(stream["codec_type"])
        for stream in ffprobe(path).get("streams", [])
    }


@dataclass
class ResolutionContext:
    run_dir: Path
    state: dict[str, Any]
    project_name: str = "integration"
    run_id: str = "run-001"

    def save_state(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "state.json").write_text(
            json.dumps(self.state),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, run_dir: Path) -> "ResolutionContext":
        return cls(
            run_dir=run_dir,
            state=json.loads(
                (run_dir / "state.json").read_text(encoding="utf-8")
            ),
        )


def query() -> BgmQuery:
    return BgmQuery(
        subjects=("technology",),
        moods=("curious", "calm"),
        template_id="science-explainer",
        terms_zh=(),
        terms_en=("technology", "calm"),
    )


def resolution_request(
    run_dir: Path,
    *,
    context: ResolutionContext | None = None,
) -> BgmResolutionRequest:
    return BgmResolutionRequest(
        context=context or ResolutionContext(run_dir, {"status": "running"}),
        local_tracks=(),
        query=query(),
        policy=BgmPolicy(preferred_moods=("curious", "calm")),
        provider_config={"providers": []},
        download_dir=run_dir / "audio" / "bgm-downloads",
    )


def online_candidate(candidate_id: str) -> OnlineBgmCandidate:
    return OnlineBgmCandidate(
        id=candidate_id,
        title=f"Candidate {candidate_id}",
        creator="Public Composer",
        source_page_url=f"https://example.test/source/{candidate_id}",
        download_url=f"https://example.test/audio/{candidate_id}.wav",
        provider="fixture-provider",
        license="CC BY 4.0",
        rights_status="cleared",
        subjects=("technology",),
        moods=("curious", "calm"),
        energy="low-medium",
        tempo_bpm=90,
        instrumental=True,
        template_tags=("science-explainer",),
        loopable=True,
    )


def fixture_downloader(source: Path):
    def download(
        _candidate: OnlineBgmCandidate,
        output_dir: Path,
        **kwargs: Any,
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"{kwargs['output_name']}.wav"
        shutil.copyfile(source, output)
        return output

    return download


def agent_response(candidate: OnlineBgmCandidate) -> str:
    return json.dumps(
        {
            "candidates": [
                {
                    "id": candidate.id,
                    "title": candidate.title,
                    "creator": candidate.creator,
                    "source_page_url": candidate.source_page_url,
                    "download_url": candidate.download_url,
                    "provider": candidate.provider,
                    "license": candidate.license,
                    "rights_status": candidate.rights_status,
                    "subjects": list(candidate.subjects),
                    "moods": list(candidate.moods),
                    "energy": candidate.energy,
                    "tempo_bpm": candidate.tempo_bpm,
                    "instrumental": candidate.instrumental,
                    "template_tags": list(candidate.template_tags),
                    "loopable": candidate.loopable,
                }
            ]
        }
    )


@pytest.mark.integration
@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="FFmpeg and ffprobe are required")
def test_complete_override_uses_only_project_bgm(tmp_path: Path):
    project = tmp_path / "projects" / "demo"
    template = SimpleNamespace(root=tmp_path / "templates" / "demo")
    levels = (
        (project / "library" / "bgm", "project-track", 180),
        (template.root / "library" / "bgm", "template-track", 220),
        (tmp_path / "library" / "bgm" / "default", "global-track", 260),
    )
    for root, track_id, frequency in levels:
        audio = root / f"{track_id}.wav"
        generate_audio(audio, 1, frequency)
        write_sidecar(audio, track_id)

    selected = resolve_bgm_library(tmp_path, project, template)

    assert selected.level == "project"
    assert [track.id for track in selected.tracks] == ["project-track"]


@pytest.mark.integration
@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="FFmpeg and ffprobe are required")
def test_provider_fallback_downloads_and_preserves_attribution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from videocreator import bgm_workflow

    source = tmp_path / "provider-source.wav"
    generate_audio(source, 1, 220)
    candidate = online_candidate("provider-track")
    monkeypatch.setattr(
        bgm_workflow,
        "search_configured_providers",
        lambda *_args, **_kwargs: [candidate],
    )
    monkeypatch.setattr(
        bgm_workflow,
        "download_candidate",
        fixture_downloader(source),
    )
    request = resolution_request(tmp_path / "run-provider")

    result = resolve_bgm_for_run(request, DurableInteractionPort())

    assert result.mode == "bgm"
    assert result.source == "provider"
    assert result.track is not None
    assert result.track.source_url == candidate.source_page_url
    assert result.track.creator == candidate.creator
    assert result.track.license == candidate.license
    assert result.track.provider == candidate.provider
    assert result.track.rights_status == candidate.rights_status
    ledger = json.loads(
        next(
            request.download_dir.glob("bgm-resolution-*.json")
        ).read_text(encoding="utf-8")
    )
    assert ledger["track"]["provider"] == candidate.provider
    assert ledger["track"]["rights_status"] == candidate.rights_status
    download_ledger = json.loads(
        next(
            request.download_dir.glob("bgm-downloads-*.json")
        ).read_text(encoding="utf-8")
    )
    validated = [
        entry
        for entry in download_ledger["candidates"].values()
        if entry["status"] == "validated"
    ]
    assert len(validated) == 1
    assert validated[0]["track"]["provider"] == candidate.provider
    assert validated[0]["track"]["rights_status"] == candidate.rights_status


@pytest.mark.integration
@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="FFmpeg and ffprobe are required")
def test_agent_fallback_waits_then_resumes_from_durable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from videocreator import bgm_workflow

    source = tmp_path / "agent-source.wav"
    generate_audio(source, 1, 260)
    candidate = online_candidate("agent-track")
    monkeypatch.setattr(
        bgm_workflow,
        "search_configured_providers",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        bgm_workflow,
        "download_candidate",
        fixture_downloader(source),
    )
    run_dir = tmp_path / "run-agent"
    first_request = resolution_request(run_dir)
    port = DurableInteractionPort()

    with pytest.raises(InteractionRequired) as raised:
        resolve_bgm_for_run(first_request, port)
    interaction = raised.value.interaction
    assert first_request.context.state["status"] == "waiting_for_input"
    port.submit(
        first_request.context,
        interaction["id"],
        agent_response(candidate),
    )

    resumed_context = ResolutionContext.load(run_dir)
    resumed_request = resolution_request(run_dir, context=resumed_context)
    result = resolve_bgm_for_run(resumed_request, port)

    assert result.mode == "bgm"
    assert result.source == "agent"
    assert result.interaction_id == interaction["id"]
    assert result.track is not None
    assert result.track.provider == candidate.provider
    assert result.track.rights_status == candidate.rights_status
    ledger = json.loads(
        next(
            resumed_request.download_dir.glob("bgm-resolution-*.json")
        ).read_text(encoding="utf-8")
    )
    assert ledger["track"]["provider"] == candidate.provider
    assert ledger["track"]["rights_status"] == candidate.rights_status
    download_ledger = json.loads(
        next(
            resumed_request.download_dir.glob("bgm-downloads-*.json")
        ).read_text(encoding="utf-8")
    )
    validated = [
        entry
        for entry in download_ledger["candidates"].values()
        if entry["status"] == "validated"
    ]
    assert len(validated) == 1
    assert validated[0]["track"]["provider"] == candidate.provider
    assert validated[0]["track"]["rights_status"] == candidate.rights_status
    assert "pending_interaction" not in resumed_context.state
    assert acknowledge_bgm_resolution(
        resumed_request,
        port,
        result.resolution_id,
    )


def test_narration_only_degradation_is_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from videocreator import bgm_workflow

    monkeypatch.setattr(
        bgm_workflow,
        "search_configured_providers",
        lambda *_args, **_kwargs: [],
    )

    result = resolve_bgm_for_run(
        resolution_request(tmp_path / "run-narration"),
        ConsoleInteractionPort(),
    )

    assert result.mode == "narration_only"
    assert result.track is None
    assert any("handoff is unavailable" in warning for warning in result.warnings)


@pytest.mark.integration
@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="FFmpeg and ffprobe are required")
def test_selected_track_and_mix_hash_gates_fail_closed(tmp_path: Path):
    narration = tmp_path / "voice.wav"
    bgm_root = tmp_path / "bgm"
    bgm = bgm_root / "track.wav"
    prepared = tmp_path / "prepared.wav"
    final_mix = tmp_path / "final-mix.wav"
    report_path = tmp_path / "bgm-mix-report.json"
    generate_audio(narration, 2, 440)
    generate_audio(bgm, 1, 180)
    write_sidecar(bgm, "gate-track")
    tracks, warnings = load_bgm_directory(bgm_root, "project")
    assert not warnings
    selected = tracks[0]

    bgm.write_bytes(b"tampered")
    with pytest.raises(BgmMixError, match="audio hash mismatch"):
        mix_bgm(
            narration,
            selected,
            prepared,
            final_mix,
            BgmPolicy(fade_in_ms=100, fade_out_ms=100),
            subprocess.run,
        )

    generate_audio(bgm, 1, 180)
    selected = BgmTrack(
        **{
            **selected.__dict__,
            "sha256": hashlib.sha256(bgm.read_bytes()).hexdigest(),
        }
    )
    result = mix_bgm(
        narration,
        selected,
        prepared,
        final_mix,
        BgmPolicy(fade_in_ms=100, fade_out_ms=100),
        subprocess.run,
    )
    write_bgm_mix_report(result, report_path)
    final_mix.write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="artifact_hash_mismatch"):
        ensure_bgm_mix_gate(final_mix, report_path)


def make_e2e_context(
    tmp_path: Path,
    *,
    provider: str | None = None,
    rights_status: str = "cleared",
) -> WorkflowContext:
    template = load_template(REPO_ROOT / "templates", "science-explainer")
    projects_root = tmp_path / "projects"
    project = initialize_project(
        projects_root,
        "bgm-e2e",
        template,
        title="Automatic BGM integration",
    )
    bgm = project / "library" / "bgm" / "calm-technology.wav"
    generate_audio(bgm, 3, 180)
    write_sidecar(
        bgm,
        "calm-technology",
        provider=provider,
        rights_status=rights_status,
    )
    library = resolve_bgm_library(REPO_ROOT, project, template)
    run_paths = create_run(
        project,
        "run-001",
        template,
        {"bgm": library},
    )

    narration = run_paths.audio / "voice.wav"
    subtitle = run_paths.subtitles / "subtitles.aligned.srt"
    alignment = run_paths.subtitles / "alignment-report.json"
    approved = run_paths.writing / "script.approved.md"
    visual_plan = run_paths.visual / "visual-plan.json"
    asset_manifest = run_paths.visual / "asset-manifest.json"
    image = project / "media" / "images" / "fixture.png"
    generate_audio(narration, 8, 440)
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x1C6E8C:s=1920x1080",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(image),
        ]
    )
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:08,000\nAutomatic BGM fixture\n",
        encoding="utf-8",
    )
    alignment.write_text(
        json.dumps(
            {
                "audio_sha256": sha256_file(narration),
                "srt_sha256": sha256_file(subtitle),
                "exact_match_coverage": 1.0,
                "character_error_rate": 0.0,
                "timing_coverage": 1.0,
                "blocks": [
                    {
                        "index": 1,
                        "boundary_drift_ms": 0,
                        "confidence": 1.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    approved.write_text(
        "Technology education benefits from calm explanatory pacing.",
        encoding="utf-8",
    )
    visual_plan.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "segment_id": "scene-001",
                        "start_ms": 0,
                        "end_ms": 8000,
                        "material_type": "image",
                        "text": "Automatic BGM fixture",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    asset_manifest.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "scene_id": "scene-001",
                        "asset_type": "image",
                        "local_path": image.relative_to(project).as_posix(),
                        "source_page_url": "https://example.test/image/source",
                        "direct_download_url": "https://example.test/image.png",
                        "provider": "integration-fixture",
                        "license": "CC0",
                        "credit": "Integration Fixture",
                        "retrieved_at": "2026-07-26T00:00:00Z",
                        "duration_ms": None,
                        "fit_mode": "cover",
                        "trim_start_ms": 0,
                        "short_video_policy": "reject",
                        "review_status": "approved",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    manifest = json.loads(run_paths.manifest.read_text(encoding="utf-8"))
    manifest["artifacts"].update(
        {
            "voice_audio": str(narration),
            "voice_subtitle": str(subtitle),
            "subtitle_alignment_report": str(alignment),
            "draft_approved": str(approved),
            "visual_plan": str(visual_plan),
            "asset_manifest": str(asset_manifest),
        }
    )
    state = {
        "schema_version": 2,
        "run_id": "run-001",
        "project_name": "bgm-e2e",
        "current_stage": "bgm",
        "status": "ready",
    }
    context = WorkflowContext(
        repo_root=REPO_ROOT,
        config_path=REPO_ROOT / "workflow.config.json",
        config={
            "confirm": {"video": True},
            "subtitle_sync": {"enabled": True},
            "bgm": {
                "enabled": True,
                "search_config": "config/bgm-search.example.json",
                "final_lufs": -16.0,
                "lufs_tolerance": 2.0,
                "true_peak_dbtp": -1.5,
                "max_duration_delta_ms": 100,
                "crossfade_ms": 500,
                "max_agent_candidates": 20,
                "max_agent_response_bytes": 200000,
            },
            "renderer": {"fps": 25},
        },
        run_id="run-001",
        project_name="bgm-e2e",
        run_dir=run_paths.root,
        project_root_override=project,
        topic="calm technology education",
        state=state,
        manifest=manifest,
        project_config=json.loads(
            (project / "project.json").read_text(encoding="utf-8")
        ),
        template=template,
        interactions=ConsoleInteractionPort(),
    )
    context.save_state()
    context.save_manifest()
    return context


@pytest.mark.integration
@pytest.mark.skipif(
    not RENDERER_AVAILABLE,
    reason="Node, npm, FFmpeg, and ffprobe are required",
)
def test_local_bgm_workflow_mixes_and_renders_one_audio_stream(tmp_path: Path):
    context = make_e2e_context(tmp_path)

    main.run_bgm(context)
    main.run_video_render(context)

    artifacts = context.manifest["artifacts"]
    mix_report = json.loads(
        Path(artifacts["bgm_mix_report"]).read_text(encoding="utf-8")
    )
    sync_audit = json.loads(
        Path(artifacts["subtitle_sync_audit"]).read_text(encoding="utf-8")
    )
    selection = json.loads(
        Path(artifacts["bgm_selection"]).read_text(encoding="utf-8")
    )
    render_input = json.loads(
        Path(artifacts["render_input"]).read_text(encoding="utf-8")
    )
    final_mp4 = Path(artifacts["final_video"])
    metadata = ffprobe(final_mp4)
    streams = metadata["streams"]

    assert mix_report["status"] == "passed"
    assert mix_report["mode"] == "bgm"
    assert sync_audit["status"] == "passed"
    assert selection["source"] == "local"
    assert selection["track"]["creator"] == "Fixture Composer"
    assert selection["track"]["source_url"] == (
        "https://example.test/music/source"
    )
    assert selection["track"]["license"] == "CC BY 4.0"
    assert render_input["audioPath"].endswith("final-mix.wav")
    assert set(render_input).isdisjoint({"bgmPath", "narrationPath"})
    assert ffprobe_stream_types(final_mp4) == {"video", "audio"}
    assert [stream["codec_name"] for stream in streams] == ["h264", "aac"]
    assert sum(stream["codec_type"] == "audio" for stream in streams) == 1
    expected_duration = (
        mix_report["outputs"]["render_audio"]["duration_ms"] / 1000
    )
    assert abs(float(metadata["format"]["duration"]) - expected_duration) <= 0.05
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(final_mp4),
            "-f",
            "null",
            "NUL" if sys.platform == "win32" else "/dev/null",
        ]
    )


@pytest.mark.integration
@pytest.mark.skipif(
    not RENDERER_AVAILABLE,
    reason="Node, npm, FFmpeg, and ffprobe are required",
)
def test_local_blank_provenance_is_canonical_through_render_gate(tmp_path: Path):
    context = make_e2e_context(
        tmp_path,
        provider="",
        rights_status="",
    )

    main.run_bgm(context)
    main.run_video_render(context)

    artifacts = context.manifest["artifacts"]
    selection = json.loads(
        Path(artifacts["bgm_selection"]).read_text(encoding="utf-8")
    )
    report = json.loads(
        Path(artifacts["bgm_mix_report"]).read_text(encoding="utf-8")
    )
    frozen = json.loads(
        Path(artifacts["bgm_source_metadata"]).read_text(encoding="utf-8")
    )
    lineage = context.manifest["lineage"]["bgm"]

    for record in (
        selection["track"],
        report["provenance"],
        frozen,
        lineage,
    ):
        assert record["provider"] is None
        assert record["rights_status"] == "unknown"
    assert Path(artifacts["final_video"]).is_file()
    assert main.resolve_context_render_audio(context).is_file()

    lineage["rights_status"] = "cleared"
    with pytest.raises(RuntimeError, match="bgm_manifest_provenance_mismatch"):
        main.resolve_context_render_audio(context)
