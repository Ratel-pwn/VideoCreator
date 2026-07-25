import json
from pathlib import Path

import pytest

from videocreator import durable_io


def test_atomic_json_preserves_previous_file_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "state.json"
    target.write_text('{"status":"old"}\n', encoding="utf-8")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated crash")

    monkeypatch.setattr(durable_io.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated crash"):
        durable_io.atomic_write_json(target, {"status": "new"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "old"}
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_atomic_json_flushes_file_before_replace_and_parent_after(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "state.json"
    events: list[str] = []
    original_fsync = durable_io.os.fsync
    original_replace = durable_io.os.replace

    def record_fsync(fd: int) -> None:
        events.append("fsync")
        original_fsync(fd)

    def record_replace(source: Path, destination: Path) -> None:
        events.append("replace")
        original_replace(source, destination)

    monkeypatch.setattr(durable_io.os, "fsync", record_fsync)
    monkeypatch.setattr(durable_io.os, "replace", record_replace)

    durable_io.atomic_write_json(target, {"status": "new"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "new"}
    assert events[0:2] == ["fsync", "replace"]
    assert events.count("fsync") >= 1
