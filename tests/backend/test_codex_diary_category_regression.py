import json
from pathlib import Path

from backend.api.notes import (
    _annotate_codex_diary_record_category,
    _build_codex_diary_record_category_candidates,
    _normalize_project_palette_token,
)


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "codex_diary_category_cases.jsonl"


def _load_cases() -> list[dict]:
    cases = []
    with FIXTURE_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _palette_lookup() -> dict[str, dict]:
    items = [
        {"key": "general", "label": "综合", "color": "#808080", "order": 120},
        {"key": "bug", "label": "缺陷", "color": "#ff6b22", "order": 5},
        {"key": "legacy_color_67c23a", "label": "凡修", "color": "#73B839", "order": 10},
        {"key": "custom_mmx3qpfhinvh", "label": "CodeYun/笔记", "color": "#446CCF", "order": 20},
        {"key": "custom_mmxc75t01g04", "label": "CodeYun/集群", "color": "#0067A5", "order": 40},
        {"key": "custom_mmxdcghtzcw7", "label": "CodeYun/综合", "color": "#00BFFF", "order": 50},
    ]
    lookup: dict[str, dict] = {}
    for item in items:
        for value in (item["key"], item["label"], str(item["key"]).removeprefix("custom_")):
            token = _normalize_project_palette_token(value)
            if token:
                lookup[token] = item
    return lookup


def test_codex_diary_category_regression_cases():
    palette_lookup = _palette_lookup()
    for case in _load_cases():
        record = {
            "thread_title": case.get("thread_title"),
            "project_label": case.get("project_label"),
            "source_root_dir": case.get("source_root_dir"),
            "user_request": case.get("user_request"),
            "assistant_result": case.get("assistant_result"),
        }

        _annotate_codex_diary_record_category(record, palette_lookup=palette_lookup, title_hints={})
        record["codex_diary_category_candidates"] = _build_codex_diary_record_category_candidates(
            record,
            palette_lookup=palette_lookup,
        )

        assert record["codex_diary_category_key"] == case["expected_category_key"], case["id"]
        assert record["codex_diary_category_candidates"][0] == case["expected_category_key"], case["id"]
