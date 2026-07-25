from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .templates import TemplateDefinition


@dataclass(frozen=True)
class BgmPolicy:
    enabled: bool = True
    instrumental_only: bool = True
    preferred_moods: tuple[str, ...] = ()
    preferred_energy: str = "low-medium"
    preferred_tempo_bpm: tuple[float, float] = (70.0, 105.0)
    avoid_tags: tuple[str, ...] = ("vocal", "heavy-drums")
    ducking_strength: str = "medium"
    fade_in_ms: int = 2000
    fade_out_ms: int = 3000

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "BgmPolicy":
        tempo = tuple(value.get("preferred_tempo_bpm", (70, 105)))
        if len(tempo) != 2 or float(tempo[0]) > float(tempo[1]):
            raise ValueError("preferred_tempo_bpm must be an ascending pair")
        ducking = str(value.get("ducking_strength", "medium"))
        if ducking not in {"light", "medium", "strong"}:
            raise ValueError(f"Unsupported ducking strength: {ducking}")
        fade_in = int(value.get("fade_in_ms", 2000))
        fade_out = int(value.get("fade_out_ms", 3000))
        if fade_in < 0 or fade_out < 0:
            raise ValueError("BGM fades must be non-negative")
        return cls(
            enabled=bool(value.get("enabled", True)),
            instrumental_only=bool(value.get("instrumental_only", True)),
            preferred_moods=tuple(map(str, value.get("preferred_moods", ()))),
            preferred_energy=str(value.get("preferred_energy", "low-medium")),
            preferred_tempo_bpm=(float(tempo[0]), float(tempo[1])),
            avoid_tags=tuple(map(str, value.get("avoid_tags", ("vocal", "heavy-drums")))),
            ducking_strength=ducking,
            fade_in_ms=fade_in,
            fade_out_ms=fade_out,
        )


def load_bgm_policy(template: TemplateDefinition) -> BgmPolicy:
    path = template.paths.get("bgm")
    if path is None:
        return BgmPolicy()
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid BGM policy: {path}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid BGM policy: {path}")
    return BgmPolicy.from_dict(raw)
