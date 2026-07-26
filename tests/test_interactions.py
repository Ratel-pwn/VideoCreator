import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from videocreator.interactions import (
    ConsoleInteractionPort,
    DurableInteractionPort,
    InteractionRequired,
    interaction_fingerprint,
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


def test_changed_kind_or_payload_cannot_replay_stale_answer(tmp_path: Path):
    ctx = Context(tmp_path)
    port = DurableInteractionPort()
    first_payload = {"schema_version": 1, "query": {"subjects": ["economics"]}}
    second_payload = {"schema_version": 1, "query": {"subjects": ["biology"]}}

    with pytest.raises(InteractionRequired) as raised:
        port.ask(
            ctx,
            "bgm-online-candidates",
            "Find BGM",
            "bgm_candidates",
            payload=first_payload,
        )
    first = raised.value.interaction
    answer = json.dumps({"candidates": []})
    port.submit(
        ctx,
        first["id"],
        answer,
        fingerprint=first["fingerprint"],
    )
    assert port.ask(
        ctx,
        "bgm-online-candidates",
        "Find BGM",
        "bgm_candidates",
        payload=first_payload,
    ) == answer

    with pytest.raises(InteractionRequired) as changed:
        port.ask(
            ctx,
            "bgm-online-candidates",
            "Find BGM",
            "bgm_candidates",
            payload=second_payload,
        )

    assert changed.value.interaction["id"] != first["id"]
    assert changed.value.interaction["fingerprint"] == interaction_fingerprint(
        "bgm_candidates",
        second_payload,
    )
    assert "response" not in changed.value.interaction
    assert "submitted_interactions" not in ctx.state
    assert "consumed_interactions" not in ctx.state


def test_submit_rejects_mismatched_interaction_fingerprint(tmp_path: Path):
    ctx = Context(tmp_path)
    port = DurableInteractionPort()
    payload = {"query": {"subjects": ["economics"]}}
    with pytest.raises(InteractionRequired) as raised:
        port.ask(
            ctx,
            "bgm-online-candidates",
            "Find BGM",
            "bgm_candidates",
            payload=payload,
        )

    with pytest.raises(ValueError, match="fingerprint"):
        port.submit(
            ctx,
            raised.value.interaction["id"],
            "answer",
            fingerprint="stale",
        )


def test_answer_audit_contains_only_bounded_hash_not_raw_response(tmp_path: Path):
    ctx = Context(tmp_path)
    port = DurableInteractionPort()
    with pytest.raises(InteractionRequired) as raised:
        port.ask(ctx, "bgm", "Find BGM", "bgm_candidates", payload={"query": "x"})
    secret = json.dumps(
        {
            "candidates": [
                {
                    "title": "TOP-SECRET",
                    "source_page_url": "https://example.test/source",
                    "download_url": "https://cdn.example/a.mp3",
                    "provider": "agent",
                }
            ]
        }
    )

    port.submit(ctx, raised.value.interaction["id"], secret)

    audit = (tmp_path / "session/interactions.jsonl").read_text(encoding="utf-8")
    answered = json.loads(audit.splitlines()[-1])
    assert "TOP-SECRET" not in audit
    assert "download_url" not in audit
    assert "response" not in answered
    assert len(answered["response_sha256"]) == 64
    assert answered["response_bytes"] == len(secret.encode("utf-8"))


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "https://cdn.example/a.mp3?token=TOP-SECRET",
        "https://user:TOP-SECRET@cdn.example/a.mp3",
        "https://cdn.example/a.mp3#TOP-SECRET",
    ],
)
def test_agent_candidate_urls_are_rejected_before_any_durable_write(
    tmp_path: Path,
    unsafe_url: str,
):
    ctx = Context(tmp_path)
    port = DurableInteractionPort()
    payload = {
        "limits": {
            "max_response_bytes": 200_000,
            "max_candidates": 20,
        },
        "response_schema": {
            "properties": {
                "candidates": {
                    "maxItems": 20,
                }
            }
        },
    }
    with pytest.raises(InteractionRequired) as raised:
        port.ask(
            ctx,
            "bgm",
            "Find BGM",
            "bgm_candidates",
            payload=payload,
        )
    response = json.dumps(
        {
            "candidates": [
                {
                    "title": "Unsafe",
                    "source_page_url": "https://example.test/source",
                    "download_url": unsafe_url,
                    "provider": "agent",
                }
            ]
        }
    )
    state_before = (tmp_path / "state.json").read_bytes()
    audit_before = (tmp_path / "session/interactions.jsonl").read_bytes()

    with pytest.raises(ValueError, match="query|userinfo|fragment"):
        port.submit(ctx, raised.value.interaction["id"], response)

    assert (tmp_path / "state.json").read_bytes() == state_before
    assert (tmp_path / "session/interactions.jsonl").read_bytes() == audit_before
    durable = state_before + audit_before
    assert b"TOP-SECRET" not in durable


def test_agent_url_validation_cannot_be_bypassed_by_missing_payload(
    tmp_path: Path,
):
    ctx = Context(tmp_path)
    port = DurableInteractionPort()
    with pytest.raises(InteractionRequired) as raised:
        port.ask(
            ctx,
            "bgm",
            "Find BGM",
            "bgm_candidates",
        )
    unsafe = json.dumps(
        {
            "candidates": [
                {
                    "title": "Unsafe",
                    "source_page_url": "https://example.test/source",
                    "download_url": (
                        "https://cdn.example/a.mp3?token=TOP-SECRET"
                    ),
                    "provider": "agent",
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="query"):
        port.submit(ctx, raised.value.interaction["id"], unsafe)

    assert "TOP-SECRET" not in json.dumps(ctx.state)


@pytest.mark.parametrize(
    "response, message",
    [
        (json.dumps({"candidates": [{"id": "a"}, {"id": "b"}]}), "candidates"),
        (json.dumps({"candidates": [], "padding": "x" * 300}), "bytes"),
    ],
)
def test_bgm_submission_bounds_apply_before_raw_response_persistence(
    tmp_path: Path,
    response: str,
    message: str,
):
    ctx = Context(tmp_path)
    port = DurableInteractionPort()
    payload = {
        "schema_version": 1,
        "limits": {"max_response_bytes": 100},
        "response_schema": {
            "properties": {"candidates": {"maxItems": 1}},
        },
    }
    with pytest.raises(InteractionRequired) as raised:
        port.ask(
            ctx,
            "bgm-online-candidates",
            "Find BGM",
            "bgm_candidates",
            payload=payload,
        )

    with pytest.raises(ValueError, match=message):
        port.submit(ctx, raised.value.interaction["id"], response)

    pending = ctx.state["pending_interaction"]
    assert "response" not in pending
    assert "submitted_interactions" not in ctx.state
    audit = (tmp_path / "session/interactions.jsonl").read_text(encoding="utf-8")
    assert response not in audit


def test_bgm_submission_bounds_do_not_affect_other_interaction_kinds(tmp_path: Path):
    ctx = Context(tmp_path)
    port = DurableInteractionPort()
    with pytest.raises(InteractionRequired) as raised:
        port.ask(ctx, "note", "Provide note", "text")

    response = "x" * 1000
    assert port.submit(ctx, raised.value.interaction["id"], response) is True
