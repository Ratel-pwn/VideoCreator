import hashlib
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

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
