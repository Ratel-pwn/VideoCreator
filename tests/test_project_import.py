import json
import subprocess
import sys
from pathlib import Path

import pytest

from videocreator.project_import import discover_legacy_artifacts, import_legacy_project


REPO_ROOT = Path(__file__).resolve().parents[1]


def make_project(root: Path, *, include_project_json: bool = True) -> None:
    for folder in ("audio", "drafts", "runs"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    if include_project_json:
        (root / "project.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "name": root.name,
                    "template_id": "chaos-museum",
                }
            ),
            encoding="utf-8",
        )
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


def test_import_materializes_immutable_run_local_inputs(tmp_path: Path):
    make_project(tmp_path)
    artifacts = discover_legacy_artifacts(tmp_path)

    run_dir = import_legacy_project(
        tmp_path,
        "legacy-run",
        artifacts,
        repo_root=REPO_ROOT,
    )

    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "subtitle_sync"
    assert state["resume_after_subtitle_sync"] == "visual_plan"
    for key in ("draft_approved", "voice_audio", "voice_subtitle", "visual_plan"):
        imported = Path(manifest["artifacts"][key]).resolve()
        imported.relative_to(run_dir.resolve())
        assert imported.is_file()
    assert Path(manifest["artifacts"]["narration_text"]).is_file()
    for snapshot in (
        "template.snapshot.json",
        "project.snapshot.json",
        "library.snapshot.json",
        "source-selection.json",
    ):
        assert (run_dir / "inputs" / snapshot).is_file()
    sources = json.loads(
        (run_dir / "inputs/source-selection.json").read_text(
            encoding="utf-8"
        )
    )
    assert {item["artifact"] for item in sources["files"]} == {
        "draft_approved",
        "voice_audio",
        "voice_subtitle",
        "visual_plan",
    }
    assert manifest["template"]["id"] == "chaos-museum"
    assert (tmp_path / "audio" / "voice.mp3").is_file()


def test_discovery_rejects_ambiguous_audio(tmp_path: Path):
    make_project(tmp_path)
    (tmp_path / "audio" / "second.mp3").write_bytes(b"audio")

    with pytest.raises(ValueError, match="ambiguous voice_audio"):
        discover_legacy_artifacts(tmp_path)


def test_import_requires_valid_project_template_before_publishing(
    tmp_path: Path,
):
    make_project(tmp_path, include_project_json=False)
    artifacts = discover_legacy_artifacts(tmp_path)

    with pytest.raises(ValueError, match="project.json"):
        import_legacy_project(
            tmp_path,
            "legacy-run",
            artifacts,
            repo_root=REPO_ROOT,
        )

    assert not (tmp_path / "runs/legacy-run").exists()


def test_import_failure_never_publishes_incomplete_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from videocreator import project_import

    make_project(tmp_path)
    artifacts = discover_legacy_artifacts(tmp_path)
    original_copy = project_import.atomic_copy_file
    calls = 0

    def fail_second_copy(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated copy failure")
        return original_copy(*args, **kwargs)

    monkeypatch.setattr(
        project_import,
        "atomic_copy_file",
        fail_second_copy,
    )

    with pytest.raises(OSError, match="copy failure"):
        import_legacy_project(
            tmp_path,
            "legacy-run",
            artifacts,
            repo_root=REPO_ROOT,
        )

    assert not (tmp_path / "runs/legacy-run").exists()
    assert list((tmp_path / "runs").glob(".creating-*")) == []


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
