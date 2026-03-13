#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

try:
    import websocket
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: websocket-client\n"
        "Install with: pip install websocket-client"
    ) from exc

WS_URL = "wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream"
SUCCESS_STATUS = 20000000
EVENT_SESSION_FINISHED = 152
EVENT_CONNECTION_FINISHED = 52
EVENT_TTS_RESPONSE = 352
SENTENCE_END_RE = re.compile(r"(?<=[。！？!?；;])")


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"failed to parse config file: {path} ({exc})") from exc


def parse_args() -> argparse.Namespace:
    default_config = Path(__file__).resolve().with_name("volc_tts_ws.config.json")
    parser = argparse.ArgumentParser(description="Synthesize speech with Volcengine TTS V3 WebSocket")
    parser.add_argument("--config", default=default_config, type=Path)
    parser.add_argument("--text", default=None, help="Text to synthesize. Overrides config text.")
    parser.add_argument("--text-file", type=Path, default=None, help="UTF-8 text file to synthesize. Overrides config text and avoids shell encoding issues.")
    parser.add_argument("--output", type=Path, default=None, help="Output audio file path.")
    parser.add_argument("--subtitle-output", type=Path, default=None, help="Optional SRT output path.")
    parser.add_argument("--uid", default=None, help="Optional user uid for request context.")
    parser.add_argument("--no-subtitle", action="store_true", help="Disable subtitle generation in request.")
    return parser.parse_args()


def infer_resource_id(speaker_id: str, configured: str) -> str:
    if configured:
        return configured
    if speaker_id.startswith("S_"):
        return "volc.megatts.default"
    return "volc.service_type.10029"


def build_send_text_frame(payload: dict[str, Any]) -> bytes:
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    header = bytes([0x11, 0x10, 0x10, 0x00])
    return header + len(payload_bytes).to_bytes(4, "big") + payload_bytes


def build_finish_connection_frame() -> bytes:
    payload_bytes = b"{}"
    header = bytes([0x11, 0x14, 0x10, 0x00])
    return header + len(payload_bytes).to_bytes(4, "big") + payload_bytes


def parse_frame(raw: bytes) -> dict[str, Any]:
    if len(raw) < 4:
        raise ValueError("invalid frame: too short")

    header_size = (raw[0] & 0x0F) * 4
    message_type = (raw[1] & 0xF0) >> 4
    message_flags = raw[1] & 0x0F

    if header_size < 4 or len(raw) < header_size:
        raise ValueError("invalid frame: bad header size")

    if message_type == 0x0F:
        error_code = int.from_bytes(raw[4:8], "big") if len(raw) >= 8 else -1
        return {
            "kind": "error",
            "error_code": error_code,
            "payload": raw[8:].decode("utf-8", errors="replace"),
        }

    cursor = header_size
    event_code = None
    if message_flags == 0x04:
        if len(raw) < cursor + 4:
            raise ValueError("invalid frame: missing event code")
        event_code = int.from_bytes(raw[cursor:cursor + 4], "big")
        cursor += 4

    if len(raw) < cursor + 4:
        raise ValueError("invalid frame: missing session length")
    session_len = int.from_bytes(raw[cursor:cursor + 4], "big")
    cursor += 4

    if len(raw) < cursor + session_len + 4:
        raise ValueError("invalid frame: truncated session id")
    session_id = raw[cursor:cursor + session_len].decode("utf-8", errors="replace")
    cursor += session_len

    payload_len = int.from_bytes(raw[cursor:cursor + 4], "big")
    cursor += 4
    payload = raw[cursor:cursor + payload_len]
    if len(payload) != payload_len:
        raise ValueError("invalid frame: truncated payload")

    return {
        "kind": "event",
        "event_code": event_code,
        "session_id": session_id,
        "payload": payload,
    }


