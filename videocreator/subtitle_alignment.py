from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any

from opencc import OpenCC


VISIBLE_CHAR_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
_T2S_CONVERTER = OpenCC("t2s")


@lru_cache(maxsize=4096)
def to_simplified(text: str) -> str:
    return _T2S_CONVERTER.convert(text)


def normalize_visible_chars(text: str) -> list[str]:
    simplified = to_simplified(text)
    return [match.group(0).lower() for match in VISIBLE_CHAR_RE.finditer(simplified)]


@dataclass(frozen=True)
class RecognizedChar:
    text: str
    start_ms: int
    end_ms: int
    confidence: float

    @property
    def normalized(self) -> str:
        return to_simplified(self.text).lower()


@dataclass
class ApprovedChar:
    text: str
    index: int
    start_ms: int | None = None
    end_ms: int | None = None
    confidence: float = 0.0
    exact: bool = False


@dataclass(frozen=True)
class AlignedBlock:
    text: str
    start_ms: int
    end_ms: int
    exact_coverage: float
    timing_coverage: float
    confidence: float
    source_start: int
    source_end: int


@dataclass
class AlignmentResult:
    approved: list[ApprovedChar]
    recognized: list[RecognizedChar]
    exact_match_count: int
    character_error_rate: float
    unmatched_approved_spans: list[dict[str, Any]] = field(default_factory=list)
    unmatched_recognized_spans: list[dict[str, Any]] = field(default_factory=list)

    @property
    def exact_match_coverage(self) -> float:
        return self.exact_match_count / len(self.approved) if self.approved else 1.0

    @property
    def timing_coverage(self) -> float:
        if not self.approved:
            return 1.0
        resolved = sum(item.start_ms is not None and item.end_ms is not None for item in self.approved)
        return resolved / len(self.approved)

    @property
    def max_unresolved_span_ms(self) -> int:
        if not self.approved or self.timing_coverage == 1.0:
            return 0
        recognized_end = max(
            (item.end_ms for item in self.recognized),
            default=0,
        )
        maximum = 0
        index = 0
        while index < len(self.approved):
            if self.approved[index].start_ms is not None:
                index += 1
                continue
            start = index
            while (
                index < len(self.approved)
                and self.approved[index].start_ms is None
            ):
                index += 1
            left_end = (
                self.approved[start - 1].end_ms
                if start > 0
                and self.approved[start - 1].end_ms is not None
                else 0
            )
            right_start = (
                self.approved[index].start_ms
                if index < len(self.approved)
                and self.approved[index].start_ms is not None
                else recognized_end
            )
            maximum = max(maximum, max(0, int(right_start - left_end)))
        return maximum

    def to_report(self) -> dict[str, Any]:
        return {
            "approved_character_count": len(self.approved),
            "recognized_character_count": len(self.recognized),
            "exact_match_coverage": round(self.exact_match_coverage, 6),
            "character_error_rate": round(self.character_error_rate, 6),
            "timing_coverage": round(self.timing_coverage, 6),
            "max_unresolved_span_ms": self.max_unresolved_span_ms,
            "unmatched_approved_spans": self.unmatched_approved_spans,
            "unmatched_recognized_spans": self.unmatched_recognized_spans,
        }


def _levenshtein_distance(left: list[str], right: list[str]) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_value in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_value in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_value != right_value),
            ))
        previous = current
    return previous[-1]


def _unmatched_spans(
    values: list[str],
    matched: set[int],
) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    start: int | None = None
    for index in range(len(values) + 1):
        unmatched = index < len(values) and index not in matched
        if unmatched and start is None:
            start = index
        if not unmatched and start is not None:
            spans.append({
                "start_index": start,
                "end_index": index,
                "text": "".join(values[start:index]),
            })
            start = None
    return spans


def _interpolate_bounded_gaps(approved: list[ApprovedChar]) -> None:
    resolved = [index for index, item in enumerate(approved) if item.start_ms is not None]
    for left_index, right_index in zip(resolved, resolved[1:]):
        gap = right_index - left_index - 1
        if gap <= 0 or gap > 12:
            continue
        left = approved[left_index]
        right = approved[right_index]
        if left.end_ms is None or right.start_ms is None:
            continue
        available = max(0, right.start_ms - left.end_ms)
        step = available / (gap + 1)
        for offset in range(1, gap + 1):
            item = approved[left_index + offset]
            item.start_ms = round(left.end_ms + step * (offset - 1))
            item.end_ms = round(left.end_ms + step * offset)
            item.confidence = min(left.confidence, right.confidence) * 0.5


