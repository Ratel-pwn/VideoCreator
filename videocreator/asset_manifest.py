from __future__ import annotations

from pathlib import Path
from typing import Any

from .media import probe_media as _probe_media
from .models import AssetAuditResult, AssetRecord


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def create_asset_requests(visual_plan: dict[str, Any]) -> dict[str, Any]:
    requests = []
    for scene in visual_plan.get("segments", []):
        if scene.get("material_type") == "subtitle_only":
            continue
        preferred = scene.get("material_type", "image")
        requests.append(
            {
                "scene_id": scene["segment_id"],
                "start_ms": scene["start_ms"],
                "end_ms": scene["end_ms"],
                "narration": scene.get("text", ""),
                "visual_brief": scene.get("brief", ""),
                "preferred_asset_type": preferred,
                "strategy": "web_curated",
                "search_queries": (scene.get("search_queries") or {}).get(preferred, []),
                "acceptance_criteria": [
                    "Matches the spoken point of this scene",
                    "Can fill a 16:9 frame without visible watermarks",
                    "Source page and usage basis can be recorded",
                ],
                "rejection_criteria": [
                    "Unrelated decorative imagery",
                    "Visible loading state or watermark",
                    "Missing source page or usage basis",
                ],
            }
        )
    return {
        "topic": visual_plan.get("topic", ""),
        "request_count": len(requests),
        "requests": requests,
    }


def audit_asset_manifest(
    project_root: Path,
    visual_plan: dict[str, Any],
    manifest: dict[str, Any],
    *,
    probe_media: bool = True,
) -> AssetAuditResult:
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

        if probe_media and _inside(project_root, path) and path.is_file():
            metadata = _probe_media(path)
            if metadata.kind != record.asset_type:
                result.errors.append(
                    f"{scene_id}: manifest type {record.asset_type} does not match {metadata.kind}"
                )
            if record.asset_type == "video" and (
                (metadata.width or 0) < 1280 or (metadata.height or 0) < 720
            ):
                result.errors.append(
                    f"{scene_id}: video resolution must be at least 1280x720"
                )
            if record.asset_type == "image" and max(
                metadata.width or 0, metadata.height or 0
            ) < 1280:
                result.warnings.append(
                    f"{scene_id}: image long edge is below 1280 pixels"
                )

        if not any(error.startswith(f"{scene_id}:") for error in result.errors):
            result.approved_scene_ids.append(scene_id)

    return result
