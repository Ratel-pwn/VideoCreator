from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable


class LeaseLostError(RuntimeError):
    pass


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
    def __init__(self, kernel32: Any, handle: Any):
        self.kernel32 = kernel32
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
        return cls(kernel32, handle)

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
    if os.name == "nt":
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
    if capture_output:
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            raise ValueError("stdout/stderr cannot be used with capture_output")
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if os.name == "nt":
        kwargs["creationflags"] = (
            int(kwargs.get("creationflags", 0))
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    windows_job: _WindowsProcessJob | None = None
    if os.name == "nt":
        try:
            windows_job = _WindowsProcessJob.attach(process)
        except OSError:
            process.kill()
            process.wait()
            raise
    started = time.monotonic()
    try:
        while True:
            if cancelled():
                _terminate_process_tree(
                    process,
                    windows_job=windows_job,
                )
                raise LeaseLostError("Job lease was lost during subprocess")
            remaining = None
            if timeout is not None:
                remaining = float(timeout) - (time.monotonic() - started)
                if remaining <= 0:
                    _terminate_process_tree(
                        process,
                        windows_job=windows_job,
                    )
                    raise subprocess.TimeoutExpired(command, timeout)
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
