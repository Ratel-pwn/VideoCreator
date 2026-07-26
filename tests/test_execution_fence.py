import sys
import threading
import time
from pathlib import Path
import pytest

from videocreator import execution_fence
from videocreator.execution_fence import (
    LeaseLostError,
    ProcessOutputLimitError,
    RunMutationLock,
    run_cancellable_process,
)


def test_run_mutation_lock_excludes_replacement_worker(tmp_path: Path):
    lock_path = tmp_path / "run" / ".worker.lock"
    stale_worker = RunMutationLock(lock_path)
    replacement = RunMutationLock(lock_path)

    assert stale_worker.acquire()
    try:
        assert replacement.acquire() is False
    finally:
        stale_worker.release()

    assert replacement.acquire()
    replacement.release()


def test_cancellable_process_terminates_before_mutating_output(tmp_path: Path):
    marker = tmp_path / "stale-worker-output.txt"
    lease_lost = threading.Event()
    timer = threading.Timer(0.1, lease_lost.set)
    timer.start()
    try:
        with pytest.raises(LeaseLostError, match="during subprocess"):
            run_cancellable_process(
                [
                    sys.executable,
                    "-c",
                    (
                        "import pathlib,sys,time;"
                        "time.sleep(1);"
                        "pathlib.Path(sys.argv[1]).write_text('stale')"
                    ),
                    str(marker),
                ],
                cancelled=lease_lost.is_set,
                poll_seconds=0.02,
                check=True,
            )
    finally:
        timer.cancel()

    assert not marker.exists()


def test_cancellable_process_terminates_descendant_processes(tmp_path: Path):
    marker = tmp_path / "descendant-output.txt"
    lease_lost = threading.Event()
    timer = threading.Timer(0.2, lease_lost.set)
    timer.start()
    child_code = (
        "import pathlib,sys,time;"
        "time.sleep(1);"
        "pathlib.Path(sys.argv[1]).write_text('descendant')"
    )
    parent_code = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]);"
        "time.sleep(5)"
    )
    try:
        with pytest.raises(LeaseLostError, match="during subprocess"):
            run_cancellable_process(
                [
                    sys.executable,
                    "-c",
                    parent_code,
                    child_code,
                    str(marker),
                ],
                cancelled=lease_lost.is_set,
                poll_seconds=0.02,
                check=True,
            )
    finally:
        timer.cancel()

    time.sleep(1.1)
    assert not marker.exists()


def test_cancellable_process_enforces_bounded_capture_output():
    with pytest.raises(ProcessOutputLimitError):
        run_cancellable_process(
            [
                sys.executable,
                "-c",
                "import sys,time;sys.stdout.write('x'*200000);"
                "sys.stdout.flush();time.sleep(2)",
            ],
            cancelled=lambda: False,
            capture_output=True,
            max_output_bytes=1024,
            poll_seconds=0.01,
        )


def test_bounded_capture_rejects_output_limit_discovered_after_exit(
    monkeypatch: pytest.MonkeyPatch,
):
    class DelayedOutput:
        def __init__(self, value: bytes):
            self.value = value

        def read(self, _size):
            time.sleep(0.02)
            value, self.value = self.value, b""
            return value

    class Process:
        returncode = 0
        stdout = DelayedOutput(b"x" * 2048)
        stderr = DelayedOutput(b"")

        def wait(self, timeout=None):
            del timeout
            return self.returncode

        def poll(self):
            return self.returncode

    monkeypatch.setattr(execution_fence, "_is_windows", lambda: False)
    monkeypatch.setattr(
        execution_fence.subprocess,
        "Popen",
        lambda *_args, **_kwargs: Process(),
    )

    with pytest.raises(ProcessOutputLimitError):
        run_cancellable_process(
            ["fake"],
            cancelled=lambda: False,
            capture_output=True,
            max_output_bytes=1024,
            poll_seconds=0.1,
        )


def test_windows_process_is_suspended_fenced_then_resumed(
    monkeypatch: pytest.MonkeyPatch,
):
    events: list[str] = []

    class Process:
        returncode = 0
        _handle = 123

        def communicate(self, input=None, timeout=None):
            del input, timeout
            events.append("communicate")
            return b"", b""

        def poll(self):
            return self.returncode

        def kill(self):
            events.append("kill")

        def wait(self, timeout=None):
            del timeout
            return self.returncode

    class Job:
        @classmethod
        def attach(cls, _process):
            events.append("attach")
            return cls()

        def resume(self, _process):
            events.append("resume")

        def close(self):
            events.append("close")

        def terminate(self):
            events.append("terminate")

    captured = {}

    def popen(_command, **kwargs):
        events.append("popen")
        captured.update(kwargs)
        return Process()

    monkeypatch.setattr(execution_fence, "_is_windows", lambda: True)
    monkeypatch.setattr(execution_fence.subprocess, "Popen", popen)
    monkeypatch.setattr(execution_fence, "_WindowsProcessJob", Job)
    monkeypatch.setattr(
        execution_fence.subprocess,
        "CREATE_NEW_PROCESS_GROUP",
        0x200,
        raising=False,
    )
    monkeypatch.setattr(
        execution_fence.subprocess,
        "CREATE_SUSPENDED",
        0x004,
        raising=False,
    )

    result = run_cancellable_process(
        ["fake"],
        cancelled=lambda: False,
        capture_output=True,
    )

    assert result.returncode == 0
    assert captured["creationflags"] & 0x200
    assert captured["creationflags"] & 0x004
    assert events[:4] == ["popen", "attach", "resume", "communicate"]
    assert events[-1] == "close"
