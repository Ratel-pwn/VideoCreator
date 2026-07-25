import json

import pytest


def test_policy_loads_declared_template_policy(tmp_path):
    from videocreator.bgm_policy import load_bgm_policy

    policy_path = tmp_path / "bgm.json"
    policy_path.write_text(
        json.dumps({
            "enabled": True,
            "preferred_moods": ["reflective"],
            "preferred_tempo_bpm": [80, 100],
            "ducking_strength": "strong",
        }),
        encoding="utf-8",
    )
    template = type("Template", (), {"paths": {"bgm": policy_path}})()

    policy = load_bgm_policy(template)

    assert policy.preferred_moods == ("reflective",)
    assert policy.preferred_tempo_bpm == (80.0, 100.0)
    assert policy.ducking_strength == "strong"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"preferred_tempo_bpm": [105, 70]}, "ascending"),
        ({"ducking_strength": "none"}, "Unsupported ducking"),
        ({"fade_in_ms": -1}, "non-negative"),
    ],
)
def test_policy_rejects_invalid_values(value, message):
    from videocreator.bgm_policy import BgmPolicy

    with pytest.raises(ValueError, match=message):
        BgmPolicy.from_dict(value)


def test_missing_policy_uses_conservative_defaults():
    from videocreator.bgm_policy import BgmPolicy, load_bgm_policy

    template = type("Template", (), {"paths": {}})()

    assert load_bgm_policy(template) == BgmPolicy()
