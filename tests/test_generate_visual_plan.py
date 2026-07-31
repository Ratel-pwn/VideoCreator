import json

from scripts.generate_visual_plan import (
    build_planning_prompt,
    normalize_plan,
    plan_visual_scenes,
    split_long_segments,
)


PACING = {
    "target_duration_ms": [3500, 6500],
    "soft_max_duration_ms": 8000,
    "hard_max_duration_ms": 10000,
    "max_subtitle_blocks": 2,
    "max_chinese_chars": 48,
    "min_shots_per_minute": 9,
    "max_semantic_beats_per_scene": 1,
}
SUBTITLE_POLICY = {"single_line": True, "omit_sentence_final_punctuation": True}


def subtitle_segments():
    return [
        {
            "segment_id": "sub-001",
            "start": "00:00:00,000",
            "end": "00:00:06,000",
            "start_ms": 0,
            "end_ms": 6000,
            "duration_seconds": 6,
            "text": "第一幕",
        },
        {
            "segment_id": "sub-002",
            "start": "00:00:06,000",
            "end": "00:00:12,000",
            "start_ms": 6000,
            "end_ms": 12000,
            "duration_seconds": 6,
            "text": "第二幕",
        },
    ]


def test_short_subtitle_with_two_sentences_becomes_two_planning_units():
    source = [{
        "segment_id": "sub-001",
        "start": "00:00:00,000",
        "end": "00:00:05,000",
        "start_ms": 0,
        "end_ms": 5000,
        "duration_seconds": 5,
        "text": "工厂开始扩张。工人随后走上街头。",
    }]

    refined = split_long_segments(source)

    assert [item["text"] for item in refined] == ["工厂开始扩张。", "工人随后走上街头。"]
    assert refined[0]["end_ms"] == refined[1]["start_ms"]


def test_normalized_scenes_cover_silent_gaps_between_subtitles():
    segments = subtitle_segments()
    segments[0]["end_ms"] = 5500
    segments[0]["end"] = "00:00:05,500"
    plan = {"scenes": [
        {"subtitle_segment_ids": ["sub-001"], "presentation_mode": "subtitle_only"},
        {"subtitle_segment_ids": ["sub-002"], "presentation_mode": "subtitle_only"},
    ]}

    normalized = normalize_plan(segments, plan, "topic", "general")

    assert normalized["segments"][0]["end_ms"] == 6000
    assert normalized["segments"][0]["end"] == "00:00:06,000"


def test_prompt_contains_effective_template_constraints_and_minimum_scene_count():
    prompt = build_planning_prompt(
        subtitle_segments(),
        topic="资本主义的潘多拉魔盒",
        category="humanities",
        draft_text="第一幕。第二幕。",
        pacing=PACING,
        subtitle_policy=SUBTITLE_POLICY,
    )

    assert prompt["planning_contract"]["minimum_scene_count"] == 2
    assert prompt["planning_contract"]["hard_max_duration_ms"] == 10000
    assert prompt["planning_contract"]["max_subtitle_blocks"] == 2
    assert prompt["planning_contract"]["max_semantic_beats_per_scene"] == 1
    assert prompt["planning_contract"]["subtitle_policy"] == SUBTITLE_POLICY


def test_failed_plan_is_replanned_with_audit_feedback():
    responses = iter([
        {
            "scenes": [{
                "subtitle_segment_ids": ["sub-001", "sub-002"],
                "presentation_mode": "footage",
                "slots": [{"role": "primary", "required_type": "video"}],
            }]
        },
        {
            "scenes": [
                {
                    "subtitle_segment_ids": ["sub-001"],
                    "presentation_mode": "footage",
                    "slots": [{"role": "primary", "required_type": "video"}],
                },
                {
                    "subtitle_segment_ids": ["sub-002"],
                    "presentation_mode": "still",
                    "slots": [{"role": "primary", "required_type": "image"}],
                },
            ]
        },
    ])
    prompts = []

    def invoke(messages):
        prompts.append(json.loads(messages[-1]["content"]))
        return json.dumps(next(responses), ensure_ascii=False)

    plan = plan_visual_scenes(
        subtitle_segments(),
        topic="资本主义的潘多拉魔盒",
        category="humanities",
        draft_text="第一幕。第二幕。",
        skill_text="规划视觉",
        pacing=PACING,
        subtitle_policy=SUBTITLE_POLICY,
        invoke=invoke,
        max_attempts=2,
    )

    assert plan["segment_count"] == 2
    assert len(prompts) == 2
    assert {item["code"] for item in prompts[1]["audit_feedback"]["errors"]} >= {
        "hard_max_duration",
        "shot_density",
    }
