import json
from pathlib import Path

from backend.api.notes import (
    _annotate_codex_diary_record_category,
    _inherit_codex_diary_thread_domain_categories,
    _is_codex_diary_hidden_ai_category_item,
    _normalize_project_palette_token,
    _resolve_codex_diary_category,
    _resolve_codex_diary_group_categories,
)
from backend.core.notes.semantics import derive_primary_category


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
        {"key": "legacy_color_e6a23c", "label": "考勤", "color": "#E6A23C", "order": 15},
        {"key": "custom_mmx3qpfhinvh", "label": "CodeYun/笔记", "color": "#446CCF", "order": 20},
        {"key": "custom_mmxc75t01g04", "label": "CodeYun/集群", "color": "#0067A5", "order": 40},
        {"key": "custom_mmxdcghtzcw7", "label": "CodeYun/综合", "color": "#00BFFF", "order": 50},
        {"key": "造化仙缘", "label": "造化仙缘", "color": "#9B2A20", "order": 55},
        {"key": "custom_mmxdyjjkxrsr", "label": "pyxllib", "color": "#2f9fa8", "order": 60},
        {"key": "custom_mmxbzxjy85x5", "label": "后勤", "color": "#E6A23C", "order": 70},
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
        raw_records = case.get("raw_records") or []
        assert raw_records, case["id"]

        note_categories = _resolve_codex_diary_group_categories(
            raw_records,
            palette_lookup=palette_lookup,
            title_hints={},
        )
        primary_category = derive_primary_category(note_categories)

        assert primary_category == case["expected_category_key"], case["id"]


def test_codex_diary_inherits_unique_strong_domain_anchor_within_thread():
    palette_lookup = _palette_lookup()
    records = [
        {
            "thread_id": "zaohua-equipment-thread",
            "thread_title": "游戏背包和多套装备",
            "project_label": "codeyun",
            "user_request": "为《造化仙缘》增加装备方案",
            "assistant_result": "已在 Code4101.Tiandao 中接入游戏原生换装接口。",
        },
        {
            "thread_id": "zaohua-equipment-thread",
            "thread_title": "游戏背包和多套装备",
            "project_label": "codeyun",
            "user_request": "继续修正方案切换失败回滚",
            "assistant_result": "按 blendType+itemId 匹配，保留原方案并补充日志。",
        },
    ]
    for record in records:
        _annotate_codex_diary_record_category(record, palette_lookup=palette_lookup, title_hints={})

    _inherit_codex_diary_thread_domain_categories(records, palette_lookup=palette_lookup)

    assert [record["codex_diary_category_key"] for record in records] == ["造化仙缘", "造化仙缘"]


def test_codex_diary_does_not_inherit_when_thread_has_multiple_strong_domains():
    palette_lookup = _palette_lookup()
    records = [
        {
            "thread_id": "mixed-domain-thread",
            "thread_title": "连续处理两个游戏",
            "user_request": "修复造化仙缘天道插件的装备方案",
            "assistant_result": "已更新 Code4101.Tiandao。",
        },
        {
            "thread_id": "mixed-domain-thread",
            "thread_title": "连续处理两个游戏",
            "user_request": "继续凡修宗门镇邪与魔祖行为树",
            "assistant_result": "已完成镇邪场景校验。",
        },
    ]
    for record in records:
        _annotate_codex_diary_record_category(record, palette_lookup=palette_lookup, title_hints={})

    _inherit_codex_diary_thread_domain_categories(records, palette_lookup=palette_lookup)

    assert [record["codex_diary_category_key"] for record in records] == ["造化仙缘", "legacy_color_67c23a"]


def test_codex_diary_uses_codeyun_general_instead_of_builtin_general_for_unknown_work():
    category = _resolve_codex_diary_category(
        {
            "thread_title": "零散问答",
            "project_label": "",
            "user_request": "idea 的复数形式是什么？",
            "assistant_result": "ideas。",
        },
        palette_lookup=_palette_lookup(),
        title_hints={},
    )

    assert category["key"] == "custom_mmxdcghtzcw7"


def test_codex_diary_hides_logistics_from_ai_without_removing_the_category():
    item = {"key": "custom_mmxbzxjy85x5", "label": "后勤", "color": "#D2B48C"}

    assert _is_codex_diary_hidden_ai_category_item(item) is True


def test_codex_diary_hides_ai_concept_category_without_removing_it():
    item = {"key": "custom_mmxdhqhnrgup", "label": "AI", "color": "#6F2DBD"}

    assert _is_codex_diary_hidden_ai_category_item(item) is True
