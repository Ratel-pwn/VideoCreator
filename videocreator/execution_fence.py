from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable


class LeaseLostError(RuntimeError):
    pass


class ProcessOutputLimitError(RuntimeError):
    pass


_ACTIVE_PROCESS_RUNNER: ContextVar[Callable[..., Any] | None] = (
    ContextVar("videocreator_process_runner", default=None)
)


def _is_windows() -> bool:
    return os.name == "nt"


@contextmanager
def process_runner_scope(runner: Callable[..., Any]):
    token = _ACTIVE_PROCESS_RUNNER.set(runner)
    try:
        yield
    finally:
        _ACTIVE_PROCESS_RUNNER.reset(token)


def run_managed_process(command: Any, **kwargs: Any) -> Any:
    runner = _ACTIVE_PROCESS_RUNNER.get()
    if runner is not None:
        return runner(command, **kwargs)
    return run_cancellable_process(
        command,
        cancelled=lambda: False,
        **kwargs,
    )


class RunMutationLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._stream: Any = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self._stream = stream
        return True

    def release(self) -> None:
        if self._stream is None:
            return
        stream = self._stream
        self._stream = None
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()

    def __enter__(self) -> "RunMutationLock":
        if not self.acquire():
            raise LeaseLostError(f"Run mutation lock is already held: {self.path}")
        return self

    def __exit__(self, *_args: Any) -> None:
        self.release()


