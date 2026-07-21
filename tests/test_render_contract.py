import pytest

from videocreator.render_contract import build_render_input, normalize_scenes, normalize_v2_scenes


def test_normalize_scenes_absorbs_gaps_and_ends_at_audio_boundary():
    plan = {
        "segments": [
            {
                "segment_id": "scene-001",
                "start_ms": 0,
                "end_ms": 1000,
                "material_type": "image",
            },
            {
                "segment_id": "scene-002",
                "start_ms": 1200,
                "end_ms": 2000,
                "material_type": "subtitle_only",
            },
        ]
    }
    assets = {
        "scene-001": {
            "asset_type": "image",
            "local_path": "assets/scene-001.jpg",
        }
    }

    scenes = normalize_scenes(plan, assets, fps=25, spoken_end_ms=2200)

    assert scenes[0]["fromFrame"] == 0
    assert scenes[0]["durationInFrames"] == 30
    assert scenes[1]["fromFrame"] == 30
    assert scenes[1]["durationInFrames"] == 25


def test_build_render_input_uses_last_scene_as_duration():
    scenes = [
        {
            "id": "scene-001",
            "fromFrame": 0,
            "durationInFrames": 55,
            "assetType": "subtitle_only",
            "assetPath": "",
            "fitMode": "cover",
            "trimBeforeFrames": 0,
            "mediaDurationInFrames": 0,
            "shortVideoPolicy": "reject",
            "motionPreset": "push-left",
        }
    ]

    value = build_render_input(
        video_id="capitalism-pandora",
        scenes=scenes,
        audio_path="audio/voice.cleaned.mp3",
        subtitle_path="audio/voice.cleaned.srt",
    )

    assert value["width"] == 1920
    assert value["height"] == 1080
    assert value["fps"] == 25
    assert value["durationInFrames"] == 55
    assert value["audioPath"] == "audio/voice.cleaned.mp3"


def test_build_render_input_maps_project_presentation_to_frame():
    scenes = [
        {
            "id": "scene-001",
            "fromFrame": 0,
            "durationInFrames": 25,
            "assetType": "subtitle_only",
            "assetPath": "",
            "fitMode": "cover",
            "trimBeforeFrames": 0,
            "mediaDurationInFrames": 0,
            "shortVideoPolicy": "reject",
            "motionPreset": "none",
        }
    ]

    value = build_render_input(
        video_id="capitalism-pandora",
        scenes=scenes,
        audio_path="audio/voice.cleaned.mp3",
        subtitle_path="audio/voice.cleaned.srt",
        presentation={
            "frame_preset": "editorial-wide",
            "video_title": "\u8d44\u672c\u4e3b\u4e49\u7684\u6f58\u591a\u62c9\u9b54\u76d2\u662f\u5982\u4f55\u5f00\u542f\u7684\uff1f",
            "publication_date": "2026.07.21",
            "creator_handle": "@\u901a\u804c\u8005Ratel",
        },
    )

    assert value["frame"] == {
        "preset": "editorial-wide",
        "videoTitle": "\u8d44\u672c\u4e3b\u4e49\u7684\u6f58\u591a\u62c9\u9b54\u76d2\u662f\u5982\u4f55\u5f00\u542f\u7684\uff1f",
        "publicationDate": "2026.07.21",
        "creatorHandle": "@\u901a\u804c\u8005Ratel",
    }


def test_build_render_input_rejects_incomplete_presentation():
    scenes = [
        {
            "id": "scene-001",
            "fromFrame": 0,
            "durationInFrames": 25,
            "assetType": "subtitle_only",
            "assetPath": "",
            "fitMode": "cover",
            "trimBeforeFrames": 0,
            "mediaDurationInFrames": 0,
            "shortVideoPolicy": "reject",
            "motionPreset": "none",
        }
    ]

    with pytest.raises(ValueError, match="publication_date"):
        build_render_input(
            video_id="capitalism-pandora",
            scenes=scenes,
            audio_path="audio/voice.cleaned.mp3",
            subtitle_path="audio/voice.cleaned.srt",
            presentation={
                "frame_preset": "editorial-wide",
                "video_title": "Title",
                "creator_handle": "@Ratel",
            },
        )


def test_normalize_v2_scenes_builds_mode_aware_payloads():
    plan = {
        "schema_version": 2,
        "segments": [
            {"segment_id": "scene-001", "start_ms": 0, "end_ms": 1000, "presentation_mode": "footage", "slots": [{"role": "primary", "required_type": "video"}]},
            {"segment_id": "scene-002", "start_ms": 1000, "end_ms": 2000, "presentation_mode": "entity_card", "slots": [{"role": "background", "required_type": "image"}, {"role": "display", "required_type": "image"}], "entity": {"primary_label": "Book", "secondary_label": "Title"}},
            {"segment_id": "scene-003", "start_ms": 2000, "end_ms": 3000, "presentation_mode": "explainer", "slots": [{"role": "background", "required_type": "image"}], "explainer": {"kind": "list", "items": ["A", "B"]}},
            {"segment_id": "scene-004", "start_ms": 3000, "end_ms": 4000, "presentation_mode": "subtitle_only", "slots": []},
        ],
    }
    assets = {
        "scene-001:primary": {"asset_type": "video", "local_path": "assets/open.mp4", "fit_mode": "cover", "duration_ms": 1000},
        "scene-002:background": {"asset_type": "image", "local_path": "assets/bg.jpg", "fit_mode": "cover"},
        "scene-002:display": {"asset_type": "image", "local_path": "assets/book.jpg", "fit_mode": "contain"},
        "scene-003:background": {"asset_type": "image", "local_path": "assets/concept.jpg", "fit_mode": "cover"},
    }

    scenes = normalize_v2_scenes(plan, assets, fps=25, spoken_end_ms=4000)

    assert scenes[0]["presentationMode"] == "footage"
    assert scenes[0]["primaryAsset"]["assetType"] == "video"
    assert scenes[1]["displayAsset"]["assetPath"] == "assets/book.jpg"
    assert scenes[1]["entity"]["primaryLabel"] == "Book"
    assert scenes[2]["explainer"]["kind"] == "list"
    assert scenes[3]["presentationMode"] == "subtitle_only"
