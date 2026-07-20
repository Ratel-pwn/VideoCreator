import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_create_asset_request_cli_writes_web_curated_requests(tmp_path: Path):
    plan_path = tmp_path / "visual-plan.json"
    output_path = tmp_path / "asset-request.json"
    plan_path.write_text(
        json.dumps(
            {
                "topic": "demo",
                "segments": [
                    {
                        "segment_id": "scene-001",
                        "start_ms": 0,
                        "end_ms": 1000,
                        "material_type": "image",
                        "asset_strategy": "generate_only",
                    },
                    {
                        "segment_id": "scene-002",
                        "start_ms": 1000,
                        "end_ms": 2000,
                        "material_type": "subtitle_only",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/create_asset_request.py",
            "--visual-plan",
            str(plan_path),
            "--output",
            str(output_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["request_count"] == 1
    assert result["requests"][0]["strategy"] == "web_curated"


def test_audit_asset_manifest_cli_returns_one_for_invalid_manifest(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan_path = project_root / "visual-plan.json"
    manifest_path = project_root / "asset-manifest.json"
    output_path = project_root / "asset-audit.json"
    plan_path.write_text(
        json.dumps(
            {
                "segments": [
                    {"segment_id": "scene-001", "material_type": "image"}
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(json.dumps({"segments": []}), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/audit_asset_manifest.py",
            "--project-root",
            str(project_root),
            "--visual-plan",
            str(plan_path),
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert result["errors"] == ["scene-001: expected exactly one asset record"]
