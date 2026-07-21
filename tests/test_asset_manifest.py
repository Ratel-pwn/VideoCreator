from pathlib import Path

from videocreator.asset_manifest import audit_asset_manifest, create_asset_requests
from videocreator.media import MediaMetadata


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


def test_generate_only_is_normalized_to_web_curated():
    visual_plan = {
        "topic": "demo",
        "segments": [
            {
                "segment_id": "scene-001",
                "start_ms": 0,
                "end_ms": 5000,
                "text": "Narration",
                "brief": "Historical city",
                "material_type": "image",
                "asset_strategy": "generate_only",
                "search_queries": {"image": ["historical city archive"]},
            }
        ],
    }

    result = create_asset_requests(visual_plan)

    assert result["requests"][0]["strategy"] == "web_curated"
    assert result["requests"][0]["rejection_criteria"]


def test_audit_rejects_low_resolution_video(monkeypatch, tmp_path: Path):
    asset = tmp_path / "assets" / "scene-001.mp4"
    asset.parent.mkdir()
    asset.write_bytes(b"video-fixture")
    manifest = {
        "segments": [
            {
                "scene_id": "scene-001",
                "asset_type": "video",
                "local_path": "assets/scene-001.mp4",
                "source_page_url": "https://example.org/page",
                "provider": "Example Archive",
                "license": "CC BY 4.0",
                "retrieved_at": "2026-07-20T12:00:00+08:00",
                "review_status": "approved",
            }
        ]
    }
    monkeypatch.setattr(
        "videocreator.asset_manifest._probe_media",
        lambda _: MediaMetadata("video", "h264", 640, 360, 5000),
    )

    result = audit_asset_manifest(tmp_path, plan(), manifest)

    assert "scene-001: video resolution must be at least 1280x720" in result.errors


def v2_plan():
    return {
        "schema_version": 2,
        "segments": [
            {
                "segment_id": "scene-001",
                "start_ms": 0,
                "end_ms": 3000,
                "presentation_mode": "footage",
                "slots": [{"role": "primary", "required_type": "video", "search_queries": ["factory"]}],
            },
            {
                "segment_id": "scene-002",
                "start_ms": 3000,
                "end_ms": 6000,
                "presentation_mode": "entity_card",
                "slots": [
                    {"role": "background", "required_type": "image", "search_queries": ["archive desk"]},
                    {"role": "display", "required_type": "image", "search_queries": ["book cover"]},
                ],
            },
        ],
    }


def test_v2_requests_are_generated_per_required_slot():
    result = create_asset_requests(v2_plan())

    assert [item["request_id"] for item in result["requests"]] == [
        "scene-001:primary",
        "scene-002:background",
        "scene-002:display",
    ]
    assert result["requests"][0]["required_asset_type"] == "video"


def test_v2_audit_rejects_video_slot_resolved_as_image(tmp_path: Path):
    asset = tmp_path / "assets" / "opening.jpg"
    asset.parent.mkdir()
    asset.write_bytes(b"image")
    manifest = {
        "schema_version": 2,
        "assets": [
            {
                "request_id": "scene-001:primary",
                "scene_id": "scene-001",
                "role": "primary",
                "asset_type": "image",
                "local_path": "assets/opening.jpg",
                "source_page_url": "https://example.org/opening",
                "provider": "Example",
                "license": "unknown",
                "credit": "Unknown",
                "retrieved_at": "2026-07-21",
                "rights_status": "unknown",
                "rights_note": "Publicly accessible; rights unknown.",
                "review_status": "approved",
            }
        ],
    }

    result = audit_asset_manifest(tmp_path, {"schema_version": 2, "segments": [v2_plan()["segments"][0]]}, manifest, probe_media=False)

    assert "scene-001:primary: expected video, got image" in result.errors
    assert any("rights_status is unknown" in warning for warning in result.warnings)


def test_v2_audit_rejects_manifest_version_mismatch(tmp_path: Path):
    result = audit_asset_manifest(tmp_path, v2_plan(), {"segments": []}, probe_media=False)

    assert "schema_version mismatch: visual plan v2 requires manifest v2" in result.errors
