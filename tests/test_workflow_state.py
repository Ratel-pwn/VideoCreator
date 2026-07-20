from videocreator.workflow_state import STAGES, missing_stage_handlers


def test_every_declared_stage_requires_a_handler():
    handlers = {stage: object() for stage in STAGES if stage != "visual_assets"}

    assert missing_stage_handlers(handlers) == ["visual_assets"]
