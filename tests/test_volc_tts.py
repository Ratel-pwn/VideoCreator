import json
from pathlib import Path

import pytest

from scripts import volc_tts_ws
from scripts.volc_tts_ws import (
    repair_tts_segment,
    synthesize,
    write_audio_chunks,
)


def test_write_audio_chunks_uses_ffmpeg_for_multiple_mp3_streams(tmp_path: Path):
    output = tmp_path / "narration.mp3"
    calls = []

    def run(command, check, capture_output):
        calls.append(command)
        concat_file = Path(command[command.index("-i") + 1])
        concat_manifest = concat_file.read_text(encoding="utf-8")
        assert "chunk-0001.mp3" in concat_manifest
        assert "chunk-0002.mp3" in concat_manifest
        output.write_bytes(b"merged")

    size = write_audio_chunks(
        [b"first mp3 stream", b"second mp3 stream"],
        output,
        audio_format="mp3",
        runner=run,
    )

    assert size == len(b"merged")
    assert output.read_bytes() == b"merged"
    assert len(calls) == 1
    assert calls[0][:4] == ["ffmpeg", "-y", "-f", "concat"]


def test_synthesize_retains_ordered_segments_and_manifest(tmp_path, monkeypatch):
    output = tmp_path / "narration.mp3"
    manifest_path = tmp_path / "tts-segments.json"
    settings = {
        "text": "第一句。第二句。",
        "output": output,
        "format": "mp3",
        "speaker_id": "speaker",
        "segment_manifest": manifest_path,
        "repair_segment": None,
    }
    generated = iter([(b"one", []), (b"two", [])])
    monkeypatch.setattr(volc_tts_ws, "split_text", lambda _text: ["第一句。", "第二句。"])
    monkeypatch.setattr(volc_tts_ws, "synthesize_chunk", lambda *_args: next(generated))

    def merge(chunks, destination, **_kwargs):
        destination.write_bytes(b"".join(chunks))
        return destination.stat().st_size

    monkeypatch.setattr(volc_tts_ws, "write_audio_chunks", merge)

    synthesize(settings)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [item["id"] for item in manifest["segments"]] == [
        "segment-0001",
        "segment-0002",
    ]
    assert [item["text"] for item in manifest["segments"]] == ["第一句。", "第二句。"]
    assert all(item["audio_sha256"] for item in manifest["segments"])
    assert all(item["generation_attempts"] == 0 for item in manifest["segments"])


def test_repair_segment_rejects_second_regeneration(tmp_path):
    segment = tmp_path / "segments" / "segment-0001.mp3"
    segment.parent.mkdir()
    segment.write_bytes(b"audio")
    output = tmp_path / "narration.mp3"
    output.write_bytes(b"audio")
    manifest_path = tmp_path / "tts-segments.json"
    manifest_path.write_text(
        json.dumps({
            "schema_version": 1,
            "output_path": str(output),
            "segments": [{
                "id": "segment-0001",
                "text": "第一句",
                "audio_path": str(segment),
                "generation_attempts": 1,
            }],
        }),
        encoding="utf-8",
    )
    settings = {
        "segment_manifest": manifest_path,
        "repair_segment": "segment-0001",
        "output": output,
        "format": "mp3",
    }

    with pytest.raises(ValueError, match="regeneration limit"):
        repair_tts_segment(settings, "segment-0001", max_attempts=1)
