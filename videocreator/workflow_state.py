STAGES = (
    "prepare",
    "prepare_confirm",
    "chat",
    "draft",
    "draft_confirm",
    "tts",
    "tts_confirm",
    "visual_plan",
    "visual_plan_confirm",
    "visual_assets",
    "visual_assets_confirm",
    "video_render",
    "video_render_confirm",
    "done",
)


def missing_stage_handlers(handlers: dict[str, object]) -> list[str]:
    return sorted(set(STAGES) - set(handlers))
