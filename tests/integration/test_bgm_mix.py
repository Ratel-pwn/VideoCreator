import shutil
import subprocess
import re
from pathlib import Path

import pytest

from videocreator.bgm_library import BgmTrack
from videocreator.bgm_mix import mix_bgm
from videocreator.bgm_policy import BgmPolicy


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg and ffprobe are required",
)


def generate_sine(path: Path, duration: float, frequency: int):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def measure_band_mean(path: Path, start: float, duration: float) -> float:
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-i",
            str(path),
            "-af",
            "bandpass=f=180:width_type=h:width=20,volumedetect",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    match = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", completed.stderr)
    assert match is not None
    return float(match.group(1))


def make_track(path: Path, *, loopable: bool = True) -> BgmTrack:
    metadata = path.with_suffix(".bgm.json")
    metadata.write_text("{}", encoding="utf-8")
    return BgmTrack(
        id="integration",
        path=path,
        metadata_path=metadata,
        level="project",
        sha256="",
        title="Integration",
        creator=None,
        source_url=None,
        license=None,
        rights_status="known",
        subjects=(),
        moods=(),
        energy="low",
        tempo_bpm=90,
        instrumental=True,
        template_tags=(),
        avoid_for=(),
        preferred_start_ms=0,
        loopable=loopable,
    )


@pytest.mark.parametrize(("bgm_duration", "expects_loop"), [(4.0, False), (0.9, True)])
def test_real_ffmpeg_mix_crops_or_loops_and_decodes(
    tmp_path, bgm_duration, expects_loop
):
    narration = tmp_path / "narration.wav"
    bgm_path = tmp_path / "bgm.wav"
    prepared = tmp_path / "prepared.wav"
    final_mix = tmp_path / "final-mix.wav"
    generate_sine(narration, 2.5, 440)
    generate_sine(bgm_path, bgm_duration, 180)

    result = mix_bgm(
        narration,
        make_track(bgm_path),
        prepared,
        final_mix,
        BgmPolicy(fade_in_ms=100, fade_out_ms=100),
        subprocess.run,
    )

    assert abs(result.mix_duration_ms - result.narration_duration_ms) <= 100
    assert prepared.is_file()
    assert final_mix.is_file()
    graph = " ".join(" ".join(command) for command in result.command_parameters)
    assert ("acrossfade" in graph) is expects_loop
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(final_mix), "-f", "null", "-"],
        check=True,
        capture_output=True,
    )


def test_real_ffmpeg_sidechain_ducks_bgm_during_speech(tmp_path):
    narration = tmp_path / "narration.wav"
    bgm_path = tmp_path / "bgm.wav"
    prepared = tmp_path / "prepared.wav"
    final_mix = tmp_path / "final-mix.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=1.5",
            "-af",
            "adelay=1000|1000,apad",
            "-t",
            "2.5",
            "-c:a",
            "pcm_s16le",
            str(narration),
        ],
        check=True,
        capture_output=True,
    )
    generate_sine(bgm_path, 4.0, 180)

    mix_bgm(
        narration,
        make_track(bgm_path),
        prepared,
        final_mix,
        BgmPolicy(
            ducking_strength="strong",
            fade_in_ms=0,
            fade_out_ms=0,
        ),
        subprocess.run,
    )

    pause_level = measure_band_mean(final_mix, 0.2, 0.5)
    speech_level = measure_band_mean(final_mix, 1.4, 0.5)
    assert speech_level <= pause_level - 2.0
