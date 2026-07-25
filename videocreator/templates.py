from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class TemplateError(ValueError):
    pass


ALLOWED_CAPABILITIES = {
    "prepare", "writing", "tts", "subtitles", "visual_planning",
    "asset_collection", "final_assembly", "bgm",
}
REQUIRED_PATHS = {"prepare", "writing", "visual_planning", "pacing", "subtitle", "composition"}
OPTIONAL_PATHS = {"bgm"}
EXECUTABLE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".sh", ".ps1", ".bat", ".cmd"}


@dataclass(frozen=True)
class TemplateDefinition:
    id: str
    version: int
    root: Path
    capabilities: tuple[str, ...]
    paths: dict[str, Path]
    raw: dict[str, Any]


@dataclass(frozen=True)
class LibraryFile:
    path: Path
    sha256: str


@dataclass(frozen=True)
class LibrarySelection:
    resource_type: str
    level: str
    root: Path | None
    files: tuple[LibraryFile, ...]


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def load_template(templates_root: Path, template_id: str) -> TemplateDefinition:
    root = (templates_root / template_id).resolve()
    config_path = root / "template.json"
    if not config_path.is_file():
        raise TemplateError(f"template not found: {template_id}")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if raw.get("id") != template_id:
        raise TemplateError(f"template id mismatch: {template_id}")
    capabilities = tuple(raw.get("capabilities", []))
    unknown = set(capabilities) - ALLOWED_CAPABILITIES
    if unknown:
        raise TemplateError(f"unknown capabilities: {sorted(unknown)}")
    declared = raw.get("paths", {})
    missing = REQUIRED_PATHS - set(declared)
    if missing:
        raise TemplateError(f"missing template paths: {sorted(missing)}")
    unknown_paths = set(declared) - REQUIRED_PATHS - OPTIONAL_PATHS
    if unknown_paths:
        raise TemplateError(f"unknown template paths: {sorted(unknown_paths)}")
    paths: dict[str, Path] = {}
    for name, relative in declared.items():
        path = (root / relative).resolve()
        if not _inside(root, path):
            raise TemplateError(f"{name} must stay inside template: {relative}")
        if not path.is_file():
            raise TemplateError(f"missing template file: {relative}")
        paths[name] = path
    executable = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in EXECUTABLE_SUFFIXES]
    if executable:
        raise TemplateError(f"template contains executable files: {[p.name for p in executable]}")
    return TemplateDefinition(template_id, int(raw.get("version", 1)), root, capabilities, paths, raw)


def discover_templates(templates_root: Path) -> dict[str, TemplateDefinition]:
    found: dict[str, TemplateDefinition] = {}
    if not templates_root.is_dir():
        return found
    for directory in sorted(p for p in templates_root.iterdir() if p.is_dir()):
        definition = load_template(templates_root, directory.name)
        if definition.id in found:
            raise TemplateError(f"duplicate template id: {definition.id}")
        found[definition.id] = definition
    return found


def resolve_library(repo_root: Path, project_root: Path, template: TemplateDefinition, resource_type: str) -> LibrarySelection:
    candidates = (
        ("project", project_root / "library" / resource_type),
        ("template", template.root / "library" / resource_type),
        ("global", repo_root / "library" / resource_type / "default"),
    )
    for level, root in candidates:
        files = tuple(sorted(
            p for p in root.rglob("*")
            if p.is_file() and p.name.lower() != "readme.md" and not p.name.startswith(".")
        )) if root.is_dir() else ()
        if files:
            return LibrarySelection(resource_type, level, root, tuple(LibraryFile(p, _hash(p)) for p in files))
    return LibrarySelection(resource_type, "none", None, ())


def snapshot_template(template: TemplateDefinition) -> dict[str, Any]:
    files = {"template.json": _hash(template.root / "template.json")}
    for name, path in sorted(template.paths.items()):
        files[path.relative_to(template.root).as_posix()] = _hash(path)
    return {"id": template.id, "version": template.version, "capabilities": list(template.capabilities), "files": files}
