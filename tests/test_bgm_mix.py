import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from videocreator.bgm_library import BgmTrack
from videocreator.bgm_policy import BgmPolicy
from videocreator.media import MediaMetadata


def make_track(tmp_path: Path, *, loopable: bool = True) -> BgmTrack:
    audio = tmp_path / "track.wav"
    metadata = tmp_path / "track.bgm.json"
    audio.write_bytes(b"source-bgm")
    metadata.write_text("{}", encoding="utf-8")
    return BgmTrack(
        id="track",
        path=audio,
        metadata_path=metadata,
        level="project",
        sha256="unused-by-mixer",
        title="Track",
        creator="Composer",
        source_url="https://example.com/track",
        license=None,
        rights_status="unknown",
        subjects=("ideas",),
        moods=("reflective",),
        energy="low",
        tempo_bpm=90,
        instrumental=True,
        template_tags=(),
        avoid_for=(),
        preferred_start_ms=500,
        loopable=loopable,
    )


def test_short_track_filter_uses_equal_power_crossfades():
    from videocreator.bgm_mix import build_bgm_filter

    value = build_bgm_filter(
        track_duration_ms=12_000,
        narration_duration_ms=35_000,
        policy=BgmPolicy(),
    )

    assert "asplit=4" in value
    assert value.count("acrossfade") == 3
    assert "c1=tri:c2=tri" in value
    assert "atrim=duration=35" in value
    assert "afade=t=in" in value
    assert "afade=t=out" in value


def test_long_track_filter_crops_without_looping():
    from videocreator.bgm_mix import build_bgm_filter

    value = build_bgm_filter(40_000, 35_000, BgmPolicy())

    assert "asplit" not in value
    assert "acrossfade" not in value
    assert "atrim=duration=35" in value
    assert "aresample=48000" in value
    assert "channel_layouts=stereo" in value


def test_filter_omits_disabled_fades():
    from videocreator.bgm_mix import build_bgm_filter

    value = build_bgm_filter(
        40_000,
        35_000,
        BgmPolicy(fade_in_ms=0, fade_out_ms=0),
    )

    assert "afade" not in value


def test_filter_rejects_negative_fades():
    from videocreator.bgm_mix import BgmMixError, build_bgm_filter

    with pytest.raises(BgmMixError, match="non-negative"):
        build_bgm_filter(
            40_000,
            35_000,
            BgmPolicy(fade_in_ms=-1),
        )


def test_mix_rejects_short_non_loopable_track(tmp_path, monkeypatch):
    from videocreator.bgm_mix import BgmMixError, mix_bgm

    narration = tmp_path / "narration.wav"
    narration.write_bytes(b"narration")
    track = make_track(tmp_path, loopable=False)
    durations = {
        narration: MediaMetadata("audio", "pcm", None, None, 35_000),
        track.path: MediaMetadata("audio", "pcm", None, None, 12_000),
    }
    monkeypatch.setattr(
        "videocreator.bgm_mix.probe_media", lambda path: durations[path]
    )

    with pytest.raises(BgmMixError, match="track is too short and is not loopable"):
        mix_bgm(
            narration,
            track,
            tmp_path / "prepared.wav",
            tmp_path / "final-mix.wav",
            BgmPolicy(),
            Mock(),
        )


def test_mix_uses_parameter_arrays_and_sidechain_graph(tmp_path, monkeypatch):
    from videocreator.bgm_mix import mix_bgm

    narration = tmp_path / "narration.wav"
    narration.write_bytes(b"narration")
    track = make_track(tmp_path)
    prepared = tmp_path / "prepared.wav"
    final_mix = tmp_path / "final-mix.wav"

    def probe(path):
        durations = {
            narration: 35_000,
            track.path: 12_000,
            prepared: 35_000,
            final_mix: 35_020,
        }
        return MediaMetadata("audio", "pcm_s24le", None, None, durations[path])

    def runner(command, **kwargs):
        assert isinstance(command, list)
        if command[0] == "ffmpeg" and command[-1] == str(prepared):
            prepared.write_bytes(b"prepared")
        elif command[0] == "ffmpeg" and command[-1] == str(final_mix):
            final_mix.write_bytes(b"mix")
        if command[:2] == ["ffmpeg", "-version"]:
            return subprocess.CompletedProcess(command, 0, "ffmpeg version 7.0\n", "")
        if "print_format=json" in " ".join(command):
            payload = json.dumps({"input_i": "-16.2", "input_tp": "-1.7"})
            return subprocess.CompletedProcess(command, 0, "", f"log\n{payload}\n")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("videocreator.bgm_mix.probe_media", probe)
    run = Mock(side_effect=runner)

    result = mix_bgm(
        narration,
        track,
        prepared,
        final_mix,
        BgmPolicy(ducking_strength="strong"),
        run,
    )

    prepare_command = run.call_args_list[0].args[0]
    mix_command = run.call_args_list[1].args[0]
    graph = mix_command[mix_command.index("-filter_complex") + 1]
    assert prepare_command[prepare_command.index("-ss") + 1] == "0.500"
    assert "sidechaincompress=threshold=0.02:ratio=12" in graph
    assert "amix=inputs=2:duration=first:normalize=0" in graph
    assert "loudnorm=I=-16:LRA=11:TP=-1.5" in graph
    assert mix_command[mix_command.index("-c:a") + 1] == "pcm_s24le"
    assert result.measured_lufs == -16.2
    assert result.true_peak_dbtp == -1.7
    assert result.mix_duration_ms == 35_020
    assert result.configuration_hash
    assert "rights status is unknown" in " ".join(result.warnings)


def test_mix_raises_when_output_duration_exceeds_tolerance(tmp_path, monkeypatch):
    from videocreator.bgm_mix import BgmMixError, mix_bgm

    narration = tmp_path / "narration.wav"
    narration.write_bytes(b"narration")
    track = make_track(tmp_path)
    prepared = tmp_path / "prepared.wav"
    final_mix = tmp_path / "final-mix.wav"

    def probe(path):
        duration = {
            narration: 35_000,
            track.path: 40_000,
            prepared: 35_000,
            final_mix: 35_101,
        }[path]
        return MediaMetadata("audio", "pcm", None, None, duration)

    def runner(command, **_kwargs):
        if str(prepared) in command:
            prepared.write_bytes(b"prepared")
        if str(final_mix) in command:
            final_mix.write_bytes(b"mix")
        if command[:2] == ["ffmpeg", "-version"]:
            return subprocess.CompletedProcess(command, 0, "ffmpeg version 7.0\n", "")
        if "print_format=json" in " ".join(command):
            payload = '{"input_i":"-16.0","input_tp":"-1.5"}'
            return subprocess.CompletedProcess(command, 0, "", payload)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("videocreator.bgm_mix.probe_media", probe)

    with pytest.raises(BgmMixError, match="duration"):
        mix_bgm(
            narration,
            track,
            prepared,
            final_mix,
            BgmPolicy(),
            runner,
        )
