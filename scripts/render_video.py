from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
RENDERER_ROOT = REPO_ROOT / "renderer"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def probe_output(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    video = next(
        (stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"),
        None,
    )
    audio = next(
        (stream for stream in payload.get("streams", []) if stream.get("codec_type") == "audio"),
        None,
    )
    if video is None or audio is None:
        raise RuntimeError("Rendered output must contain video and audio streams")
    if video.get("codec_name") != "h264":
        raise RuntimeError(f"Expected H.264 video, got {video.get('codec_name')}")
    if (video.get("width"), video.get("height")) != (1920, 1080):
        raise RuntimeError(
            f"Expected 1920x1080, got {video.get('width')}x{video.get('height')}"
        )
    if video.get("avg_frame_rate") != "25/1":
        raise RuntimeError(f"Expected 25fps, got {video.get('avg_frame_rate')}")
    return {
        "video_codec": video["codec_name"],
        "audio_codec": audio.get("codec_name"),
        "width": video["width"],
        "height": video["height"],
        "frame_rate": video["avg_frame_rate"],
        "duration_seconds": float(payload["format"]["duration"]),
    }


def trim_to_timeline(output_path: Path, duration_seconds: float) -> None:
    trimmed_path = output_path.with_name(f"{output_path.stem}.timeline{output_path.suffix}")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(output_path),
            "-t",
            f"{duration_seconds:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(trimmed_path),
        ],
        check=True,
        capture_output=True,
    )
    os.replace(trimmed_path, output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render and audit a Remotion video")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    for label, path in (("project root", project_root), ("render input", input_path)):
        if not path.exists():
            print(f"Missing {label}: {path}", file=sys.stderr)
            return 2
    if output_path.exists() and not output_path.is_file():
        print(f"Output is not a file path: {output_path}", file=sys.stderr)
        return 2

    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = input_path.with_name("render.log")
    report_path = input_path.with_name("render-report.json")
    package = json.loads((RENDERER_ROOT / "package.json").read_text(encoding="utf-8"))
    started_at = now_iso()
    npm = shutil.which("npm") or "npm"
    command = [
        npm,
        "--prefix",
        str(RENDERER_ROOT),
        "run",
        "render",
        "--",
        "--project-root",
        str(project_root),
        "--input",
        str(input_path),
        "--output",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log_path.write_text(
        f"$ {' '.join(command)}\n\nSTDOUT\n{completed.stdout}\nSTDERR\n{completed.stderr}",
        encoding="utf-8",
    )
    report: dict[str, Any] = {
        "status": "failed" if completed.returncode else "rendered",
        "input_path": str(input_path),
        "output_path": str(output_path),
        "log_path": str(log_path),
        "started_at": started_at,
        "finished_at": now_iso(),
        "remotion_version": package["dependencies"]["remotion"],
    }
    if completed.returncode:
        report["return_code"] = completed.returncode
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(completed.stderr or completed.stdout, file=sys.stderr)
        return completed.returncode

    try:
        render_input = json.loads(input_path.read_text(encoding="utf-8"))
        duration_seconds = render_input["durationInFrames"] / render_input["fps"]
        trim_to_timeline(output_path, duration_seconds)
        report["output_metadata"] = probe_output(output_path)
        report["timeline_duration_seconds"] = duration_seconds
        report["status"] = "verified"
    except Exception as exc:
        report["status"] = "audit_failed"
        report["error"] = str(exc)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Output audit failed: {exc}", file=sys.stderr)
        return 1
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
