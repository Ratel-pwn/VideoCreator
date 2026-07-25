from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .bgm_library import BgmTrack
from .bgm_policy import BgmPolicy


WEIGHTS = {
    "subject": 30.0,
    "mood": 25.0,
    "template": 20.0,
    "energy": 10.0,
    "tempo": 10.0,
    "instrumental": 5.0,
    "avoid": -50.0,
}

_ENGLISH_TOKEN = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", re.IGNORECASE)
_CHINESE_TOKEN = re.compile(r"[\u4e00-\u9fff]{1,12}")
_MAX_TERMS_PER_LANGUAGE = 12
_APPROVED_TEXT_SAMPLE_CHARS = 1200


@dataclass(frozen=True)
class BgmQuery:
    subjects: tuple[str, ...]
    moods: tuple[str, ...]
    template_id: str
    terms_zh: tuple[str, ...]
    terms_en: tuple[str, ...]


@dataclass(frozen=True)
class CandidateScore:
    track_id: str
    total: float
    eligible: bool
    components: dict[str, float]
    rejection_reasons: tuple[str, ...]


@dataclass(frozen=True)
class SelectionResult:
    track: BgmTrack | None
    scores: tuple[CandidateScore, ...]


def _normalize(value: str) -> str:
    return "-".join(part for part in re.split(r"[^a-z0-9\u4e00-\u9fff]+", value.lower()) if part)


def _deduplicate(values: Iterable[str], limit: int | None = None) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if limit is not None and len(result) >= limit:
            break
    return tuple(result)


def _extract_english(text: str) -> tuple[str, ...]:
    return _deduplicate(_ENGLISH_TOKEN.findall(text), _MAX_TERMS_PER_LANGUAGE)


def _extract_chinese(text: str) -> tuple[str, ...]:
    return _deduplicate(_CHINESE_TOKEN.findall(text), _MAX_TERMS_PER_LANGUAGE)


def build_bgm_query(
    title: str,
    topic: str,
    approved_text: str,
    template_id: str,
    policy: BgmPolicy,
) -> BgmQuery:
    """Build deterministic, bounded search terms without inferring new tags."""
    title_topic = f"{title}\n{topic}"
    sampled_text = approved_text[:_APPROVED_TEXT_SAMPLE_CHARS]
    policy_terms = " ".join(
        (*policy.preferred_moods, policy.preferred_energy, *policy.avoid_tags)
    )
    query_text = f"{title_topic}\n{policy_terms}\n{sampled_text}"
    subject_terms = (*_extract_english(title_topic), *_extract_chinese(title_topic))
    return BgmQuery(
        subjects=subject_terms,
        moods=_deduplicate(policy.preferred_moods),
        template_id=_normalize(template_id),
        terms_zh=_extract_chinese(query_text),
        terms_en=_extract_english(query_text),
    )


def _matches(left: Iterable[str], right: Iterable[str]) -> bool:
    return bool(set(_deduplicate(left)) & set(_deduplicate(right)))


def _track_tags(track: BgmTrack) -> tuple[str, ...]:
    return (
        *track.subjects,
        *track.moods,
        *track.template_tags,
        track.energy,
    )


def score_candidate(track: BgmTrack, query: BgmQuery, policy: BgmPolicy) -> CandidateScore:
    components = {
        "subject": WEIGHTS["subject"] if _matches(track.subjects, query.subjects) else 0.0,
        "mood": WEIGHTS["mood"] if _matches(track.moods, query.moods) else 0.0,
        "template": WEIGHTS["template"] if _matches(track.template_tags, (query.template_id,)) else 0.0,
        "energy": WEIGHTS["energy"]
        if _normalize(track.energy) == _normalize(policy.preferred_energy)
        else 0.0,
        "tempo": WEIGHTS["tempo"]
        if track.tempo_bpm is not None
        and policy.preferred_tempo_bpm[0] <= track.tempo_bpm <= policy.preferred_tempo_bpm[1]
        else 0.0,
        "instrumental": WEIGHTS["instrumental"] if track.instrumental else 0.0,
        "avoid": 0.0,
    }
    avoided_by_policy = _matches(_track_tags(track), policy.avoid_tags)
    avoided_by_track = _matches(
        track.avoid_for, (*query.subjects, *query.moods, query.template_id)
    )
    if avoided_by_policy or avoided_by_track:
        components["avoid"] = WEIGHTS["avoid"]

    rejection_reasons = ()
    if policy.instrumental_only and not track.instrumental:
        rejection_reasons = ("instrumental_only",)
    return CandidateScore(
        track_id=track.id,
        total=sum(components.values()),
        eligible=not rejection_reasons,
        components=components,
        rejection_reasons=rejection_reasons,
    )


def select_bgm_candidate(
    tracks: Iterable[BgmTrack], query: BgmQuery, policy: BgmPolicy
) -> SelectionResult:
    pairs = [(track, score_candidate(track, query, policy)) for track in tracks]
    pairs.sort(
        key=lambda item: (
            not item[1].eligible,
            -item[1].total,
            item[0].id,
            item[0].sha256,
            item[0].path.as_posix().casefold(),
        )
    )
    scores = tuple(score for _, score in pairs)
    selected = next((track for track, score in pairs if score.eligible), None)
    return SelectionResult(track=selected, scores=scores)
