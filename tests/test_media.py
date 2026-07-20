from videocreator.media import parse_ffprobe_json


def test_parse_ffprobe_json_returns_video_metadata():
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "duration": "4.25",
            }
        ],
        "format": {"duration": "4.25"},
    }

    metadata = parse_ffprobe_json(payload)

    assert metadata.kind == "video"
    assert metadata.width == 1920
    assert metadata.duration_ms == 4250
