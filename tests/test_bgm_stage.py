from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from main import WorkflowContext
from videocreator.bgm_library import BgmLibrarySelection, BgmTrack
from videocreator.bgm_mix import bgm_policy_hash, mix_configuration_hash
from videocreator.bgm_workflow import BgmResolution
from videocreator.media import MediaMetadata


@pytest.fixture
def context(tmp_path: Path) -> WorkflowContext:
    project = tmp_path / "projects" / "demo"
    run = project / "runs" / "run-001"
    for directory in (
        run / "audio",
        run / "subtitles",
        run / "visual",
        run / "render",
        run / "review",
        run / "session",
        run / "writing",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    narration = run / "audio" / "voice.mp3"
    subtitle = run / "subtitles" / "voice.srt"
    narration.write_bytes(b"narration")
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n",
        encoding="utf-8",
    )
    asset_manifest = run / "visual" / "asset-manifest.json"
    asset_manifest.write_text('{"assets":[]}', encoding="utf-8")
    approved = run / "writing" / "script.approved.md"
    approved.write_text("Approved narration", encoding="utf-8")
    ctx = WorkflowContext(
        repo_root=tmp_path,
        config_path=tmp_path / "workflow.config.json",
        config={
            "confirm": {"assets": False},
            "bgm": {
                "enabled": True,
                "search_config": "config/bgm-search.local.json",
            },
        },
        run_id="run-001",
        project_name="demo",
        run_dir=run,
        project_root_override=project,
        topic="A topic",
        state={"current_stage": "visual_assets_confirm"},
        manifest={
            "artifacts": {
                "voice_audio": str(narration),
                "voice_subtitle": str(subtitle),
                "draft_approved": str(approved),
                "asset_manifest": str(asset_manifest),
            }
        },
        project_config={"title": "Demo title"},
        template=SimpleNamespace(
            id="demo",
            root=tmp_path / "templates" / "demo",
            paths={},
        ),
    )
    ctx.save_state()
    ctx.save_manifest()
    return ctx


def resolution(mode: str, track: BgmTrack | None = None) -> BgmResolution:
    return BgmResolution(
        mode=mode,
        source="local" if track else "none",
        track=track,
        scores=(),
        warnings=("fixture warning",),
        resolution_id="resolution-1",
        request_fingerprint="request-1",
        interaction_id=None,
        interaction_fingerprint=None,
    )


def test_assets_confirmation_advances_to_bgm(context):
    main.confirm_visual_assets(context)

    assert context.state["current_stage"] == "bgm"
    assert context.state["status"] == "ready"


def test_narration_only_stage_registers_report_and_advances(
    context,
    monkeypatch,
):
    monkeypatch.setattr(
        main,
        "ensure_current_subtitle_sync_audit",
        lambda _ctx: {"status": "passed"},
    )
    monkeypatch.setattr(
        main,
        "resolve_bgm_for_context",
        lambda _ctx: resolution("narration_only"),
    )
    monkeypatch.setattr(
        main,
        "write_narration_only_report",
        lambda narration, path, warnings: (
            path.write_text(
                json.dumps(
                    {
                        "mode": "narration_only",
                        "status": "passed",
                        "outputs": {
                            "render_audio": {"path": str(narration)}
                        },
                        "warnings": list(warnings),
                    }
                ),
                encoding="utf-8",
            )
            or {"mode": "narration_only", "status": "passed"}
        ),
    )
    monkeypatch.setattr(
        main,
        "ensure_bgm_mix_gate",
        lambda _audio, _report: {"status": "passed"},
    )
    monkeypatch.setattr(
        main,
        "acknowledge_bgm_for_context",
        lambda _ctx, _resolution: None,
    )

    main.run_bgm(context)

    artifacts = context.manifest["artifacts"]
    assert Path(artifacts["bgm_selection"]).is_file()
    assert Path(artifacts["bgm_mix_report"]).is_file()
    assert "final_mix" not in artifacts
    assert context.state["current_stage"] == "video_render"
    assert context.state["status"] == "ready"


