#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

import whisper
from whisper.utils import get_writer


SUPPORTED_EXTS = {
    ".mp4",
    ".mkv",
    ".webm",
    ".mov",
    ".avi",
    ".mp3",
    ".m4a",
    ".wav",
    ".flac",
    ".aac",
}


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"failed to parse config file: {path} ({exc})") from exc


def parse_args() -> argparse.Namespace:
    default_config = Path(__file__).resolve().with_name("whisper_batch_transcribe.config.json")
    parser = argparse.ArgumentParser(description="Batch transcribe media files with openai-whisper")
    parser.add_argument(
        "--config",
        default=default_config,
        type=Path,
        help=f"JSON config file (default: {default_config})",
    )
    parser.add_argument("--input-dir", type=Path, default=None, help="Folder containing media files")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output folder for transcript files",
    )
    parser.add_argument("--model", default=None, help="Whisper model: tiny/base/small/medium/large/turbo")
    parser.add_argument("--language", default=None, help="Language code, e.g. zh/en/ja; use auto for autodetect")
    parser.add_argument("--task", default=None, choices=["transcribe", "translate"])
    parser.add_argument(
        "--output-format",
        default=None,
        choices=["txt", "vtt", "srt", "tsv", "json", "all"],
        help="Transcript output format",
    )
    parser.add_argument("--device", default=None, help='auto/cpu/cuda, e.g. "cuda"')
    parser.add_argument("--beam-size", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--recursive", action="store_true", default=None, help="Scan input folder recursively")
    parser.add_argument("--force", action="store_true", default=None, help="Overwrite existing transcript outputs")
    parser.add_argument("--dry-run", action="store_true", default=None, help="Only print target files, no transcription")
    return parser.parse_args()


def find_media_files(root: Path, recursive: bool) -> list[Path]:
    if recursive:
        files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS]
    else:
        files = [p for p in root.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS]
    return sorted(files)


def main() -> int:
    cli = parse_args()

    try:
        cfg = load_config(cli.config)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    input_dir = Path(cli.input_dir or cfg.get("input_dir") or r"E:\Media\Videos\chaos")
    output_dir = Path(cli.output_dir or cfg.get("output_dir") or r"E:\Media\Videos\chaos\transcripts")
    model_name = cli.model or cfg.get("model") or "medium"
    language = cli.language or cfg.get("language") or "zh"
    task = cli.task or cfg.get("task") or "transcribe"
    output_format = cli.output_format or cfg.get("output_format") or "srt"
    device = cli.device or cfg.get("device") or "auto"
    beam_size = cli.beam_size if cli.beam_size is not None else int(cfg.get("beam_size", 5))
    temperature = cli.temperature if cli.temperature is not None else float(cfg.get("temperature", 0.0))
    recursive = cli.recursive if cli.recursive is not None else bool(cfg.get("recursive", False))
    force = cli.force if cli.force is not None else bool(cfg.get("force", False))
    dry_run = cli.dry_run if cli.dry_run is not None else bool(cfg.get("dry_run", False))

    if not input_dir.exists():
        print(f"Input dir not found: {input_dir}", file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    files = find_media_files(input_dir, recursive)
    if not files:
        print("No media files found.")
        return 0

    print(f"Found {len(files)} file(s).")
    for f in files:
        print(f" - {f}")

    if dry_run:
        return 0

    if device == "auto":
        try:
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"

    print(f"Loading model: {model_name} on {device}")
    model = whisper.load_model(model_name, device=device)
    writer = get_writer(output_format, str(output_dir))

    ok = 0
    fail = 0
    skip = 0
    output_suffixes = [output_format] if output_format != "all" else ["txt", "vtt", "srt", "tsv", "json"]

    for idx, media_path in enumerate(files, start=1):
        stem = media_path.stem
        existing = [output_dir / f"{stem}.{ext}" for ext in output_suffixes]
        # Skip early if any target output already exists (unless --force).
        if (not force) and any(p.exists() for p in existing):
            skip += 1
            print(f"[{idx}/{len(files)}] SKIP {media_path.name} (output exists)")
            continue

        try:
            print(f"[{idx}/{len(files)}] RUN  {media_path.name}")
            result = model.transcribe(
                str(media_path),
                language=None if language == "auto" else language,
                task=task,
                beam_size=beam_size,
                temperature=temperature,
                verbose=False,
            )
            writer(result, str(media_path))
            ok += 1
            print(f"[{idx}/{len(files)}] OK   {media_path.name}")
        except Exception as exc:
            fail += 1
            print(f"[{idx}/{len(files)}] FAIL {media_path.name}: {exc}")
            traceback.print_exc()

    print(f"\nFinished: success={ok} fail={fail} skip={skip}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
