import pytest

from videocreator.subtitle_alignment import (
    RecognizedChar,
    align_approved_text,
    build_aligned_blocks,
)
from scripts.align_subtitles_with_whisper import build_alignment_artifacts


def chars(text: str, step_ms: int = 100) -> list[RecognizedChar]:
    return [
        RecognizedChar(
            text=character,
            start_ms=index * step_ms,
            end_ms=(index + 1) * step_ms,
            confidence=0.95,
        )
        for index, character in enumerate(text)
    ]


def test_alignment_uses_matching_text_instead_of_position():
    result = align_approved_text("甲乙丙丁", chars("甲乙多余丙丁"))

    assert result.approved[2].start_ms == 400
    assert result.exact_match_coverage == 1.0


def test_alignment_reports_unanchored_omission():
    result = align_approved_text("甲乙缺失丙丁", chars("甲乙丙丁"))

    assert result.exact_match_coverage < 1.0
    assert result.unmatched_approved_spans


def test_alignment_normalizes_mixed_english_case():
    result = align_approved_text("AI时代", chars("ai时代"))

    assert result.exact_match_coverage == 1.0


def test_alignment_normalizes_traditional_whisper_output():
    result = align_approved_text("我们继续游戏", chars("我們繼續遊戲"))

    assert result.exact_match_coverage == 1.0
    assert result.character_error_rate == 0.0


def test_blocks_use_matched_character_boundaries():
    result = align_approved_text("甲乙丙丁", chars("甲乙停顿丙丁", step_ms=200))

    blocks = build_aligned_blocks(["甲乙", "丙丁"], result)

    assert blocks[0].start_ms == 0
    assert blocks[0].end_ms == 400
    assert blocks[1].start_ms == 800
    assert blocks[1].end_ms == 1200


def test_blocks_fail_instead_of_dropping_an_unresolved_approved_chunk():
    result = align_approved_text("abcd", chars("ab"))

    with pytest.raises(ValueError, match="unresolved approved subtitle chunk"):
        build_aligned_blocks(["ab", "cd"], result)


def test_blocks_fail_when_segmentation_omits_approved_text():
    result = align_approved_text("abcd", chars("abcd"))

    with pytest.raises(ValueError, match="does not cover"):
        build_aligned_blocks(["ab"], result)


def test_whisper_artifacts_include_alignment_evidence():
    whisper_result = {
        "segments": [{
            "words": [
                {"word": "甲乙", "start": 0.0, "end": 0.2, "probability": 0.9},
                {"word": "停顿", "start": 0.2, "end": 0.4, "probability": 0.8},
                {"word": "丙丁", "start": 0.4, "end": 0.6, "probability": 0.9},
            ]
        }]
    }

    blocks, report = build_alignment_artifacts(
        "甲乙，丙丁",
        whisper_result,
        max_chars=2,
    )

    assert [block.start_ms for block in blocks] == [0, 400]
    assert report["exact_match_coverage"] == 1.0
    assert report["recognized_character_count"] == 6