def maybe_parse_json(data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return None


def coalesce_config(cli: argparse.Namespace, cfg: dict[str, Any]) -> dict[str, Any]:
    if cli.text_file is not None:
        try:
            text = cli.text_file.read_text(encoding="utf-8-sig")
        except Exception as exc:
            raise ValueError(f"failed to read text file: {cli.text_file} ({exc})") from exc
    else:
        text = cli.text if cli.text is not None else cfg.get("text", "")
    if not text.strip():
        raise ValueError("text is required. Set it in volc_tts_ws.config.json or pass --text/--text-file.")

    speaker_id = cfg.get("speaker_id") or ""
    resource_id = infer_resource_id(speaker_id, cfg.get("resource_id") or "")
    output = cli.output or Path(cfg.get("output_file") or "volc_tts_output.mp3")
    subtitle_output = cli.subtitle_output
    if subtitle_output is None and bool(cfg.get("enable_subtitle", False)) and output.suffix:
        subtitle_output = output.with_suffix(".srt")

    return {
        "appid": cfg.get("appid"),
        "access_token": cfg.get("access_token"),
        "resource_id": resource_id,
        "speaker_id": speaker_id,
        "format": cfg.get("format") or "mp3",
        "sample_rate": int(cfg.get("sample_rate") or 24000),
        "explicit_language": cfg.get("explicit_language") or "zh-cn",
        "model": cfg.get("model") or "seed-tts-2.0-expressive",
        "enable_subtitle": False if cli.no_subtitle else bool(cfg.get("enable_subtitle", False)),
        "text": text,
        "output": output,
        "subtitle_output": subtitle_output,
        "uid": cli.uid or cfg.get("uid") or "chaos-museum",
        "speech_rate": int(cfg.get("speech_rate", 0)),
        "loudness_rate": int(cfg.get("loudness_rate", 0)),
        "disable_markdown_filter": bool(cfg.get("disable_markdown_filter", False)),
    }


def validate_settings(settings: dict[str, Any]) -> None:
    required = ["appid", "access_token", "resource_id", "speaker_id"]
    missing = [key for key in required if not settings.get(key)]
    if missing:
        raise ValueError(f"missing required config fields: {', '.join(missing)}")

    if settings["speaker_id"].startswith("S_") and settings["resource_id"] != "volc.megatts.default":
        print(
            "Warning: official demo maps S_ cloned voices to resource_id volc.megatts.default. "
            "If this still fails, verify the exact granted resource in the Volcengine console.",
            file=sys.stderr,
        )

    if settings["disable_markdown_filter"]:
        print(
            "Warning: disable_markdown_filter=true is not supported for some TTS2.0 / ICL2.0 voices.",
            file=sys.stderr,
        )


def build_request_payload(settings: dict[str, Any], text: str) -> dict[str, Any]:
    additions = {
        "disable_markdown_filter": settings["disable_markdown_filter"],
    }
    if settings.get("explicit_language"):
        additions["explicit_language"] = settings["explicit_language"]

    return {
        "user": {"uid": settings["uid"]},
        "req_params": {
            "text": text,
            "speaker": settings["speaker_id"],
            "model": settings["model"],
            "audio_params": {
                "format": settings["format"],
                "sample_rate": settings["sample_rate"],
                "speech_rate": settings["speech_rate"],
                "loudness_rate": settings["loudness_rate"],
                "enable_subtitle": settings["enable_subtitle"],
            },
            "additions": json.dumps(additions, ensure_ascii=False, separators=(",", ":")),
        },
    }


def format_srt_timestamp(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def extract_subtitle_block(event_payload: Any) -> tuple[float, float, str] | None:
    if not isinstance(event_payload, dict):
        return None
    words = event_payload.get("words")
    text = event_payload.get("text")
    if not isinstance(words, list) or not text:
        return None
    timed_words = [w for w in words if isinstance(w, dict) and "startTime" in w and "endTime" in w]
    if not timed_words:
        return None
    return float(timed_words[0]["startTime"]), float(timed_words[-1]["endTime"]), str(text)


def write_srt(path: Path, subtitle_blocks: list[tuple[float, float, str]]) -> None:
    if not subtitle_blocks:
        return
    lines: list[str] = []
    for idx, (start, end, text) in enumerate(subtitle_blocks, start=1):
        lines.append(str(idx))
        lines.append(f"{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}")
        lines.append(text.strip())
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def split_text(text: str, max_chars: int = 120) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    pieces = [part.strip() for part in SENTENCE_END_RE.split(normalized) if part.strip()]
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if not current:
            current = piece
            continue
        if len(current) + len(piece) <= max_chars:
            current += piece
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)

    refined: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            refined.append(chunk)
            continue
        subparts = re.split(r"(?<=[，、,：:])", chunk)
        current = ""
        for sub in [s.strip() for s in subparts if s.strip()]:
            if not current:
                current = sub
                continue
            if len(current) + len(sub) <= max_chars:
                current += sub
            else:
                refined.append(current)
                current = sub
        if current:
            refined.append(current)
    return refined


def synthesize_chunk(settings: dict[str, Any], text: str) -> tuple[bytes, list[tuple[float, float, str]]]:
    headers = [
        f"X-Api-App-Key: {settings['appid']}",
        f"X-Api-App-Id: {settings['appid']}",
        f"X-Api-Access-Key: {settings['access_token']}",
        f"X-Api-Resource-Id: {settings['resource_id']}",
        f"X-Api-Connect-Id: {uuid.uuid4()}",
        "X-Control-Require-Usage-Tokens-Return: text_words",
    ]

    ws = websocket.create_connection(WS_URL, header=headers, timeout=60)
    audio_chunks: list[bytes] = []
    subtitle_blocks: list[tuple[float, float, str]] = []
    try:
        ws.send_binary(build_send_text_frame(build_request_payload(settings, text)))
        while True:
            raw = ws.recv()
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            frame = parse_frame(raw)
            if frame["kind"] == "error":
                raise RuntimeError(f"server error {frame['error_code']}: {frame['payload']}")

            event_code = frame["event_code"]
            payload = frame["payload"]
            if event_code == EVENT_TTS_RESPONSE:
                audio_chunks.append(payload)
                continue

            parsed = maybe_parse_json(payload)
            block = extract_subtitle_block(parsed)
            if block is not None:
                subtitle_blocks.append(block)

            if event_code == EVENT_SESSION_FINISHED:
                if isinstance(parsed, dict) and "status_code" in parsed:
                    status_code = int(parsed.get("status_code", -1))
                    if status_code != SUCCESS_STATUS:
                        raise RuntimeError(
                            f"session finished with status {status_code}: {parsed.get('message', 'unknown error')} | payload={json.dumps(parsed, ensure_ascii=False)}"
                        )
                break

        try:
            ws.send_binary(build_finish_connection_frame())
            ws.recv()
        except Exception:
            pass
    finally:
        ws.close()

    return b"".join(audio_chunks), subtitle_blocks


def synthesize(settings: dict[str, Any]) -> tuple[int, list[tuple[float, float, str]], int]:
    chunks = split_text(settings["text"])
    if not chunks:
        raise RuntimeError("no text chunks to synthesize")

    all_audio: list[bytes] = []
    all_subtitles: list[tuple[float, float, str]] = []
    offset = 0.0
    for index, chunk in enumerate(chunks, start=1):
        audio_data, subtitle_blocks = synthesize_chunk(settings, chunk)
        if not audio_data:
            raise RuntimeError(f"chunk {index} returned no audio")
        all_audio.append(audio_data)
        adjusted_blocks: list[tuple[float, float, str]] = []
        max_end = 0.0
        for start, end, text in subtitle_blocks:
            adjusted_blocks.append((start + offset, end + offset, text))
            if end > max_end:
                max_end = end
        all_subtitles.extend(adjusted_blocks)
        if subtitle_blocks:
            offset += max_end
        else:
            estimated = max(1.0, len(chunk) / 4.5)
            offset += estimated

    audio_data = b"".join(all_audio)
    settings["output"].parent.mkdir(parents=True, exist_ok=True)
    settings["output"].write_bytes(audio_data)
    return len(audio_data), all_subtitles, len(chunks)


def main() -> int:
    cli = parse_args()
    try:
        cfg = load_config(cli.config)
        settings = coalesce_config(cli, cfg)
        validate_settings(settings)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        audio_size, subtitle_blocks, chunk_count = synthesize(settings)
    except Exception as exc:
        print(f"Synthesis failed: {exc}", file=sys.stderr)
        return 1

    print(f"Audio written: {settings['output']} ({audio_size} bytes, {chunk_count} chunks)")
    subtitle_output = settings.get("subtitle_output")
    if subtitle_output:
        write_srt(subtitle_output, subtitle_blocks)
        if subtitle_output.exists():
            print(f"Subtitle written: {subtitle_output}")
        elif settings["enable_subtitle"]:
            print("Subtitle requested, but no subtitle events were returned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
