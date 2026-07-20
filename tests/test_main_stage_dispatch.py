from main import build_stage_handlers
from videocreator.workflow_state import STAGES, missing_stage_handlers


def test_main_dispatch_covers_every_declared_stage():
    handlers = build_stage_handlers()

    assert missing_stage_handlers(handlers) == []
    assert handlers["visual_plan"].__name__ == "run_visual_plan"
    assert handlers["visual_assets"].__name__ == "run_visual_assets"
    assert handlers["video_render"].__name__ == "run_video_render"
