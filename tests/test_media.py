from pathlib import Path
from unittest.mock import Mock
import json

from videocreator.media import (
    clean_audio_and_srt,
    detect_trailing_silence,
    parse_ffprobe_json,
    parse_trailing_silence,
    probe_media,
)


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


def test_probe_media_routes_ffprobe_through_managed_runner(
    tmp_path: Path,
    monkeypatch,
):
    path = tmp_path / "voice.mp3"
    path.write_bytes(b"audio")
    calls = []
    completed = Mock(
        stdout=json.dumps(
            {
                "streams": [
                    {
                        "codec_type": "audio",
                        "codec_name": "mp3",
                        "duration": "1.25",
                    }
                ],
                "format": {"duration": "1.25"},
            }
        )
    )

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return completed

    monkeypatch.setattr(
        "videocreator.media.run_managed_process",
        run,
    )

    metadata = probe_media(path)

    assert metadata.kind == "audio"
    assert metadata.duration_ms == 1250
    assert calls[0][0][0] == "ffprobe"
    assert calls[0][1]["timeout"] > 0


def test_parse_trailing_silence_returns_absolute_spoken_end():
    log = "silence_start: 18.367417\nsilence_end: 69.064 | silence_duration: 50.696583"

    assert (
        parse_trailing_silence(
            log, analysis_offset_ms=200_000, total_duration_ms=269_126
        )
        == 218_367
    )


def test_parse_trailing_silence_rejects_non_trailing_silence():
    log = "silence_start: 2.0\nsilence_end: 5.0 | silence_duration: 3.0"

    assert (
        parse_trailing_silence(
            log, analysis_offset_ms=200_000, total_duration_ms=269_126
        )
        is None
    )


def test_detect_trailing_silence_analyzes_only_the_audio_tail(tmp_path: Path):
    completed = Mock()
    completed.stderr = (
        "silence_start: 18.367417\n"
        "silence_end: 69.064 | silence_duration: 50.696583"
    )
    runner = Mock(return_value=completed)

    spoken_end = detect_trailing_silence(
        tmp_path / "voice.mp3",
        total_duration_ms=269_126,
        analysis_window_ms=69_126,
        runner=runner,
    )

    assert spoken_end == 218_367
    command = runner.call_args.args[0]
    assert command[command.index("-ss") + 1] == "200.000"


def test_clean_audio_and_srt_creates_derivatives_without_changing_sources(tmp_path: Path):
    audio = tmp_path / "voice.mp3"
    subtitle = tmp_path / "voice.srt"
    audio.write_bytes(b"original-audio")
    original_srt = (
        "1\n00:00:00,000 --> 00:00:01,000\nFirst\n\n"
        "2\n00:00:01,000 --> 00:00:03,000\nSecond\n"
    )
    subtitle.write_text(original_srt, encoding="utf-8")
    runner = Mock()

    cleaned_audio, cleaned_srt = clean_audio_and_srt(
        audio,
        subtitle,
        tmp_path / "cleaned",
        spoken_end_ms=2_200,
        runner=runner,
        probe_duration=lambda _: 2_200,
    )

    assert cleaned_audio.name == "voice.cleaned.mp3"
    assert cleaned_srt.read_text(encoding="utf-8").endswith(
        "00:00:01,000 --> 00:00:02,200\nSecond\n"
    )
    assert audio.read_bytes() == b"original-audio"
    assert subtitle.read_text(encoding="utf-8") == original_srt
    command = runner.call_args.args[0]
    assert command[0] == "ffmpeg"
    assert command[command.index("-t") + 1] == "2.200"


def test_clean_audio_retries_with_measured_encoder_shortfall(tmp_path: Path):
    audio = tmp_path / "voice.mp3"
    subtitle = tmp_path / "voice.srt"
    audio.write_bytes(b"audio")
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:02,200\nCaption\n", encoding="utf-8"
    )
    runner = Mock()
    measured = iter([1_936, 2_200])

    clean_audio_and_srt(
        audio,
        subtitle,
        tmp_path / "cleaned",
        spoken_end_ms=2_200,
        runner=runner,
        probe_duration=lambda _: next(measured),
    )

    assert runner.call_count == 2
    second_command = runner.call_args_list[1].args[0]
    assert second_command[second_command.index("-t") + 1] == "2.464"
