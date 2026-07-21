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
    if int(visual_plan.get("schema_version", 1)) == 2:
        requests = []
        for scene in visual_plan.get("segments", []):
            for slot in scene.get("slots", []):
                request_id = f"{scene['segment_id']}:{slot['role']}"
                requests.append(
                    {
                        "request_id": request_id,
                        "scene_id": scene["segment_id"],
                        "role": slot["role"],
                        "start_ms": scene["start_ms"],
                        "end_ms": scene["end_ms"],
                        "narration": scene.get("text", ""),
                        "visual_brief": scene.get("brief", ""),
                        "required_asset_type": slot["required_type"],
                        "strategy": "web_curated",
                        "search_queries": slot.get("search_queries", []),
                    }
                )
        return {
            "schema_version": 2,
            "topic": visual_plan.get("topic", ""),
            "request_count": len(requests),
            "requests": requests,
        }
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
    if int(visual_plan.get("schema_version", 1)) == 2:
        return _audit_v2_manifest(project_root, visual_plan, manifest, probe_media)
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


def _audit_v2_manifest(
    project_root: Path,
    visual_plan: dict[str, Any],
    manifest: dict[str, Any],
    probe_media: bool,
) -> AssetAuditResult:
    result = AssetAuditResult()
    if int(manifest.get("schema_version", 1)) != 2:
        result.errors.append("schema_version mismatch: visual plan v2 requires manifest v2")
        return result

    required = {
        f"{scene['segment_id']}:{slot['role']}": (scene, slot)
        for scene in visual_plan.get("segments", [])
        for slot in scene.get("slots", [])
    }
    records = manifest.get("assets", [])
    by_request: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_request.setdefault(str(record.get("request_id", "")), []).append(record)

    for request_id, (scene, slot) in required.items():
        matches = by_request.get(request_id, [])
        if len(matches) != 1:
            result.errors.append(f"{request_id}: expected exactly one asset record")
            continue
        record = matches[0]
        expected_type = slot["required_type"]
        actual_type = str(record.get("asset_type", ""))
        if actual_type != expected_type:
            result.errors.append(f"{request_id}: expected {expected_type}, got {actual_type}")

        path = project_root / str(record.get("local_path", ""))
        if not _inside(project_root, path):
            result.errors.append(f"{request_id}: local_path escapes project root")
        elif not path.is_file():
            result.errors.append(f"{request_id}: asset file does not exist")

        for field_name in (
            "source_page_url",
            "provider",
            "license",
            "credit",
            "retrieved_at",
            "rights_status",
            "rights_note",
        ):
            if not str(record.get(field_name, "")).strip():
                result.errors.append(f"{request_id}: missing {field_name}")
        rights_status = record.get("rights_status")
        if rights_status in {"unknown", "restricted"}:
            result.warnings.append(f"{request_id}: rights_status is {rights_status}")
        if record.get("review_status") != "approved":
            result.errors.append(f"{request_id}: review_status must be approved")

        if probe_media and _inside(project_root, path) and path.is_file():
            metadata = _probe_media(path)
            if metadata.kind != expected_type:
                result.errors.append(
                    f"{request_id}: required {expected_type} does not match {metadata.kind}"
                )
            if expected_type == "video" and (
                (metadata.width or 0) < 1280 or (metadata.height or 0) < 720
            ):
                result.errors.append(f"{request_id}: video resolution must be at least 1280x720")

        if not any(error.startswith(f"{request_id}:") for error in result.errors):
            result.approved_scene_ids.append(request_id)

    if visual_plan.get("segments"):
        first = visual_plan["segments"][0]
        if first.get("presentation_mode") != "footage":
            result.errors.append("scene-001: opening scene must use footage")
    return result