def align_approved_text(
    approved_text: str,
    recognized: list[RecognizedChar],
) -> AlignmentResult:
    approved_values = normalize_visible_chars(approved_text)
    recognized_values = [item.normalized for item in recognized]
    approved = [
        ApprovedChar(text=value, index=index)
        for index, value in enumerate(approved_values)
    ]
    matcher = SequenceMatcher(
        None,
        approved_values,
        recognized_values,
        autojunk=False,
    )
    matched_approved: set[int] = set()
    matched_recognized: set[int] = set()
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            approved_index = block.a + offset
            recognized_index = block.b + offset
            source = recognized[recognized_index]
            target = approved[approved_index]
            target.start_ms = source.start_ms
            target.end_ms = source.end_ms
            target.confidence = source.confidence
            target.exact = True
            matched_approved.add(approved_index)
            matched_recognized.add(recognized_index)
    _interpolate_bounded_gaps(approved)
    distance = _levenshtein_distance(approved_values, recognized_values)
    return AlignmentResult(
        approved=approved,
        recognized=recognized,
        exact_match_count=len(matched_approved),
        character_error_rate=distance / max(1, len(approved_values)),
        unmatched_approved_spans=_unmatched_spans(approved_values, matched_approved),
        unmatched_recognized_spans=_unmatched_spans(
            recognized_values, matched_recognized
        ),
    )


def build_aligned_blocks(
    chunks: list[str],
    result: AlignmentResult,
) -> list[AlignedBlock]:
    blocks: list[AlignedBlock] = []
    cursor = 0
    for chunk in chunks:
        count = len(normalize_visible_chars(chunk))
        if count == 0:
            raise ValueError("subtitle chunk has no visible approved text")
        source_start = cursor
        source_end = min(len(result.approved), cursor + count)
        selected = result.approved[source_start:source_end]
        cursor = source_end
        if len(selected) != count:
            raise ValueError(
                "subtitle segmentation does not cover approved text exactly"
            )
        resolved = [
            item
            for item in selected
            if item.start_ms is not None and item.end_ms is not None
        ]
        if len(resolved) != len(selected):
            raise ValueError(
                "unresolved approved subtitle chunk cannot be timed safely: "
                f"{chunk}"
            )
        exact = sum(item.exact for item in selected)
        confidence = sum(item.confidence for item in resolved) / len(resolved)
        blocks.append(AlignedBlock(
            text=chunk,
            start_ms=int(resolved[0].start_ms),
            end_ms=int(resolved[-1].end_ms),
            exact_coverage=exact / max(1, len(selected)),
            timing_coverage=len(resolved) / max(1, len(selected)),
            confidence=confidence,
            source_start=source_start,
            source_end=source_end,
        ))
    if cursor != len(result.approved):
        raise ValueError(
            "subtitle segmentation does not cover approved text exactly"
        )
    return blocks


def recognized_chars_from_whisper(result: dict[str, Any]) -> list[RecognizedChar]:
    output: list[RecognizedChar] = []
    for segment in result.get("segments") or []:
        for word in segment.get("words") or []:
            visible = normalize_visible_chars(str(word.get("word", "")))
            if not visible:
                continue
            start_ms = round(float(word.get("start", 0.0)) * 1000)
            end_ms = round(float(word.get("end", 0.0)) * 1000)
            duration = max(0, end_ms - start_ms)
            confidence = float(word.get("probability", segment.get("avg_logprob", 0.0)))
            if confidence < 0:
                confidence = max(0.0, min(1.0, 1.0 + confidence))
            for index, character in enumerate(visible):
                char_start = start_ms + round(duration * index / len(visible))
                char_end = start_ms + round(duration * (index + 1) / len(visible))
                output.append(RecognizedChar(
                    text=character,
                    start_ms=char_start,
                    end_ms=char_end,
                    confidence=confidence,
                ))
    return output
