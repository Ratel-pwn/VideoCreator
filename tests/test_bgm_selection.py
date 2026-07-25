from pathlib import Path

from videocreator.bgm_library import BgmTrack
from videocreator.bgm_policy import BgmPolicy


def track(track_id: str, **overrides) -> BgmTrack:
    values = {
        "id": track_id,
        "path": Path(f"{track_id}.mp3"),
        "metadata_path": Path(f"{track_id}.bgm.json"),
        "level": "project",
        "sha256": "a" * 64,
        "metadata_sha256": "b" * 64,
        "title": track_id,
        "creator": None,
        "source_url": None,
        "license": None,
        "rights_status": "known",
        "subjects": (),
        "moods": (),
        "energy": "low-medium",
        "tempo_bpm": None,
        "instrumental": True,
        "template_tags": (),
        "avoid_for": (),
        "preferred_start_ms": 0,
        "loopable": True,
    }
    values.update(overrides)
    return BgmTrack(**values)


def neutral_query():
    from videocreator.bgm_selection import BgmQuery

    return BgmQuery((), (), "science-explainer", (), ())


def test_selector_prefers_subject_mood_template_and_tempo_matches():
    from videocreator.bgm_selection import BgmQuery, select_bgm_candidate

    policy = BgmPolicy(
        preferred_moods=("reflective",),
        preferred_tempo_bpm=(70, 105),
    )
    query = BgmQuery(
        subjects=("technology",),
        moods=("reflective",),
        template_id="science-explainer",
        terms_zh=("科技", "思考"),
        terms_en=("technology", "reflective"),
    )

    selected = select_bgm_candidate(
        [
            track("loud", energy="high", tempo_bpm=150),
            track(
                "calm",
                subjects=("technology",),
                moods=("reflective",),
                template_tags=("science-explainer",),
                tempo_bpm=88,
            ),
        ],
        query,
        policy,
    )

    assert selected.track is not None
    assert selected.track.id == "calm"
    assert selected.scores[0].components["mood"] > 0


def test_selector_uses_stable_track_id_as_final_tie_breaker():
    from videocreator.bgm_selection import select_bgm_candidate

    selected = select_bgm_candidate(
        [track("b"), track("a")],
        neutral_query(),
        BgmPolicy(),
    )

    assert selected.track is not None
    assert selected.track.id == "a"


def test_selector_is_stable_for_duplicate_ids_when_input_is_reversed():
    from videocreator.bgm_selection import select_bgm_candidate

    first = track(
        "duplicate",
        path=Path("z-track.mp3"),
        sha256="b" * 64,
    )
    second = track(
        "duplicate",
        path=Path("a-track.mp3"),
        sha256="a" * 64,
    )

    forward = select_bgm_candidate([first, second], neutral_query(), BgmPolicy())
    reversed_result = select_bgm_candidate(
        [second, first], neutral_query(), BgmPolicy()
    )

    assert forward.track is not None
    assert reversed_result.track is not None
    assert forward.track.path == Path("a-track.mp3")
    assert reversed_result.track.path == forward.track.path


def test_selector_rejects_non_instrumental_tracks_when_required():
    from videocreator.bgm_selection import score_candidate, select_bgm_candidate

    policy = BgmPolicy(instrumental_only=True)
    rejected = track("lyrics", instrumental=False)
    score = score_candidate(rejected, neutral_query(), policy)

    selected = select_bgm_candidate([rejected], neutral_query(), policy)

    assert not score.eligible
    assert score.rejection_reasons == ("instrumental_only",)
    assert selected.track is None


def test_selector_penalizes_policy_avoid_tags():
    from videocreator.bgm_selection import BgmQuery, score_candidate

    policy = BgmPolicy(avoid_tags=("comedy",))
    query = BgmQuery(("finance",), (), "science-explainer", (), ())
    score = score_candidate(
        track("avoid", moods=("comedy",)), query, policy
    )

    assert score.components["avoid"] == -50.0


def test_selector_penalizes_track_level_avoid_for():
    from videocreator.bgm_selection import BgmQuery, score_candidate

    policy = BgmPolicy(avoid_tags=())
    query = BgmQuery(("finance",), (), "science-explainer", (), ())
    score = score_candidate(
        track("avoid", avoid_for=("finance",)), query, policy
    )

    assert score.components["avoid"] == -50.0


def test_build_query_is_bounded_normalized_and_repeatable():
    from videocreator.bgm_selection import build_bgm_query

    policy = BgmPolicy(preferred_moods=("Reflective", "calm"))
    query = build_bgm_query(
        "科技与未来 Future Tech",
        "technology 教育",
        "科技 科技 科技 " * 100 + "reflective systems and society",
        "science-explainer",
        policy,
    )

    repeated = build_bgm_query(
        "科技与未来 Future Tech",
        "technology 教育",
        "科技 科技 科技 " * 100 + "reflective systems and society",
        "science-explainer",
        policy,
    )

    assert query == repeated
    assert query.template_id == "science-explainer"
    assert "technology" in query.subjects
    assert "reflective" in query.moods
    assert len(query.terms_zh) <= 12
    assert len(query.terms_en) <= 12
    assert len(query.terms_zh) == len(set(query.terms_zh))
    assert len(query.terms_en) == len(set(query.terms_en))
