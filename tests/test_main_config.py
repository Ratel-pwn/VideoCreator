import hashlib
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

import main
from main import load_json, prepare_cloned_voice, resume_context


def test_load_json_accepts_utf8_bom(tmp_path):
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps({"renderer": "remotion"}), encoding="utf-8-sig")

    assert load_json(path) == {"renderer": "remotion"}


def test_resume_context_uses_project_containing_the_run(tmp_path: Path):
    repo_root = tmp_path / "isolated-code"
    project_root = tmp_path / "real-project"
    run_dir = project_root / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    repo_root.mkdir()
    config_path = repo_root / "workflow.config.json"
    config_path.write_text(
        json.dumps({"projects": {"root": "projects"}}), encoding="utf-8"
    )
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "project_name": "real-project",
                "current_stage": "video_render",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps({"project_name": "real-project", "artifacts": {}}),
        encoding="utf-8",
    )

    context = resume_context(repo_root, config_path, run_dir)

    assert context.project_root == project_root


def test_resume_migrates_legacy_video_render_through_sync_once(tmp_path: Path):
    repo_root = tmp_path / "repo"
    project_root = tmp_path / "projects" / "demo"
    run_dir = project_root / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    repo_root.mkdir()
    config_path = repo_root / "workflow.config.json"
    config_path.write_text(
        json.dumps({"templates": {"root": "templates"}}),
        encoding="utf-8",
    )
    (project_root / "project.json").write_text(
        json.dumps({"name": "demo"}),
        encoding="utf-8",
    )
    voice = run_dir / "audio" / "voice.mp3"
    voice.parent.mkdir()
    voice.write_bytes(b"voice")
    artifacts = {"voice_audio": str(voice)}
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "project_name": "demo",
                "current_stage": "video_render",
                "status": "failed",
                "last_error": "legacy render failed",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps({"project_name": "demo", "artifacts": artifacts}),
        encoding="utf-8",
    )

    first = resume_context(repo_root, config_path, run_dir)
    second = resume_context(repo_root, config_path, run_dir)

    assert first.state["current_stage"] == "subtitle_sync"
    assert first.state["resume_after_subtitle_sync"] == "bgm"
    assert first.state["status"] == "ready"
    assert "last_error" not in first.state
    assert first.manifest["artifacts"]["voice_audio"] == str(voice)
    assert first.state["migrations"]["quality_gates_v2"]["from"] == "video_render"
    assert second.state["current_stage"] == "subtitle_sync"
    assert second.state["migrations"] == first.state["migrations"]


def test_resume_routes_pre_feature_render_confirmation_back_through_sync(
    tmp_path: Path,
):
    repo_root = tmp_path / "repo"
    project_root = tmp_path / "projects" / "demo"
    run_dir = project_root / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    repo_root.mkdir()
    config_path = repo_root / "workflow.config.json"
    config_path.write_text(
        json.dumps({"templates": {"root": "templates"}}),
        encoding="utf-8",
    )
    (project_root / "project.json").write_text(
        json.dumps({"name": "demo"}),
        encoding="utf-8",
    )
    external_audio = project_root / "audio" / "voice.mp3"
    external_srt = project_root / "audio" / "voice.srt"
    external_draft = project_root / "drafts" / "draft.md"
    external_audio.parent.mkdir()
    external_draft.parent.mkdir()
    external_audio.write_bytes(b"voice")
    external_srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nText\n",
        encoding="utf-8",
    )
    external_draft.write_text("Text", encoding="utf-8")
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "project_name": "demo",
                "current_stage": "video_render_confirm",
                "status": "awaiting_confirmation",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "project_name": "demo",
                "artifacts": {
                    "voice_audio": str(external_audio),
                    "voice_subtitle": str(external_srt),
                    "draft_approved": str(external_draft),
                },
            }
        ),
        encoding="utf-8",
    )

    context = resume_context(repo_root, config_path, run_dir)

    assert context.state["current_stage"] == "subtitle_sync"
    assert context.state["resume_after_subtitle_sync"] == "bgm"
    assert context.state["migrations"]["quality_gates_v2"]["from"] == (
        "video_render_confirm"
    )
    for key in ("voice_audio", "voice_subtitle", "draft_approved"):
        Path(context.manifest["artifacts"][key]).resolve().relative_to(
            run_dir.resolve()
        )
    assert Path(context.manifest["artifacts"]["narration_text"]).is_file()


def test_resume_revalidates_pre_feature_confirmation_even_when_files_exist(
    tmp_path: Path,
):
    repo_root = tmp_path / "repo"
    project_root = tmp_path / "projects" / "demo"
    run_dir = project_root / "runs" / "run-1"
    for directory in ("audio", "subtitles", "review"):
        (run_dir / directory).mkdir(parents=True, exist_ok=True)
    repo_root.mkdir()
    config_path = repo_root / "workflow.config.json"
    config_path.write_text(
        json.dumps({"templates": {"root": "templates"}}),
        encoding="utf-8",
    )
    (project_root / "project.json").write_text(
        json.dumps({"name": "demo"}),
        encoding="utf-8",
    )
    artifacts = {}
    for key, relative, content in (
        ("voice_audio", "audio/voice.mp3", b"voice"),
        ("voice_subtitle", "subtitles/voice.srt", b"subtitle"),
        ("narration_text", "audio/narration.txt", b"text"),
        ("subtitle_alignment_report", "subtitles/alignment-report.json", b"{}"),
        ("bgm_mix_report", "audio/bgm-mix-report.json", b"{}"),
    ):
        path = run_dir / relative
        path.write_bytes(content)
        artifacts[key] = str(path)
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "project_name": "demo",
                "current_stage": "video_render_confirm",
                "status": "awaiting_confirmation",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps({"project_name": "demo", "artifacts": artifacts}),
        encoding="utf-8",
    )

    context = resume_context(repo_root, config_path, run_dir)

    assert context.state["current_stage"] == "subtitle_sync"
    assert context.state["resume_after_subtitle_sync"] == "bgm"
    assert context.state["status"] == "ready"