def test_selected_bgm_stage_registers_full_lineage(context, monkeypatch):
    source = context.run_dir / "audio" / "selected.mp3"
    metadata = context.run_dir / "audio" / "selected.bgm.json"
    source.write_bytes(b"selected")
    metadata.write_text("{}", encoding="utf-8")
    track = BgmTrack(
        id="selected",
        path=source,
        metadata_path=metadata,
        level="project",
        sha256="a" * 64,
        title="Selected",
        creator="Composer",
        source_url="https://example.com/selected",
        license="CC BY 4.0",
        rights_status="cleared",
        subjects=(),
        moods=(),
        energy="low",
        tempo_bpm=90,
        instrumental=True,
        template_tags=(),
        avoid_for=(),
        preferred_start_ms=0,
        loopable=True,
        metadata_sha256="b" * 64,
    )
    prepared = context.run_dir / "audio" / "bgm.prepared.wav"
    final_mix = context.run_dir / "audio" / "final-mix.wav"
    prepared.write_bytes(b"prepared")
    final_mix.write_bytes(b"mix")
    result = SimpleNamespace(
        bgm=track,
        prepared_bgm_path=prepared,
        mix_path=final_mix,
    )
    monkeypatch.setattr(
        main,
        "ensure_current_subtitle_sync_audit",
        lambda _ctx: {"status": "passed"},
    )
    monkeypatch.setattr(
        main,
        "resolve_bgm_for_context",
        lambda _ctx: resolution("bgm", track),
    )
    monkeypatch.setattr(main, "freeze_bgm_source", lambda _ctx, item: item)
    mix_calls = []

    def mix(*_args, **_kwargs):
        mix_calls.append(True)
        return result

    monkeypatch.setattr(main, "mix_bgm", mix)
    def write_report(_result, path):
        settings = main.bgm_mix_settings_for_context(context)
        value = {
            "mode": "bgm",
            "status": "passed",
            "inputs": {
                "narration": {
                    "path": context.manifest["artifacts"]["voice_audio"],
                    "sha256": main.sha256_file(
                        Path(context.manifest["artifacts"]["voice_audio"])
                    ),
                },
                "bgm": {"path": str(source)},
            },
            "outputs": {
                "prepared_bgm": {"path": str(prepared)},
                "render_audio": {"path": str(final_mix)},
            },
            "settings": vars(settings),
            "policy_sha256": bgm_policy_hash(
                main._effective_bgm_policy(context)
            ),
            "configuration_sha256": mix_configuration_hash(settings),
        }
        path.write_text(json.dumps(value), encoding="utf-8")
        return value

    monkeypatch.setattr(main, "write_bgm_mix_report", write_report)
    monkeypatch.setattr(
        main,
        "ensure_bgm_mix_gate",
        lambda _audio, _report: {"status": "passed"},
    )
    monkeypatch.setattr(
        main,
        "acknowledge_bgm_for_context",
        lambda _ctx, _resolution: None,
    )

    main.run_bgm(context)
    main.run_bgm(context)

    artifacts = context.manifest["artifacts"]
    assert artifacts["bgm_source"] == str(source)
    assert artifacts["bgm_prepared"] == str(prepared)
    assert artifacts["final_mix"] == str(final_mix)
    assert artifacts["bgm_mix_report"].endswith("bgm-mix-report.json")
    assert context.manifest["lineage"]["bgm"]["resolution_id"] == "resolution-1"
    assert json.loads(
        Path(artifacts["bgm_mix_report"]).read_text(encoding="utf-8")
    )["workflow"]["resolution_id"] == "resolution-1"
    assert len(mix_calls) == 1

    report_path = Path(artifacts["bgm_mix_report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["policy_sha256"] = "tampered"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(RuntimeError, match="bgm_mix_policy_mismatch"):
        main.resolve_context_render_audio(context)


def test_bgm_mix_settings_follow_workflow_configuration(context):
    context.config["bgm"].update(
        {
            "final_lufs": -15.0,
            "lufs_tolerance": 1.5,
            "true_peak_dbtp": -1.2,
            "max_duration_delta_ms": 80,
            "crossfade_ms": 1200,
        }
    )

    settings = main.bgm_mix_settings_for_context(context)

    assert settings.target_lufs == -15.0
    assert settings.min_lufs == -16.5
    assert settings.max_lufs == -13.5
    assert settings.target_true_peak_dbtp == -1.2
    assert settings.max_true_peak_dbtp == -1.2
    assert settings.duration_tolerance_ms == 80
    assert settings.crossfade_ms == 1200


def test_bgm_stage_rejects_library_changed_after_run_snapshot(context):
    source = context.project_root / "library" / "bgm" / "track.mp3"
    metadata = source.with_suffix(".bgm.json")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"current-audio")
    metadata.write_text("{}", encoding="utf-8")
    track = BgmTrack(
        id="track",
        path=source,
        metadata_path=metadata,
        level="project",
        sha256="current-audio-hash",
        title="Track",
        creator=None,
        source_url=None,
        license=None,
        rights_status="unknown",
        subjects=(),
        moods=(),
        energy="low",
        tempo_bpm=None,
        instrumental=True,
        template_tags=(),
        avoid_for=(),
        preferred_start_ms=0,
        loopable=True,
        metadata_sha256="current-metadata-hash",
    )
    inputs = context.run_dir / "inputs"
    inputs.mkdir()
    (inputs / "library.snapshot.json").write_text(
        json.dumps(
            {
                "bgm": {
                    "level": "project",
                    "files": [
                        {
                            "path": str(source),
                            "sha256": "original-audio-hash",
                            "metadata": {
                                "path": str(metadata),
                                "sha256": "original-metadata-hash",
                            },
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    library = BgmLibrarySelection(
        "project",
        source.parent,
        (track,),
        (),
    )

    with pytest.raises(RuntimeError, match="snapshot_mismatch"):
        main.ensure_bgm_library_snapshot(context, library)


def test_disabled_bgm_bypasses_library_resolution_and_snapshot(context, monkeypatch):
    context.config["bgm"]["enabled"] = False
    monkeypatch.setattr(
        main,
        "resolve_bgm_library",
        lambda *_args: pytest.fail("disabled BGM must not resolve libraries"),
    )
    monkeypatch.setattr(
        main,
        "ensure_bgm_library_snapshot",
        lambda *_args: pytest.fail("disabled BGM must not verify snapshots"),
    )

    resolved = main.resolve_bgm_for_context(context)

    assert resolved.mode == "narration_only"
    assert any("disabled" in warning.lower() for warning in resolved.warnings)


def test_bgm_policy_is_loaded_from_immutable_run_snapshot(context):
    template_root = context.template.root
    template_root.mkdir(parents=True)
    policy_path = template_root / "bgm.json"
    original = {"enabled": True, "preferred_moods": ["reflective"]}
    policy_path.write_text(json.dumps(original), encoding="utf-8")
    context.template = SimpleNamespace(
        id="demo",
        root=template_root,
        paths={"bgm": policy_path},
    )
    inputs = context.run_dir / "inputs"
    inputs.mkdir()
    snapshot = {
        "id": "demo",
        "files": {"bgm.json": main.sha256_file(policy_path)},
        "bgm_policy": {
            "source_path": "bgm.json",
            "source_sha256": main.sha256_file(policy_path),
            "content": original,
            "content_sha256": main._stable_payload_hash(original),
        },
    }
    (inputs / "template.snapshot.json").write_text(
        json.dumps(snapshot),
        encoding="utf-8",
    )
    policy_path.write_text(
        json.dumps({"enabled": True, "preferred_moods": ["urgent"]}),
        encoding="utf-8",
    )

    assert main._effective_bgm_policy(context).preferred_moods == ("reflective",)


def test_legacy_run_freezes_bgm_policy_once(context):
    template_root = context.template.root
    template_root.mkdir(parents=True)
    policy_path = template_root / "bgm.json"
    policy_path.write_text(
        json.dumps({"enabled": True, "preferred_moods": ["reflective"]}),
        encoding="utf-8",
    )
    context.template = SimpleNamespace(
        id="demo",
        root=template_root,
        paths={"bgm": policy_path},
    )
    inputs = context.run_dir / "inputs"
    inputs.mkdir()
    snapshot_path = inputs / "template.snapshot.json"
    snapshot_path.write_text(json.dumps({"id": "demo", "files": {}}), encoding="utf-8")

    first = main._effective_bgm_policy(context)
    policy_path.write_text(
        json.dumps({"enabled": True, "preferred_moods": ["urgent"]}),
        encoding="utf-8",
    )
    second = main._effective_bgm_policy(context)

    assert first.preferred_moods == ("reflective",)
    assert second == first
    frozen = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert frozen["bgm_policy"]["content"]["preferred_moods"] == ["reflective"]


def test_narration_only_report_selects_original_narration(
    context,
    monkeypatch,
):
    from videocreator.bgm_audit import write_narration_only_report

    narration = Path(context.manifest["artifacts"]["voice_audio"])
    report_path = context.run_dir / "audio" / "bgm-mix-report.json"
    monkeypatch.setattr(
        "videocreator.bgm_audit.probe_media",
        lambda _path: MediaMetadata("audio", "mp3", None, None, 1000),
    )
    write_narration_only_report(narration, report_path, ["No BGM"])
    fallback = resolution("narration_only")
    main._write_bgm_selection(context, fallback, None)
    main._bind_bgm_report_to_workflow(context, fallback, report_path)
    context.manifest["artifacts"]["bgm_mix_report"] = str(report_path)
    main._record_bgm_lineage(
        context,
        fallback,
        narration,
        report_path,
    )

    selected = main.resolve_context_render_audio(context)

    assert selected == narration

    context.config["bgm"]["final_lufs"] = -14.0
    with pytest.raises(RuntimeError, match="workflow_config_mismatch"):
        main.resolve_context_render_audio(context)


def test_render_gate_rejects_sibling_run_selection_path(context, monkeypatch):
    from videocreator.bgm_audit import write_narration_only_report

    narration = Path(context.manifest["artifacts"]["voice_audio"])
    report_path = context.run_dir / "audio" / "bgm-mix-report.json"
    monkeypatch.setattr(
        "videocreator.bgm_audit.probe_media",
        lambda _path: MediaMetadata("audio", "mp3", None, None, 1000),
    )
    write_narration_only_report(narration, report_path, [])
    fallback = resolution("narration_only")
    selection_path = main._write_bgm_selection(context, fallback, None)
    main._bind_bgm_report_to_workflow(context, fallback, report_path)
    context.manifest["artifacts"]["bgm_mix_report"] = str(report_path)
    main._record_bgm_lineage(context, fallback, narration, report_path)
    sibling = context.project_root / "runs" / "run-002" / "audio"
    sibling.mkdir(parents=True)
    sibling_selection = sibling / "bgm-selection.json"
    sibling_selection.write_bytes(selection_path.read_bytes())
    context.manifest["artifacts"]["bgm_selection"] = str(sibling_selection)
    context.manifest["lineage"]["bgm"]["selection"] = str(sibling_selection)

    with pytest.raises(RuntimeError, match="bgm_artifact_outside_run"):
        main.resolve_context_render_audio(context)


def test_render_gate_requires_exact_current_narration_path(context, monkeypatch):
    from videocreator.bgm_audit import write_narration_only_report

    narration = Path(context.manifest["artifacts"]["voice_audio"])
    sibling = context.project_root / "runs" / "run-002" / "audio"
    sibling.mkdir(parents=True)
    sibling_voice = sibling / "voice.mp3"
    sibling_voice.write_bytes(narration.read_bytes())
    report_path = context.run_dir / "audio" / "bgm-mix-report.json"
    monkeypatch.setattr(
        "videocreator.bgm_audit.probe_media",
        lambda _path: MediaMetadata("audio", "mp3", None, None, 1000),
    )
    write_narration_only_report(sibling_voice, report_path, [])
    fallback = resolution("narration_only")
    main._write_bgm_selection(context, fallback, None)
    main._bind_bgm_report_to_workflow(context, fallback, report_path)
    context.manifest["artifacts"]["bgm_mix_report"] = str(report_path)
    main._record_bgm_lineage(context, fallback, narration, report_path)

    with pytest.raises(RuntimeError, match="bgm_narration_path_mismatch"):
        main.resolve_context_render_audio(context)


def test_stale_final_mix_is_rejected_before_render(context):
    narration = Path(context.manifest["artifacts"]["voice_audio"])
    source = context.run_dir / "audio" / "bgm.source.wav"
    metadata = context.run_dir / "audio" / "bgm.source.bgm.json"
    prepared = context.run_dir / "audio" / "bgm.prepared.wav"
    final_mix = context.run_dir / "audio" / "final-mix.wav"
    for path, value in (
        (source, b"source"),
        (metadata, b"{}"),
        (prepared, b"prepared"),
        (final_mix, b"mix"),
    ):
        path.write_bytes(value)

    def artifact(path: Path) -> dict:
        import hashlib

        return {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "duration_ms": 1000,
        }

    report = {
        "schema_version": 1,
        "mode": "bgm",
        "status": "passed",
        "inputs": {
            "narration": artifact(narration),
            "bgm": {
                **artifact(source),
                "metadata_path": str(metadata),
                "metadata_sha256": artifact(metadata)["sha256"],
                "level": "project",
            },
        },
        "outputs": {
            "prepared_bgm": artifact(prepared),
            "render_audio": artifact(final_mix),
        },
        "policy_sha256": "policy",
        "configuration_sha256": "config",
        "measurement": {
            "integrated_lufs": -16.0,
            "true_peak_dbtp": -1.5,
        },
        "ffmpeg": {"version": "fixture", "commands": []},
        "provenance": {"rights_status": "cleared"},
        "warnings": [],
        "findings": [],
    }
    report_path = context.run_dir / "audio" / "bgm-mix-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    context.manifest["artifacts"].update(
        {
            "final_mix": str(final_mix),
            "bgm_mix_report": str(report_path),
        }
    )
    final_mix.write_bytes(b"changed")

    with pytest.raises(RuntimeError, match="artifact_hash_mismatch"):
        main.resolve_context_render_audio(context)
