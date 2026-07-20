from videocreator.render_contract import build_render_input, normalize_scenes


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
