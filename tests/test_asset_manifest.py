from pathlib import Path

from videocreator.asset_manifest import audit_asset_manifest


def plan():
    return {
        "segments": [
            {"segment_id": "scene-001", "material_type": "image"},
            {"segment_id": "scene-002", "material_type": "subtitle_only"},
        ]
    }


def test_audit_accepts_complete_approved_manifest(tmp_path: Path):
    asset = tmp_path / "assets" / "scene-001.jpg"
    asset.parent.mkdir()
    asset.write_bytes(b"jpeg-fixture")
    manifest = {
        "segments": [
            {
                "scene_id": "scene-001",
                "asset_type": "image",
                "local_path": "assets/scene-001.jpg",
                "source_page_url": "https://example.org/page",
                "direct_download_url": "https://example.org/image.jpg",
                "provider": "Example Archive",
                "license": "Public domain",
                "credit": "Example Archive",
                "retrieved_at": "2026-07-20T12:00:00+08:00",
                "fit_mode": "cover",
                "trim_start_ms": 0,
                "short_video_policy": "reject",
                "review_status": "approved",
            }
        ]
    }

    result = audit_asset_manifest(tmp_path, plan(), manifest, probe_media=False)

    assert result.errors == []
    assert result.approved_scene_ids == ["scene-001"]


def test_audit_rejects_missing_provenance_and_path_escape(tmp_path: Path):
    manifest = {
        "segments": [
            {
                "scene_id": "scene-001",
                "asset_type": "image",
                "local_path": "../outside.jpg",
                "source_page_url": "",
                "provider": "",
                "license": "",
                "retrieved_at": "",
                "review_status": "approved",
            }
        ]
    }

    result = audit_asset_manifest(tmp_path, plan(), manifest, probe_media=False)

    assert "scene-001: local_path escapes project root" in result.errors
    assert "scene-001: missing source_page_url" in result.errors
    assert "scene-001: missing license" in result.errors
