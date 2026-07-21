from __future__ import annotations

import re
from typing import Any


FINAL_PUNCTUATION = re.compile(r"[。！？!?；;，,：:]$")


def audit_visual_plan(plan: dict[str, Any], pacing: dict[str, Any], subtitle_policy: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    segments = plan.get("segments", [])
    previous_end = 0
    total_ms = 0
    allowed = {"footage", "still", "entity_card", "explainer", "subtitle_only"}
    for index, segment in enumerate(segments):
        shot_id = segment.get("id", f"segment-{index + 1}")
        start = int(segment.get("start_ms", -1))
        end = int(segment.get("end_ms", -1))
        duration = end - start
        total_ms = max(total_ms, end)
        if start != previous_end or duration <= 0:
            errors.append({"code": "timing_continuity", "segment_id": shot_id})
        previous_end = end
        if duration > int(pacing["hard_max_duration_ms"]):
            errors.append({"code": "hard_max_duration", "segment_id": shot_id, "duration_ms": duration})
        elif duration > int(pacing["soft_max_duration_ms"]):
            warnings.append({"code": "soft_max_duration", "segment_id": shot_id, "duration_ms": duration})
        if duration > int(pacing["soft_max_duration_ms"]) and not segment.get("long_hold_reason"):
            errors.append({"code": "long_hold_reason_required", "segment_id": shot_id})
        text = str(segment.get("subtitle_text", "")).strip()
        if int(segment.get("subtitle_blocks", 1)) > int(pacing["max_subtitle_blocks"]):
            errors.append({"code": "subtitle_blocks", "segment_id": shot_id})
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        if chinese_chars > int(pacing["max_chinese_chars"]):
            errors.append({"code": "subtitle_characters", "segment_id": shot_id, "count": chinese_chars})
        if subtitle_policy.get("single_line") and ("\n" in text or "\r" in text):
            errors.append({"code": "subtitle_line_break", "segment_id": shot_id})
        if subtitle_policy.get("omit_sentence_final_punctuation") and FINAL_PUNCTUATION.search(text):
            errors.append({"code": "sentence_final_punctuation", "segment_id": shot_id})
        mode = segment.get("presentation_mode")
        if mode not in allowed:
            errors.append({"code": "presentation_mode", "segment_id": shot_id})
        if mode in {"footage", "still"} and not segment.get("slots"):
            errors.append({"code": "media_slots_required", "segment_id": shot_id})
        entity = segment.get("entity") or {}
        if mode == "entity_card" and not (entity.get("name") or entity.get("primary_label")):
            errors.append({"code": "entity_name_required", "segment_id": shot_id})
        if mode == "explainer" and not (segment.get("explainer") or {}).get("kind"):
            errors.append({"code": "explainer_kind_required", "segment_id": shot_id})
    shots_per_minute = round(len(segments) * 60000 / total_ms, 3) if total_ms > 0 else 0
    if shots_per_minute < float(pacing["min_shots_per_minute"]):
        errors.append({"code": "shot_density", "shots_per_minute": shots_per_minute})
    return {"ok": not errors, "errors": errors, "warnings": warnings, "metrics": {"shots": len(segments), "duration_ms": total_ms, "shots_per_minute": shots_per_minute}}
