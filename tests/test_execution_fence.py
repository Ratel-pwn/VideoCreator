import sys
import threading
import time
from pathlib import Path

import pytest

from videocreator.execution_fence import (
    LeaseLostError,
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
