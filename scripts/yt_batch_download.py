#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


INVALID_CHARS = '<>:"/\\|?*'
DEFAULT_BROWSER_PROFILE = Path(r"C:\Users\24485\AppData\Local\CentBrowser\User Data\Default")


def sanitize_name(name: str) -> str:
    cleaned = name.strip()
    for ch in INVALID_CHARS:
        cleaned = cleaned.replace(ch, "_")
    cleaned = cleaned.rstrip(" .")
    if not cleaned:
        raise ValueError("name is empty after sanitization")
    return cleaned


def parse_line(line: str, line_no: int) -> tuple[str, str] | None:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None
    if "," not in raw:
        raise ValueError(f"line {line_no}: expected 'URL, Name'")
    url, name = raw.split(",", 1)
    url = url.strip()
    name = name.strip()
    if not url or not name:
        raise ValueError(f"line {line_no}: URL or Name is empty")
    return url, sanitize_name(name)


def build_args(
    url: str,
    safe_name: str,
    output_dir: Path,
    fmt: str,
    js_runtime: str,
    remote_components: str,
    no_overwrite: bool,
    cookies_file: Path | None,
    cookies_from_browser: str | None,
    user_agent: str | None,
) -> list[str]:
    cmd = ["yt-dlp"]
    if user_agent:
        cmd += ["--user-agent", user_agent]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    elif cookies_file:
        cmd += ["--cookies", str(cookies_file)]
    cmd += [
        "--no-playlist",
        "--js-runtimes",
        js_runtime,
        "--remote-components",
        remote_components,
        "-f",
        fmt,
        "-P",
        str(output_dir),
        "-o",
        f"{safe_name}.%(ext)s",
    ]
    if no_overwrite:
        cmd.insert(1, "--no-overwrites")
    cmd.append(url)
    return cmd


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"failed to parse config file: {path} ({exc})") from exc


def main() -> int:
    default_list_file = Path(__file__).resolve().with_name("yt-list.example.txt")
    default_config_file = Path(__file__).resolve().with_name("yt_batch_download.config.json")
    parser = argparse.ArgumentParser(
        description="Batch download YouTube videos from lines in format: URL, Name"
    )
    parser.add_argument(
        "--config",
        default=default_config_file,
        type=Path,
        help=f"JSON config file (default: {default_config_file})",
    )
    parser.add_argument(
        "--list-file",
        default=None,
        type=Path,
        help=f"Input txt file (default from config or {default_list_file})",
    )
    parser.add_argument(
        "--output-dir", default=None, type=Path, help="Output directory"
    )
    parser.add_argument(
        "--cookies-file",
        default=None,
        type=Path,
        help="cookies.txt path",
    )
    parser.add_argument(
        "--cookies-from-browser",
        default=None,
        help='Example: "chromium:C:\\Users\\24485\\AppData\\Local\\CentBrowser\\User Data\\Default"',
    )
    parser.add_argument("--format", default=None, help="yt-dlp format selector")
    parser.add_argument("--js-runtime", default=None, help='Example: "node" or "node:D:\\path\\node.exe"')
    parser.add_argument("--remote-components", default=None)
    parser.add_argument("--user-agent", default=None)
    parser.add_argument("--force", action="store_true", default=None, help="Overwrite existing files")
    parser.add_argument("--dry-run", action="store_true", default=None, help="Print commands only")
    cli = parser.parse_args()

    try:
        cfg = load_config(cli.config)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    list_file = Path(cli.list_file or cfg.get("list_file") or default_list_file)
    output_dir = Path(cli.output_dir or cfg.get("output_dir") or r"E:\Media\Videos\chaos")
    cookies_file = Path(cli.cookies_file or cfg.get("cookies_file") or r"C:\Users\24485\yt-cookies.txt")
    cookies_from_browser = cli.cookies_from_browser or cfg.get("cookies_from_browser")
    fmt = cli.format or cfg.get("format") or "bv*+ba/b"
    js_runtime = cli.js_runtime or cfg.get("js_runtime") or "node"
    remote_components = cli.remote_components or cfg.get("remote_components") or "ejs:github"
    user_agent = cli.user_agent or cfg.get("user_agent")
    force = cli.force if cli.force is not None else bool(cfg.get("force", False))
    dry_run = cli.dry_run if cli.dry_run is not None else bool(cfg.get("dry_run", False))

    if not list_file.exists():
        print(f"List file not found: {list_file}", file=sys.stderr)
        return 2

    # Prefer CentBrowser profile automatically when available.
    if not cookies_from_browser and DEFAULT_BROWSER_PROFILE.exists():
        cookies_from_browser = f"chromium:{DEFAULT_BROWSER_PROFILE}"
        print(f"[INFO] Auto cookies source: {cookies_from_browser}")

    if not cookies_from_browser and not cookies_file.exists():
        print(
            f"Cookies file not found: {cookies_file} (or pass --cookies-from-browser)",
            file=sys.stderr,
        )
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)

    ok = fail = skip = 0
    lines = list_file.read_text(encoding="utf-8").splitlines()

    for idx, line in enumerate(lines, start=1):
        try:
            parsed = parse_line(line, idx)
        except ValueError as exc:
            print(f"[WARN] {exc}")
            fail += 1
            continue

        if parsed is None:
            skip += 1
            continue

        url, safe_name = parsed
        cmd = build_args(
            url=url,
            safe_name=safe_name,
            output_dir=output_dir,
            fmt=fmt,
            js_runtime=js_runtime,
            remote_components=remote_components,
            no_overwrite=not force,
            cookies_file=cookies_file,
            cookies_from_browser=cookies_from_browser,
            user_agent=user_agent,
        )

        if dry_run:
            print("[DRY-RUN]", " ".join(cmd))
            ok += 1
            continue

        print(f"[INFO] Downloading line {idx}: {safe_name}")
        proc = subprocess.run(cmd)
        if proc.returncode == 0:
            ok += 1
            print(f"[OK] {safe_name}")
        else:
            fail += 1
            print(f"[FAIL] line {idx}: {url}")

    print(f"\nFinished: success={ok} fail={fail} skip={skip}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
