#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import whisper

VISIBLE_CHAR_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
SENTENCE_END_RE = re.compile(r"(?<=[。！？!?；;])")
PHRASE_BREAK_RE = re.compile(r"(?<=[，、,：:])")


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"failed to parse config file: {path} ({exc})") from exc


def parse_args() -> argparse.Namespace:
    default_config = Path(__file__).resolve().with_name("whisper_batch_transcribe.config.json")
    parser = argparse.ArgumentParser(description="Build SRT from original text using Whisper timestamps only")
    parser.add_argument("--config", default=default_config, type=Path)
    parser.add_argument("--audio-file", required=True, type=Path, help="Final audio file to transcribe for timestamps")
    parser.add_argument("--text-file", required=True, type=Path, help="Original UTF-8 text file used for synthesis")
    parser.add_argument("--output-srt", default=None, type=Path, help="Output SRT path. Defaults to <audio-file>.srt")
    parser.add_argument("--model", default=None, help="Whisper model override")
    parser.add_argument("--language", default=None, help="Language override, e.g. zh/en")
    parser.add_argument("--device", default=None, help="auto/cpu/cuda")
    parser.add_argument("--beam-size", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--max-chars", type=int, default=28, help="Preferred max chars per subtitle chunk")
    return parser.parse_args()


def strip_markdown(text: str) -> str:
    text = re.sub(r"^#.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def visible_char_count(text: str) -> int:
    return len(VISIBLE_CHAR_RE.findall(text))


def split_original_text(text: str, max_chars: int) -> list[str]:
    normalized = re.sub(r"\s+", " ", strip_markdown(text)).strip()
    if not normalized:
        return []

    sentences = [part.strip() for part in SENTENCE_END_RE.split(normalized) if part.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
            continue
        if visible_char_count(current) + visible_char_count(sentence) <= max_chars:
            current += sentence
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)

    refined: list[str] = []
    for chunk in chunks:
        if visible_char_count(chunk) <= max_chars:
            refined.append(chunk)
            continue
        parts = [part.strip() for part in PHRASE_BREAK_RE.split(chunk) if part.strip()]
        current = ""
        for part in parts:
            if not current:
                current = part
                continue
            if visible_char_count(current) + visible_char_count(part) <= max_chars:
                current += part
            else:
                refined.append(current)
                current = part
        if current:
            refined.append(current)
    return refined


def resolve_device(configured: str) -> str:
    if configured != "auto":
        return configured
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def expand_word_units(result: dict[str, Any]) -> list[tuple[float, float]]:
    units: list[tuple[float, float]] = []
    segments = result.get("segments") or []
    for segment in segments:
        words = segment.get("words") or []
        for word in words:
            try:
                start = float(word["start"])
                end = float(word["end"])
            except Exception:
                continue
            count = visible_char_count(str(word.get("word", "")))
            if count <= 0:
                continue
            duration = max(0.0, end - start)
            if count == 1 or duration <= 0:
                units.append((start, end))
                continue
            step = duration / count
            for index in range(count):
                unit_start = start + step * index
                unit_end = end if index == count - 1 else start + step * (index + 1)
                units.append((unit_start, unit_end))
    return units


def build_subtitle_blocks(chunks: list[str], units: list[tuple[float, float]], fallback_end: float) -> list[tuple[float, float, str]]:
    if not chunks:
        return []
    if not units:
        total_chars = sum(max(1, visible_char_count(chunk)) for chunk in chunks)
        elapsed = 0.0
        blocks: list[tuple[float, float, str]] = []
        for chunk in chunks:
            chunk_chars = max(1, visible_char_count(chunk))
            start = fallback_end * elapsed / total_chars
            elapsed += chunk_chars
            end = fallback_end * elapsed / total_chars
            blocks.append((start, end, chunk))
        return blocks

    blocks: list[tuple[float, float, str]] = []
    cursor = 0
    total_units = len(units)
    for index, chunk in enumerate(chunks):
        if cursor >= total_units:
            break
        if index == len(chunks) - 1:
            end_index = total_units - 1
        else:
            required = max(1, visible_char_count(chunk))
            end_index = min(total_units - 1, cursor + required - 1)
        start = units[cursor][0]
        end = units[end_index][1]
        blocks.append((start, end, chunk))
        cursor = end_index + 1

    if blocks and blocks[-1][1] < fallback_end:
        start, _, text = blocks[-1]
        blocks[-1] = (start, fallback_end, text)
    return blocks


def format_srt_timestamp(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(path: Path, blocks: list[tuple[float, float, str]]) -> None:
    lines: list[str] = []
    for index, (start, end, text) in enumerate(blocks, start=1):
        lines.append(str(index))
        lines.append(f"{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}")
        lines.append(text.strip())
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    cli = parse_args()
    try:
        cfg = load_config(cli.config)
        model_name = cli.model or cfg.get("model") or "medium"
        language = cli.language or cfg.get("language") or "zh"
        device = resolve_device(cli.device or cfg.get("device") or "auto")
        beam_size = cli.beam_size if cli.beam_size is not None else int(cfg.get("beam_size", 5))
        temperature = cli.temperature if cli.temperature is not None else float(cfg.get("temperature", 0.0))
        original_text = cli.text_file.read_text(encoding="utf-8-sig")
        output_srt = cli.output_srt or cli.audio_file.with_suffix(".srt")
    except Exception as exc:
        print(f"Subtitle alignment setup failed: {exc}", file=sys.stderr)
        return 2

    try:
        print(f"Loading Whisper model: {model_name} on {device}")
        model = whisper.load_model(model_name, device=device)
        result = model.transcribe(
            str(cli.audio_file),
            language=None if language == "auto" else language,
            task="transcribe",
            beam_size=beam_size,
            temperature=temperature,
            verbose=False,
            word_timestamps=True,
        )
    except Exception as exc:
        print(f"Whisper transcription failed: {exc}", file=sys.stderr)
        return 1

    chunks = split_original_text(original_text, cli.max_chars)
    total_end = 0.0
    segments = result.get("segments") or []
    if segments:
        total_end = float(segments[-1].get("end", 0.0) or 0.0)
    units = expand_word_units(result)
    blocks = build_subtitle_blocks(chunks, units, total_end)
    write_srt(output_srt, blocks)
    print(f"Subtitle aligned: {output_srt} ({len(blocks)} blocks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
