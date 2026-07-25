from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def interaction_fingerprint(
    kind: str,
    payload: dict[str, Any] | None,
) -> str:
    canonical = json.dumps(
        {"kind": kind, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class InteractionContext(Protocol):
    run_id: str
    run_dir: Path
    state: dict[str, Any]

    def save_state(self) -> None: ...


class InteractionRequired(RuntimeError):
    def __init__(self, interaction: dict[str, Any]):
        super().__init__(interaction["prompt"])
        self.interaction = interaction


class InteractionPort(Protocol):
    supports_agent_handoff: bool

    def ask(
        self,
        ctx: InteractionContext,
        key: str,
        prompt: str,
        kind: str = "text",
        choices: tuple[str, ...] = (),
        payload: dict[str, Any] | None = None,
    ) -> str: ...

    def clear(self, ctx: InteractionContext, *keys: str) -> None: ...


class ConsoleInteractionPort:
    supports_agent_handoff = False

    def ask(
        self,
        ctx: InteractionContext,
        key: str,
        prompt: str,
        kind: str = "text",
        choices: tuple[str, ...] = (),
        payload: dict[str, Any] | None = None,
    ) -> str:
        suffix = f" [{' / '.join(choices)}]" if choices else ""
        while True:
            answer = input(f"{prompt}{suffix}: ").strip()
            if not choices or answer in choices:
                return answer
            print(f"请输入以下选项之一：{', '.join(choices)}")

    def clear(self, ctx: InteractionContext, *keys: str) -> None:
        return None


class DurableInteractionPort:
    supports_agent_handoff = True

    def _append(self, ctx: InteractionContext, value: dict[str, Any]) -> None:
        path = ctx.run_dir / "session" / "interactions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")

    def ask(
        self,
        ctx: InteractionContext,
        key: str,
        prompt: str,
        kind: str = "text",
        choices: tuple[str, ...] = (),
        payload: dict[str, Any] | None = None,
    ) -> str:
        expected_fingerprint = interaction_fingerprint(kind, payload)
        answers = ctx.state.setdefault("interaction_answers", {})
        if key in answers:
            fingerprints = ctx.state.get("interaction_answer_fingerprints", {})
            if fingerprints.get(key) == expected_fingerprint:
                return str(answers[key])
            answers.pop(key, None)
            fingerprints.pop(key, None)
            consumed = ctx.state.get("consumed_interactions", {})
            submitted = ctx.state.get("submitted_interactions", {})
            interaction_id = consumed.pop(key, None)
            if interaction_id:
                submitted.pop(interaction_id, None)
            if not answers:
                ctx.state.pop("interaction_answers", None)
            if not fingerprints:
                ctx.state.pop("interaction_answer_fingerprints", None)
            if not consumed:
                ctx.state.pop("consumed_interactions", None)
            if not submitted:
                ctx.state.pop("submitted_interactions", None)

        pending = ctx.state.get("pending_interaction")
        if pending:
            if pending.get("key") != key:
                raise InteractionRequired(pending)
            stored_fingerprint = pending.get("fingerprint") or interaction_fingerprint(
                str(pending.get("kind", "text")),
                pending.get("payload"),
            )
            if stored_fingerprint != expected_fingerprint:
                submitted = ctx.state.get("submitted_interactions", {})
                submitted.pop(pending.get("id"), None)
                if not submitted:
                    ctx.state.pop("submitted_interactions", None)
                ctx.state.pop("pending_interaction", None)
                self._append(
                    ctx,
                    {
                        "event": "superseded",
                        "id": pending.get("id"),
                        "key": key,
                        "fingerprint": stored_fingerprint,
                        "superseded_at": _now(),
                    },
                )
                pending = None
            else:
                pending["fingerprint"] = stored_fingerprint
        if pending:
            if "response" not in pending:
                raise InteractionRequired(pending)
            response = str(pending["response"])
            answers[key] = response
            answer_fingerprints = ctx.state.setdefault(
                "interaction_answer_fingerprints",
                {},
            )
            answer_fingerprints[key] = expected_fingerprint
            consumed = ctx.state.setdefault("consumed_interactions", {})
            consumed[key] = pending["id"]
            ctx.state.pop("pending_interaction", None)
            ctx.save_state()
            return response

        interaction = {
            "id": uuid.uuid4().hex,
            "key": key,
            "kind": kind,
            "prompt": prompt,
            "choices": list(choices),
            "fingerprint": expected_fingerprint,
            "created_at": _now(),
        }
        if payload is not None:
            interaction["payload"] = deepcopy(payload)
        ctx.state["pending_interaction"] = interaction
        ctx.state["status"] = "waiting_for_input"
        ctx.save_state()
        self._append(ctx, {"event": "asked", **interaction})
        raise InteractionRequired(interaction)

    def submit(
        self,
        ctx: InteractionContext,
        interaction_id: str,
        response: str,
        *,
        fingerprint: str | None = None,
    ) -> bool:
        pending = ctx.state.get("pending_interaction")
        submitted = ctx.state.setdefault("submitted_interactions", {})
        if interaction_id in submitted:
            if submitted[interaction_id] != response:
                raise ValueError("Interaction already has a different response")
            return False
        if not pending or pending.get("id") != interaction_id:
            raise ValueError(f"Stale interaction: {interaction_id}")
        stored_fingerprint = pending.get("fingerprint") or interaction_fingerprint(
            str(pending.get("kind", "text")),
            pending.get("payload"),
        )
        if fingerprint is not None and fingerprint != stored_fingerprint:
            raise ValueError("Interaction fingerprint does not match")
        pending["fingerprint"] = stored_fingerprint
        choices = pending.get("choices") or []
        if choices and response not in choices:
            raise ValueError(f"Response must be one of: {', '.join(choices)}")
        pending["response"] = response
        pending["answered_at"] = _now()
        submitted[interaction_id] = response
        ctx.save_state()
        self._append(
            ctx,
            {
                "event": "answered",
                "id": interaction_id,
                "key": pending["key"],
                "response_sha256": hashlib.sha256(
                    response.encode("utf-8")
                ).hexdigest(),
                "response_bytes": len(response.encode("utf-8")),
                "answered_at": pending["answered_at"],
            },
        )
        return True

    def clear(self, ctx: InteractionContext, *keys: str) -> None:
        answers = ctx.state.get("interaction_answers", {})
        answer_fingerprints = ctx.state.get(
            "interaction_answer_fingerprints",
            {},
        )
        consumed = ctx.state.get("consumed_interactions", {})
        submitted = ctx.state.get("submitted_interactions", {})
        for key in keys:
            answers.pop(key, None)
            answer_fingerprints.pop(key, None)
            interaction_id = consumed.pop(key, None)
            if interaction_id:
                submitted.pop(interaction_id, None)
        pending = ctx.state.get("pending_interaction")
        if pending and pending.get("key") in keys:
            submitted.pop(pending.get("id"), None)
            ctx.state.pop("pending_interaction", None)
        if not answers:
            ctx.state.pop("interaction_answers", None)
        if not answer_fingerprints:
            ctx.state.pop("interaction_answer_fingerprints", None)
        if not consumed:
            ctx.state.pop("consumed_interactions", None)
        if not submitted:
            ctx.state.pop("submitted_interactions", None)
        ctx.save_state()


@dataclass(frozen=True)
class WorkflowOutcome:
    status: str
    interaction: dict[str, Any] | None = None
    error: str | None = None
