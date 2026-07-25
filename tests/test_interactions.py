import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from videocreator.interactions import (
    ConsoleInteractionPort,
    DurableInteractionPort,
    InteractionRequired,
)


class Context:
    def __init__(self, root: Path):
        self.run_id = "run-1"
        self.run_dir = root
        self.state = {"current_stage": "draft_confirm"}

    def save_state(self):
        (self.run_dir / "state.json").write_text(json.dumps(self.state), encoding="utf-8")


def test_durable_interaction_pauses_records_and_consumes_reply(tmp_path: Path):
    ctx = Context(tmp_path)
    port = DurableInteractionPort()

    with pytest.raises(InteractionRequired) as raised:
        port.ask(ctx, "draft-approval", "Approve?", "confirmation", ("y", "n"))

    interaction = raised.value.interaction
    assert ctx.state["pending_interaction"]["id"] == interaction["id"]
    port.submit(ctx, interaction["id"], "y")
    assert port.ask(ctx, "draft-approval", "Approve?", "confirmation", ("y", "n")) == "y"
    assert "pending_interaction" not in ctx.state
    events = [json.loads(line) for line in (tmp_path / "session/interactions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["asked", "answered"]


def test_stale_and_duplicate_responses_are_safe(tmp_path: Path):
    ctx = Context(tmp_path)
    port = DurableInteractionPort()
    with pytest.raises(InteractionRequired) as raised:
        port.ask(ctx, "draft-approval", "Approve?", "confirmation", ("y", "n"))

    with pytest.raises(ValueError, match="Stale interaction"):
        port.submit(ctx, "wrong", "y")
    assert port.submit(ctx, raised.value.interaction["id"], "y") is True
    assert port.submit(ctx, raised.value.interaction["id"], "y") is False


def test_answers_can_be_replayed_then_cleared_for_a_repeated_stage(tmp_path: Path):
    ctx = Context(tmp_path)
    port = DurableInteractionPort()
    with pytest.raises(InteractionRequired) as raised:
        port.ask(ctx, "approval", "Approve?", "confirmation", ("y", "n"))
    port.submit(ctx, raised.value.interaction["id"], "n")
    assert port.ask(ctx, "approval", "Approve?", "confirmation", ("y", "n")) == "n"
    port.clear(ctx, "approval")
    assert "submitted_interactions" not in ctx.state

    with pytest.raises(InteractionRequired) as repeated:
        port.ask(ctx, "approval", "Approve?", "confirmation", ("y", "n"))
    assert repeated.value.interaction["id"] != raised.value.interaction["id"]


def test_typed_payload_is_persisted_unchanged_and_handoff_is_explicit(tmp_path: Path):
    ctx = Context(tmp_path)
    port = DurableInteractionPort()
    payload = {
        "schema_version": 1,
        "query": {"subjects": ["economics"]},
        "response_schema": {"type": "object"},
    }

    with pytest.raises(InteractionRequired) as raised:
        port.ask(
            ctx,
            "bgm-online-candidates",
            "Find BGM",
            "bgm_candidates",
            payload=payload,
        )

    assert DurableInteractionPort.supports_agent_handoff is True
    assert ConsoleInteractionPort.supports_agent_handoff is False
    assert raised.value.interaction["payload"] == payload
    assert ctx.state["pending_interaction"]["payload"] == payload
    event = json.loads(
        (tmp_path / "session/interactions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert event["payload"] == payload
