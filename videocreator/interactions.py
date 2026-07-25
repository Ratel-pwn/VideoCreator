from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    def ask(
        self,
        ctx: InteractionContext,
        key: str,
        prompt: str,
        kind: str = "text",
        choices: tuple[str, ...] = (),
    ) -> str: ...

    def clear(self, ctx: InteractionContext, *keys: str) -> None: ...


class ConsoleInteractionPort:
    def ask(
        self,
        ctx: InteractionContext,
        key: str,
        prompt: str,
        kind: str = "text",
        choices: tuple[str, ...] = (),
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
    ) -> str:
        answers = ctx.state.setdefault("interaction_answers", {})
        if key in answers:
            return str(answers[key])

        pending = ctx.state.get("pending_interaction")
        if pending:
            if pending.get("key") != key:
                raise InteractionRequired(pending)
            if "response" not in pending:
                raise InteractionRequired(pending)
            response = str(pending["response"])
            answers[key] = response
            ctx.state.pop("pending_interaction", None)
            ctx.save_state()
            return response

        interaction = {
            "id": uuid.uuid4().hex,
            "key": key,
            "kind": kind,
            "prompt": prompt,
            "choices": list(choices),
            "created_at": _now(),
        }
        ctx.state["pending_interaction"] = interaction
        ctx.state["status"] = "waiting_for_input"
        ctx.save_state()
        self._append(ctx, {"event": "asked", **interaction})
        raise InteractionRequired(interaction)

    def submit(self, ctx: InteractionContext, interaction_id: str, response: str) -> bool:
        pending = ctx.state.get("pending_interaction")
        submitted = ctx.state.setdefault("submitted_interactions", {})
        if interaction_id in submitted:
            if submitted[interaction_id] != response:
                raise ValueError("Interaction already has a different response")
            return False
        if not pending or pending.get("id") != interaction_id:
            raise ValueError(f"Stale interaction: {interaction_id}")
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
                "response": response,
                "answered_at": pending["answered_at"],
            },
        )
        return True

    def clear(self, ctx: InteractionContext, *keys: str) -> None:
        answers = ctx.state.get("interaction_answers", {})
        for key in keys:
            answers.pop(key, None)
        if not answers:
            ctx.state.pop("interaction_answers", None)
        ctx.save_state()


@dataclass(frozen=True)
class WorkflowOutcome:
    status: str
    interaction: dict[str, Any] | None = None
    error: str | None = None
