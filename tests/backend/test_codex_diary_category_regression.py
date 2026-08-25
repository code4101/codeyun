import json
from pathlib import Path

from backend.api.notes import (
    _annotate_codex_diary_record_category,
    _fanxiu_registered_job_identity_terms,
    _ensure_codex_diary_business_summary_coverage,
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


def test_codex_diary_classifies_every_registered_fanxiu_job_identity_as_fanxiu():
    terms = _fanxiu_registered_job_identity_terms()
    assert {"activity_quiz", "活动_答题", "activity-quiz", "历练_事件"}.issubset(terms)

    for term in terms:
        category = _resolve_codex_diary_category(
            {
                "thread_title": f"{term} 作业维护",
                "project_label": "codeyun",
                "user_request": f"继续修复正式作业 {term} 的运行逻辑。",
                "assistant_result": "已完成作业实现并验证 Scheduler 清单。",
            },
            palette_lookup=_palette_lookup(),
            title_hints={},
        )
        assert category["key"] == "legacy_color_67c23a", term


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


def test_codex_diary_thread_inheritance_uses_primary_anchor_not_strong_secondary_score():
    palette_lookup = _palette_lookup()
    records = [
        {
            "thread_id": "attendance-followup",
            "thread_title": "修道班退款核查",
            "project_label": "codeyun",
            "user_request": "把四个修道班返款规则保存到 docs 恢复档案。",
            "assistant_result": "考勤 source_meta 保留规则，前端隐藏返款文案。",
        },
        {
            "thread_id": "attendance-followup",
            "thread_title": "修道班退款核查",
            "project_label": "codeyun",
            "user_request": "我感觉 mi15 那边没动了，是不是检查完了？",
            "assistant_result": "核查已经完成，列出未全退订单与四个课程结果。",
        },
        {
            "thread_id": "attendance-followup",
            "thread_title": "修道班退款核查",
            "project_label": "codeyun",
            "user_request": "继续核对四个修道班剩余订单。",
            "assistant_result": "考勤课程订单已全部核对。",
        },
    ]
    for record in records:
        _annotate_codex_diary_record_category(record, palette_lookup=palette_lookup, title_hints={})

    _inherit_codex_diary_thread_domain_categories(records, palette_lookup=palette_lookup)

    assert [record["codex_diary_category_key"] for record in records] == [
        "legacy_color_e6a23c",
        "legacy_color_e6a23c",
        "legacy_color_e6a23c",
    ]


def test_codex_diary_splits_knowledge_attendance_and_fanxiu_records_before_aggregation():
    palette_lookup = _palette_lookup()
    records = [
        {
            "thread_id": "halting",
            "thread_title": "解释图灵机停机问题",
            "project_label": "codeyun",
            "user_request": "图灵机停机问题为什么无法由一个通用程序判定？",
            "assistant_result": "从可计算性的定义解释反转程序与自指矛盾。",
        },
        {
            "thread_id": "attendance",
            "thread_title": "四个修道班配置归档",
            "project_label": "codeyun",
            "user_request": "核对修道班13期1阶和11期3阶的课程配置。",
            "assistant_result": "修正考勤 source_meta.course_name，并保留各课程规则。",
        },
        {
            "thread_id": "quantum",
            "thread_title": "量子计算机原理",
            "project_label": "codeyun",
            "user_request": "量子计算机的原理是什么？我不理解。",
            "assistant_result": "量子计算先确定性地操纵概率振幅，最后再测量。",
        },
        {
            "thread_id": "fanxiu",
            "thread_title": "玄荒完成判断",
            "project_label": "codeyun",
            "user_request": "进入 #418 后先识别次数，0次不能点。",
            "assistant_result": "修复凡修玄荒 OCR 完成态并验证。",
        },
    ]

    for record in records:
        _annotate_codex_diary_record_category(record, palette_lookup=palette_lookup, title_hints={})

    assert [record["codex_diary_category_key"] for record in records] == [
        "custom_mmx3qpfhinvh",
        "legacy_color_e6a23c",
        "custom_mmx3qpfhinvh",
        "legacy_color_67c23a",
    ]


def test_codex_diary_classifies_yunmeng_trial_business_as_fanxiu():
    category = _resolve_codex_diary_category(
        {
            "thread_title": "云梦试剑兑币能力沉淀",
            "project_label": "codeyun",
            "user_request": "把云梦试剑累计兑币、剩余挑战和目标预测重新算准。",
            "assistant_result": "接口优先读取 YunmengPK 运行态，挑战记录去重后滚动更新速度与丹均积分。",
        },
        palette_lookup=_palette_lookup(),
        title_hints={},
    )

    assert category["key"] == "legacy_color_67c23a"


def test_codex_diary_fanxiu_summary_keeps_yunmeng_business_theme():
    block = {
        "category_label": "凡修",
        "records": [
            {
                "user_request": "修正云梦试剑累计兑币和还需挑战。",
                "assistant_result": "完成榜单、丹均积分与目标预测复算。",
            }
        ],
        "summary_items": [f"其它凡修主线 {index}" for index in range(1, 7)],
    }

    _ensure_codex_diary_business_summary_coverage(block)

    assert len(block["summary_items"]) == 6
    assert "云梦试剑" in block["summary_items"][-1]


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