class _WindowsProcessJob:
    def __init__(self, kernel32: Any, ntdll: Any, handle: Any):
        self.kernel32 = kernel32
        self.ntdll = ntdll
        self.handle = handle

    @classmethod
    def attach(
        cls,
        process: subprocess.Popen[Any],
    ) -> "_WindowsProcessJob":
        import ctypes
        from ctypes import wintypes

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [
            ctypes.c_void_p,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.UINT,
        ]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        ntdll.NtResumeProcess.restype = ctypes.c_long

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(
                ctypes.get_last_error(),
                "Could not create subprocess job object",
            )
        info = ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
            handle,
            9,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(
                error,
                "Could not configure subprocess job object",
            )
        if not kernel32.AssignProcessToJobObject(
            handle,
            wintypes.HANDLE(int(process._handle)),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(
                error,
                "Could not fence subprocess in a job object",
            )
        return cls(kernel32, ntdll, handle)

    def resume(self, process: subprocess.Popen[Any]) -> None:
        import ctypes
        from ctypes import wintypes

        status = self.ntdll.NtResumeProcess(
            wintypes.HANDLE(int(process._handle))
        )
        if status != 0:
            raise OSError(
                int(status),
                "Could not resume fenced subprocess",
            )

    def terminate(self) -> None:
        if self.handle:
            self.kernel32.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle:
            handle = self.handle
            self.handle = None
            self.kernel32.CloseHandle(handle)


def _terminate_process_tree(
    process: subprocess.Popen[Any],
    *,
    windows_job: _WindowsProcessJob | None = None,
    grace_seconds: float = 2,
) -> None:
    if _is_windows():
        if windows_job is not None:
            windows_job.terminate()
        elif process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if process.poll() is None:
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _bounded_communicate(
    process: subprocess.Popen[Any],
    command: Any,
    *,
    cancelled: Callable[[], bool],
    windows_job: _WindowsProcessJob | None,
    max_output_bytes: int,
    timeout: float | int | None,
    poll_seconds: float,
    text_mode: bool,
    encoding: str,
) -> tuple[Any, Any]:
    if max_output_bytes < 1:
        raise ValueError("max_output_bytes must be positive")
    if process.stdout is None or process.stderr is None:
        raise ValueError(
            "max_output_bytes requires captured stdout and stderr"
        )
    chunks: dict[str, list[Any]] = {"stdout": [], "stderr": []}
    output_size = 0
    output_lock = threading.Lock()
    exceeded = threading.Event()
    reader_errors: list[BaseException] = []

    def drain(name: str, stream: Any) -> None:
        nonlocal output_size
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                size = (
                    len(chunk.encode(encoding, errors="replace"))
                    if isinstance(chunk, str)
                    else len(chunk)
                )
                with output_lock:
                    if output_size + size > max_output_bytes:
                        exceeded.set()
                        return
                    output_size += size
                    chunks[name].append(chunk)
        except BaseException as exc:
            reader_errors.append(exc)

    readers = [
        threading.Thread(
            target=drain,
            args=("stdout", process.stdout),
            daemon=True,
        ),
        threading.Thread(
            target=drain,
            args=("stderr", process.stderr),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    started = time.monotonic()
    failure: BaseException | None = None
    while True:
        if cancelled():
            failure = LeaseLostError(
                "Job lease was lost during subprocess"
            )
            break
        if exceeded.is_set():
            failure = ProcessOutputLimitError(
                f"Subprocess output exceeds {max_output_bytes} bytes"
            )
            break
        if reader_errors:
            failure = RuntimeError("Subprocess output reader failed")
            break
        elapsed = time.monotonic() - started
        if timeout is not None and elapsed >= float(timeout):
            failure = subprocess.TimeoutExpired(command, timeout)
            break
        remaining = (
            max(0.001, float(timeout) - elapsed)
            if timeout is not None
            else poll_seconds
        )
        try:
            process.wait(timeout=min(poll_seconds, remaining))
            break
        except subprocess.TimeoutExpired:
            continue

    if failure is not None:
        _terminate_process_tree(process, windows_job=windows_job)
    for reader in readers:
        reader.join(timeout=2)
    if any(reader.is_alive() for reader in readers):
        if process.poll() is None:
            _terminate_process_tree(process, windows_job=windows_job)
        raise RuntimeError("Subprocess output pipes did not close")
    if failure is None and exceeded.is_set():
        failure = ProcessOutputLimitError(
            f"Subprocess output exceeds {max_output_bytes} bytes"
        )
    if failure is not None:
        raise failure
    if reader_errors:
        raise RuntimeError("Subprocess output reader failed") from (
            reader_errors[0]
        )
    empty: Any = "" if text_mode else b""
    return (
        empty.join(chunks["stdout"]),
        empty.join(chunks["stderr"]),
    )


def run_cancellable_process(
    command: Any,
    *,
    cancelled: Callable[[], bool],
    poll_seconds: float = 0.1,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    if cancelled():
        raise LeaseLostError("Job lease was lost before subprocess start")
    check = bool(kwargs.pop("check", False))
    capture_output = bool(kwargs.pop("capture_output", False))
    timeout = kwargs.pop("timeout", None)
    input_value = kwargs.pop("input", None)
    max_output_bytes = kwargs.pop("max_output_bytes", None)
    if max_output_bytes is not None and input_value is not None:
        raise ValueError(
            "input is not supported with bounded subprocess output"
        )
    if capture_output:
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            raise ValueError("stdout/stderr cannot be used with capture_output")
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if _is_windows():
        kwargs["creationflags"] = (
            int(kwargs.get("creationflags", 0))
            | int(
                getattr(
                    subprocess,
                    "CREATE_NEW_PROCESS_GROUP",
                    0x00000200,
                )
            )
            | int(
                getattr(
                    subprocess,
                    "CREATE_SUSPENDED",
                    0x00000004,
                )
            )
        )
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    windows_job: _WindowsProcessJob | None = None
    if _is_windows():
        try:
            windows_job = _WindowsProcessJob.attach(process)
            windows_job.resume(process)
        except BaseException:
            if windows_job is not None:
                windows_job.terminate()
                windows_job.close()
            else:
                process.kill()
            process.wait()
            raise
    started = time.monotonic()
    try:
        if max_output_bytes is not None:
            stdout, stderr = _bounded_communicate(
                process,
                command,
                cancelled=cancelled,
                windows_job=windows_job,
                max_output_bytes=int(max_output_bytes),
                timeout=timeout,
                poll_seconds=poll_seconds,
                text_mode=bool(
                    kwargs.get("text")
                    or kwargs.get("universal_newlines")
                    or kwargs.get("encoding")
                ),
                encoding=str(kwargs.get("encoding") or "utf-8"),
            )
        else:
            while True:
                if cancelled():
                    _terminate_process_tree(
                        process,
                        windows_job=windows_job,
                    )
                    raise LeaseLostError(
                        "Job lease was lost during subprocess"
                    )
                remaining = None
                if timeout is not None:
                    remaining = float(timeout) - (
                        time.monotonic() - started
                    )
                    if remaining <= 0:
                        _terminate_process_tree(
                            process,
                            windows_job=windows_job,
                        )
                        raise subprocess.TimeoutExpired(
                            command,
                            timeout,
                        )
                try:
                    stdout, stderr = process.communicate(
                        input=input_value,
                        timeout=min(poll_seconds, remaining)
                        if remaining is not None
                        else poll_seconds,
                    )
                    break
                except subprocess.TimeoutExpired:
                    input_value = None
                    continue
    except BaseException:
        if process.poll() is None:
            _terminate_process_tree(
                process,
                windows_job=windows_job,
            )
        raise
    finally:
        if windows_job is not None:
            windows_job.close()
    result = subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout,
        stderr,
    )
    if check:
        result.check_returncode()
    return result
