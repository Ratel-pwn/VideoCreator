import json
import subprocess
import sys
from pathlib import Path

from videocreator.project_migration import migrate_capital_project


def test_migration_script_can_run_directly():
    root = Path(__file__).parents[1]
    result = subprocess.run([sys.executable, str(root / "scripts/migrate_project_layout.py"), "--help"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_migration_encapsulates_two_runs_and_does_not_touch_sibling(tmp_path: Path):
    project = tmp_path / "capital"
    sibling = tmp_path / "grasshopper"
    sibling.mkdir()
    sentinel = sibling / "keep.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    for folder in ("assets/video", "audio", "drafts", "sessions", "runs/old", "runs/new/review"):
        (project / folder).mkdir(parents=True, exist_ok=True)
    (project / "project.json").write_text("{}", encoding="utf-8")
    (project / "assets/a.jpg").write_bytes(b"image")
    (project / "assets/video/a.webm").write_bytes(b"video")
    (project / "audio/source.mp3").write_bytes(b"audio")
    (project / "audio/source.srt").write_text("srt", encoding="utf-8")
    (project / "audio/voice.cleaned.mp3").write_bytes(b"clean")
    (project / "audio/voice.cleaned.srt").write_text("clean-srt", encoding="utf-8")
    (project / "drafts/script.md").write_text("script", encoding="utf-8")
    (project / "drafts/visual-plan.json").write_text("{}", encoding="utf-8")
    (project / "drafts/visual-plan.v2.json").write_text("{}", encoding="utf-8")
    for run in ("old", "new"):
        for name in ("asset-manifest.json", "asset-audit.json", "render-input.json", "render-report.json", "render.log", "final.mp4", "state.json", "manifest.json"):
            path = project / "runs" / run / name
            path.write_text("{}" if name.endswith(".json") else name, encoding="utf-8")

    report = migrate_capital_project(project, "chaos-museum")

    assert report["ok"]
    assert json.loads((project / "project.json").read_text(encoding="utf-8"))["template_id"] == "chaos-museum"
    assert (project / "media/images/a.jpg").is_file()
    assert (project / "runs/old/writing/script.approved.md").is_file()
    assert (project / "runs/new/visual/visual-plan.json").is_file()
    assert (project / "runs/old/render/final.mp4").is_file()
    assert sentinel.read_text(encoding="utf-8") == "unchanged"
