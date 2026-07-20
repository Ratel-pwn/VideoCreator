from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True)


@pytest.mark.integration
@pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in ("node", "npm", "ffmpeg", "ffprobe")),
    reason="Node, npm, FFmpeg, and ffprobe are required",
)
def test_remotion_renders_verified_three_second_mp4(tmp_path: Path):
    project_root = tmp_path / "project"
    assets = project_root / "assets"
    audio = project_root / "audio"
    run_dir = project_root / "runs" / "fixture"
    for directory in (assets, audio, run_dir):
        directory.mkdir(parents=True)

    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            str(audio / "voice.wav"),
        ]
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0xC84B31:s=1920x1080",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(assets / "still.png"),
        ]
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x1C6E8C:s=1920x1080:d=1:r=25",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(assets / "clip.mp4"),
        ]
    )
    (audio / "voice.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,500\nFirst caption\n\n"
        "2\n00:00:01,500 --> 00:00:03,000\nSecond caption\n",
        encoding="utf-8",
    )

    input_path = run_dir / "render-input.json"
    output_path = run_dir / "fixture.mp4"
    input_path.write_text(
        json.dumps(
            {
                "videoId": "fixture",
                "width": 1920,
                "height": 1080,
                "fps": 25,
                "durationInFrames": 75,
                "audioPath": "audio/voice.wav",
                "subtitlePath": "audio/voice.srt",
                "backgroundColor": "#080b0f",
                "scenes": [
                    {
                        "id": "still",
                        "fromFrame": 0,
                        "durationInFrames": 25,
                        "assetType": "image",
                        "assetPath": "assets/still.png",
                        "fitMode": "cover",
                        "trimBeforeFrames": 0,
                        "mediaDurationInFrames": 0,
                        "shortVideoPolicy": "reject",
                        "motionPreset": "push-left",
                    },
                    {
                        "id": "video",
                        "fromFrame": 25,
                        "durationInFrames": 25,
                        "assetType": "video",
                        "assetPath": "assets/clip.mp4",
                        "fitMode": "cover",
                        "trimBeforeFrames": 0,
                        "mediaDurationInFrames": 25,
                        "shortVideoPolicy": "reject",
                        "motionPreset": "none",
                    },
                    {
                        "id": "text",
                        "fromFrame": 50,
                        "durationInFrames": 25,
                        "assetType": "subtitle_only",
                        "assetPath": "",
                        "fitMode": "cover",
                        "trimBeforeFrames": 0,
                        "mediaDurationInFrames": 0,
                        "shortVideoPolicy": "reject",
                        "motionPreset": "none",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "render_video.py"),
            "--project-root",
            str(project_root),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    assert output_path.exists()
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    metadata = json.loads(probe.stdout)
    video = next(stream for stream in metadata["streams"] if stream["codec_type"] == "video")
    audio_stream = next(
        stream for stream in metadata["streams"] if stream["codec_type"] == "audio"
    )
    assert (video["width"], video["height"]) == (1920, 1080)
    assert video["codec_name"] == "h264"
    assert video["avg_frame_rate"] == "25/1"
    assert audio_stream["codec_name"] == "aac"
    assert abs(float(metadata["format"]["duration"]) - 3) <= 0.04
    assert (run_dir / "render-report.json").exists()
