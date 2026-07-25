from __future__ import annotations

import hashlib
import json
from typing import Any, Callable


REPAIR_BY_DIAGNOSIS = {
    "artifact_hash_mismatch": ["rebuild_alignment", "reassemble_audio"],
    "audio_decode_failure": ["reassemble_audio", "regenerate_segment"],
    "segment_missing": ["regenerate_segment"],
    "segment_duplicate": ["reassemble_audio"],
    "segment_order_mismatch": ["reassemble_audio"],
    "unexpected_silence": ["regenerate_segment"],
    "speech_truncated": ["regenerate_segment"],
    "asr_low_confidence": ["recognize_window"],
    "text_content_mismatch": ["recognize_window", "regenerate_segment"],
    "subtitle_boundary_drift": ["realign_range", "recognize_window"],
    "subtitle_overlap": ["realign_range"],
    "audit_threshold_false_positive": ["recognize_window"],
}


def _action_fingerprint(
    action: str,
    target: str,
    inputs: dict[str, Any],
) -> str:
    value = json.dumps(
        {
            "action": action,
            "target": target,
            "inputs": inputs,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def choose_repair(
    audit: dict[str, Any],
    history: dict[str, Any],
) -> dict[str, Any] | None:
    if audit.get("status") == "passed":
        return None
    inputs = audit.get("inputs") or {}
    for finding in audit.get("findings") or []:
        code = str(finding.get("code", ""))
        actions = REPAIR_BY_DIAGNOSIS.get(code)
        if not actions:
            continue
        target = str(finding.get("target") or "narration")
        for action in actions:
            fingerprint = _action_fingerprint(action, target, inputs)
            if fingerprint in history:
                continue
            return {
                "action": action,
                "diagnosis": code,
                "target": target,
                "fingerprint": fingerprint,
                "input_hashes": inputs,
            }
    return None


def run_repair(
    action: dict[str, Any],
    *,
    handlers: dict[str, Callable[[str], Any]],
) -> dict[str, Any]:
    action_name = str(action["action"])
    handler = handlers.get(action_name)
    if handler is None:
        return {
            **action,
            "status": "blocked",
            "error": f"No repair handler is installed for {action_name}",
        }
    try:
        output = handler(str(action["target"]))
    except Exception as exc:
        return {
            **action,
            "status": "failed",
            "error": str(exc),
        }
    return {
        **action,
        "status": "completed",
        "result": output,
    }