def test_resume_preserves_current_render_confirmation_gate(
    tmp_path: Path,
):
    repo_root = tmp_path / "repo"
    project_root = tmp_path / "projects" / "demo"
    run_dir = project_root / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    repo_root.mkdir()
    config_path = repo_root / "workflow.config.json"
    config_path.write_text(
        json.dumps({"templates": {"root": "templates"}}),
        encoding="utf-8",
    )
    (project_root / "project.json").write_text(
        json.dumps({"name": "demo"}),
        encoding="utf-8",
    )
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "project_name": "demo",
                "current_stage": "video_render_confirm",
                "status": "awaiting_confirmation",
                "quality_gate_version": 2,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps({"project_name": "demo", "artifacts": {}}),
        encoding="utf-8",
    )

    context = resume_context(repo_root, config_path, run_dir)

    assert context.state["current_stage"] == "video_render_confirm"
    assert context.state["status"] == "awaiting_confirmation"


def test_prepare_cloned_voice_binds_source_once_and_reuses_speaker(tmp_path: Path):
    source = tmp_path / "library" / "voice" / "default" / "voice.mp3"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"default voice")
    clone_script = tmp_path / "scripts" / "volc_clone_voice.py"
    clone_script.parent.mkdir()
    clone_script.write_text("", encoding="utf-8")
    tts_config = tmp_path / "scripts" / "volc_tts_ws.config.json"
    tts_config.write_text(
        json.dumps({
            "appid": "app",
            "access_token": "token",
            "speaker_id": "speaker",
            "clone_speaker_id": "speaker",
        }),
        encoding="utf-8",
    )
    save_calls = []
    context = SimpleNamespace(
        repo_root=tmp_path,
        config={"tts": {"clone_script": "scripts/volc_clone_voice.py"}},
        active_voice_source_file=source,
        manifest={"resources": {}},
        save_manifest=lambda: save_calls.append(True),
    )
    commands = []

    def run(command, check):
        commands.append(command)

    first = prepare_cloned_voice(context, tts_config, runner=run)
    second = prepare_cloned_voice(context, tts_config, runner=run)

    stored = json.loads(tts_config.read_text(encoding="utf-8"))
    assert first["cloned"] is True
    assert second["cloned"] is False
    assert len(commands) == 1
    assert commands[0][-2:] == ["--audio", str(source)]
    assert stored["voice_source_sha256"] == first["voice_source_sha256"]
    assert context.manifest["resources"]["voice_source_sha256"] == first["voice_source_sha256"]
    assert "voice_speaker_id" not in context.manifest["resources"]
    assert context.manifest["resources"]["voice_speaker_fingerprint"] == hashlib.sha256(
        b"speaker"
    ).hexdigest()
    assert len(save_calls) == 2


def test_prepare_cloned_voice_never_trains_in_existing_speaker_mode(tmp_path: Path):
    source = tmp_path / "voice.mp3"
    source.write_bytes(b"default voice")
    tts_config = tmp_path / "tts.json"
    tts_config.write_text(
        json.dumps({
            "appid": "app",
            "access_token": "token",
            "speaker_id": "speaker",
        }),
        encoding="utf-8",
    )
    context = SimpleNamespace(
        repo_root=tmp_path,
        config={"tts": {"voice_source_mode": "existing_speaker"}},
        active_voice_source_file=source,
        manifest={"resources": {}},
        save_manifest=lambda: None,
    )

    with pytest.raises(RuntimeError, match="not bound"):
        prepare_cloned_voice(
            context,
            tts_config,
            runner=lambda *_args, **_kwargs: pytest.fail("must not train"),
        )


def test_reassemble_tts_routes_ffmpeg_through_cancellable_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from scripts import volc_tts_ws

    segment = tmp_path / "segment.mp3"
    segment.write_bytes(b"segment")
    output = tmp_path / "voice.mp3"
    output.write_bytes(b"old")
    segment_manifest = tmp_path / "tts-segments.json"
    segment_manifest.write_text(
        json.dumps(
            {
                "speaker_fingerprint": "speaker",
                "segments": [
                    {
                        "ordinal": 1,
                        "audio_path": str(segment),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    observed = {}

    def process_runner(*_args, **_kwargs):
        return None

    def merge(chunks, destination, *, audio_format, runner=None):
        observed["runner"] = runner
        observed["destination"] = Path(destination)
        Path(destination).write_bytes(b"".join(chunks))
        return Path(destination).stat().st_size

    context = SimpleNamespace(
        manifest={
            "artifacts": {
                "tts_segment_manifest": str(segment_manifest),
                "voice_audio": str(output),
            }
        },
        config={"tts": {"output_format": "mp3"}},
        assert_active=lambda: None,
        run_process=process_runner,
    )
    monkeypatch.setattr(volc_tts_ws, "write_audio_chunks", merge)
    monkeypatch.setattr(main, "_run_alignment", lambda _ctx: "")

    main._reassemble_tts_audio(context, "all")

    assert observed["runner"] is process_runner
    assert observed["destination"] != output
    assert output.read_bytes() == b"segment"
