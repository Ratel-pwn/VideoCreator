from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import AssetAuditResult, AssetRecord


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def audit_asset_manifest(
    project_root: Path,
    visual_plan: dict[str, Any],
    manifest: dict[str, Any],
    *,
    probe_media: bool = True,
) -> AssetAuditResult:
    del probe_media
    result = AssetAuditResult()
    required = {
        segment["segment_id"]
        for segment in visual_plan.get("segments", [])
        if segment.get("material_type") != "subtitle_only"
    }
    records = [AssetRecord.from_dict(value) for value in manifest.get("segments", [])]
    by_scene: dict[str, list[AssetRecord]] = {}
    for record in records:
        by_scene.setdefault(record.scene_id, []).append(record)

    for scene_id in sorted(required):
        matches = by_scene.get(scene_id, [])
        if len(matches) != 1:
            result.errors.append(f"{scene_id}: expected exactly one asset record")
            continue

        record = matches[0]
        path = project_root / record.local_path
        if not _inside(project_root, path):
            result.errors.append(f"{scene_id}: local_path escapes project root")
        elif not path.is_file():
            result.errors.append(f"{scene_id}: asset file does not exist")

        for field_name in ("source_page_url", "provider", "license", "retrieved_at"):
            if not getattr(record, field_name):
                result.errors.append(f"{scene_id}: missing {field_name}")

        if record.review_status != "approved":
            result.errors.append(f"{scene_id}: review_status must be approved")

        if not any(error.startswith(f"{scene_id}:") for error in result.errors):
            result.approved_scene_ids.append(scene_id)

    return result
