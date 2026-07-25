import inspect

from types import SimpleNamespace

import main
from main import build_stage_handlers
from videocreator.workflow_state import STAGES, missing_stage_handlers


def test_main_dispatch_covers_every_declared_stage():
    handlers = build_stage_handlers()

    assert missing_stage_handlers(handlers) == []
    assert handlers["visual_plan"].__name__ == "run_visual_plan"
    assert handlers["subtitle_sync"].__name__ == "run_subtitle_sync"
    assert handlers["visual_assets"].__name__ == "run_visual_assets"
    assert handlers["video_render"].__name__ == "run_video_render"
    assert "not installed" not in inspect.getsource(handlers["video_render"])


def test_execute_boundary_honors_cancellation_before_next_stage(monkeypatch):
    called = []
    handlers = {stage: lambda ctx: called.append(stage) for stage in STAGES}
    monkeypatch.setattr(main, "build_stage_handlers", lambda: handlers)
    ctx = SimpleNamespace(
        state={"current_stage": "prepare"},
        should_cancel=lambda: True,
        set_stage=lambda stage, status, error=None: ctx.state.update(status=status),
    )

    outcome = main.execute_until_boundary(ctx)

    assert outcome.status == "cancelled"
    assert called == []
