import pytest

from videocreator.subtitle_repair import choose_repair, run_repair


def audit_with(code: str, target: str = "segment-0001") -> dict:
    return {
        "status": "failed",
        "inputs": {
            "audio_sha256": "audio",
            "srt_sha256": "srt",
            "alignment_report_sha256": "report",
        },
        "findings": [{"code": code, "target": target}],
    }


@pytest.mark.parametrize(
    ("code", "action"),
    [
        ("artifact_hash_mismatch", "rebuild_alignment"),
        ("audio_decode_failure", "reassemble_audio"),
        ("segment_missing", "regenerate_segment"),
        ("text_content_mismatch", "recognize_window"),
        ("asr_low_confidence", "recognize_window"),
        ("subtitle_boundary_drift", "realign_range"),
        ("subtitle_overlap", "realign_range"),
        ("approved_text_missing", "rebuild_alignment"),
        ("approved_text_hash_mismatch", "rebuild_alignment"),
        ("approved_text_mismatch", "rebuild_alignment"),
        ("alignment_evidence_missing", "rebuild_alignment"),
        ("unresolved_span_too_long", "realign_range"),
    ],
)
def test_choose_repair_maps_diagnosis(code, action):
    assert choose_repair(audit_with(code), {})["action"] == action


def test_choose_repair_does_not_repeat_unchanged_action():
    audit = audit_with("subtitle_boundary_drift", "caption-3")
    first = choose_repair(audit, {})
    second = choose_repair(audit, {first["fingerprint"]: first})

    assert choose_repair(
        audit,
        {
            first["fingerprint"]: first,
            second["fingerprint"]: second,
        },
    ) is None


def test_choose_repair_escalates_decode_failure_after_reassembly():
    audit = audit_with("audio_decode_failure")
    first = choose_repair(audit, {})

    second = choose_repair(audit, {first["fingerprint"]: first})

    assert first["action"] == "reassemble_audio"
    assert second["action"] == "regenerate_segment"


def test_run_repair_dispatches_only_selected_action():
    calls = []
    action = choose_repair(audit_with("segment_missing"), {})

    result = run_repair(
        action,
        handlers={"regenerate_segment": lambda target: calls.append(target) or "ok"},
    )

    assert calls == ["segment-0001"]
    assert result["status"] == "completed"
