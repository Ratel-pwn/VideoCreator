import json

from main import load_json


def test_load_json_accepts_utf8_bom(tmp_path):
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps({"renderer": "remotion"}), encoding="utf-8-sig")

    assert load_json(path) == {"renderer": "remotion"}
