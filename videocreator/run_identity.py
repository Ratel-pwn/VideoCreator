from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath


_INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "clock$",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def validate_run_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("run_id must be a string")
    if not value or value != value.strip() or len(value) > 128:
        raise ValueError("run_id must be a non-empty safe path component")
    if value in {".", ".."} or value.endswith((".", " ")):
        raise ValueError("run_id must be a non-empty safe path component")
    windows_path = PureWindowsPath(value)
    if (
        Path(value).is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or windows_path.root
        or _INVALID_WINDOWS_CHARS.search(value)
    ):
        raise ValueError("run_id must be one safe path component")
    stem = value.split(".", 1)[0].casefold()
    if stem in _WINDOWS_RESERVED_NAMES:
        raise ValueError("run_id uses a reserved Windows name")
    return value


def resolve_run_dir(project_root: Path, run_id: str) -> Path:
    safe_id = validate_run_id(run_id)
    runs_root = (Path(project_root) / "runs").resolve()
    run_dir = (runs_root / safe_id).resolve()
    try:
        run_dir.relative_to(runs_root)
    except ValueError as exc:
        raise ValueError("run_id escapes the project runs directory") from exc
    return run_dir
