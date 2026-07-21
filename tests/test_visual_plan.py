from videocreator.visual_plan import audit_visual_plan


PACING = {
    "target_duration_ms": [3500, 6500], "soft_max_duration_ms": 8000,
    "hard_max_duration_ms": 10000, "max_subtitle_blocks": 2,
    "max_chinese_chars": 48, "min_shots_per_minute": 9,
}


def test_audit_rejects_sparse_plan_and_punctuation():
    plan = {"version": 2, "segments": [{
        "id": "s1", "start_ms": 0, "end_ms": 12000,
        "subtitle_text": "这是一个过长的镜头。", "subtitle_blocks": 1,
        "presentation_mode": "footage", "slots": [{"media_type": "image"}],
    }]}
    result = audit_visual_plan(plan, PACING, {"single_line": True, "omit_sentence_final_punctuation": True})
    codes = {item["code"] for item in result["errors"]}
    assert {"hard_max_duration", "long_hold_reason_required", "sentence_final_punctuation", "shot_density"} <= codes
    assert not result["ok"]


def test_audit_accepts_dense_entity_and_explainer_plan():
    plan = {"version": 2, "segments": [
        {"id": "s1", "start_ms": 0, "end_ms": 5000, "subtitle_text": "亚当斯密提出分工", "subtitle_blocks": 1, "presentation_mode": "entity_card", "entity": {"name": "亚当·斯密"}},
        {"id": "s2", "start_ms": 5000, "end_ms": 10000, "subtitle_text": "交换形成循环", "subtitle_blocks": 1, "presentation_mode": "explainer", "explainer": {"kind": "process", "items": ["生产", "交换"]}},
    ]}
    result = audit_visual_plan(plan, PACING, {"single_line": True, "omit_sentence_final_punctuation": True})
    assert result["ok"]
    assert result["metrics"]["shots_per_minute"] == 12
