import json
import subprocess
import sys
from pathlib import Path

import pytest

from videocreator.project_import import discover_legacy_artifacts, import_legacy_project


REPO_ROOT = Path(__file__).resolve().parents[1]


def make_project(root: Path) -> None:
    for folder in ("audio", "drafts", "runs"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "audio" / "voice.mp3").write_bytes(b"audio")
    (root / "audio" / "voice.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nText\n",
        encoding="utf-8",
    )
    (root / "drafts" / "draft.md").write_text("Draft", encoding="utf-8")
    (root / "drafts" / "visual-plan.json").write_text(
        '{"segments": []}',
        encoding="utf-8",
    )


def test_import_registers_existing_files_without_moving_them(tmp_path: Path):
    make_project(tmp_path)
    artifacts = discover_legacy_artifacts(tmp_path)

    run_dir = import_legacy_project(tmp_path, "legacy-run", artifacts)

    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "visual_assets"
    assert Path(manifest["artifacts"]["voice_audio"]).is_absolute()
    assert (tmp_path / "audio" / "voice.mp3").is_file()


def test_discovery_rejects_ambiguous_audio(tmp_path: Path):
    make_project(tmp_path)
    (tmp_path / "audio" / "second.mp3").write_bytes(b"audio")

    with pytest.raises(ValueError, match="ambiguous voice_audio"):
        discover_legacy_artifacts(tmp_path)


def test_import_legacy_project_cli_creates_run(tmp_path: Path):
    make_project(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/import_legacy_project.py",
            str(tmp_path),
            "--run-id",
            "cli-run",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "runs" / "cli-run" / "state.json").is_file()
