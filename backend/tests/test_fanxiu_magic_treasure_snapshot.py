from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from backend.core.fanxiu.catalog.inventory_snapshot_store import (
    load_inventory_hall_snapshot,
    upsert_inventory_hall_snapshot,
)
from backend.core.fanxiu.instrumentation.magic_treasure import (
    _build_projection,
    _complete_talisman_rows,
    _is_routine_upgrade_effect,
)
from backend.core.fanxiu.instrumentation.magic_treasure import _localized_text
from backend.core.fanxiu.instrumentation.runtime_memory import LuaRef
from backend.core.fanxiu.instrumentation.magic_treasure_collector import (
    build_magic_treasure_database_snapshot,
)


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_inventory_snapshot_store_replaces_current_fact() -> None:
    with _session() as session:
        first = {
            "fabao": [{"id": "one"}],
            "runtime_complete": True,
            "runtime_updated_at": 100.0,
        }
        second = {
            "fabao": [{"id": "two"}],
            "runtime_complete": True,
            "runtime_updated_at": 200.0,
        }
        upsert_inventory_hall_snapshot(
            session, "magic_treasure_hall", first,
            source_kind="runtime", entity_name="法宝殿", require_complete_runtime=True,
        )
        upsert_inventory_hall_snapshot(
            session, "magic_treasure_hall", second,
            source_kind="runtime", entity_name="法宝殿", require_complete_runtime=True,
        )

        stored = load_inventory_hall_snapshot(session, "magic_treasure_hall")

        assert stored == second


def test_inventory_snapshot_store_returns_detached_nested_payload() -> None:
    with _session() as session:
        original = {
            "fabao": [{"id": "one", "type": ""}],
            "runtime_complete": True,
            "runtime_updated_at": 100.0,
        }
        upsert_inventory_hall_snapshot(
            session,
            "magic_treasure_hall",
            original,
            source_kind="runtime",
            entity_name="法宝殿",
        )

        loaded = load_inventory_hall_snapshot(session, "magic_treasure_hall")
        assert loaded is not None
        loaded["fabao"][0]["type"] = "灵力"
        session.expire_all()

        stored = load_inventory_hall_snapshot(session, "magic_treasure_hall")
        assert stored is not None
        assert stored["fabao"][0]["type"] == ""


@pytest.mark.parametrize(
    "description",
    [
        "攻击+2400",
        "灵力+288000",
        "气血+240000",
        "气血上限+90000",
        "守御+750",
        "灵力恢复+750",
        "功法增伤+4500",
        "功法减伤+4500",
        "攻击加成+1%",
        "灵力加成+1%",
        "气血加成+1%",
        "攻击资质+0.1%",
        "灵力资质+0.1%",
        "气血资质+0.1%",
        "角色永久增加攻击加成1%",
        "角色永久增加灵力加成1%",
        "角色永久增加气血加成1%",
        "角色永久增加气血上限加成1%",
    ],
)
def test_routine_upgrade_effect_covers_numeric_attribute_rows(description: str) -> None:
    assert _is_routine_upgrade_effect(description) is True


@pytest.mark.parametrize(
    "description",
    [
        "【狼影】：追击伤害提升400%",
        "贯穿几率提升14%",
        "触发护盾转化时额外恢复最大气血值",
    ],
)
def test_routine_upgrade_effect_keeps_skill_rows(description: str) -> None:
    assert _is_routine_upgrade_effect(description) is False


def test_incomplete_runtime_cannot_overwrite_inventory_snapshot() -> None:
    with _session() as session:
        valid = {
            "fabao": [{"id": "kept"}],
            "runtime_complete": True,
            "runtime_updated_at": 100.0,
        }
        upsert_inventory_hall_snapshot(
            session, "magic_treasure_hall", valid,
            source_kind="runtime", entity_name="法宝殿", require_complete_runtime=True,
        )

        with pytest.raises(ValueError, match="不完整"):
            upsert_inventory_hall_snapshot(
                session,
                "magic_treasure_hall",
                {"fabao": [], "runtime_complete": False},
                source_kind="runtime",
                entity_name="法宝殿",
                require_complete_runtime=True,
            )

        stored = load_inventory_hall_snapshot(session, "magic_treasure_hall")

    assert stored == valid


def test_collector_preserves_manual_metadata_and_splits_categories() -> None:
    runtime = {
        "complete": True,
        "source": "runtime_memory",
        "captured_at": "2026-08-06 10:00:00",
        "captured_timestamp": 123.0,
        "items": [
            {
                "id": "talisman-8",
                "talisman_id": 8,
                "name": "青竹蜂云剑",
                "rank": 35,
                "shenlian": 11,
                "section_key": "houtiangubao",
            }
        ],
    }
    existing = {
        "fabao": [
            {
                "id": "legacy",
                "talisman_id": 8,
                "name": "青竹蜂云剑",
                "main_use": "主阵",
                "date": "2026-01-01",
            }
        ]
    }

    snapshot = build_magic_treasure_database_snapshot(
        runtime,
        existing,
        {
            8: {
                "catalog_item_id": 4010008,
                "catalog_description": "青竹蜂云剑的图鉴来历。",
                "catalog_quality": 5,
                "catalog_quality_name": "黄色品质",
                "catalog_quality_color": "864c00",
            }
        },
    )

    assert snapshot["fabao"] == []
    assert snapshot["houtiangubao"][0]["main_use"] == "主阵"
    assert snapshot["houtiangubao"][0]["date"] == "2026-01-01"
    assert snapshot["houtiangubao"][0]["catalog_item_id"] == 4010008
    assert snapshot["houtiangubao"][0]["catalog_description"] == "青竹蜂云剑的图鉴来历。"
    assert snapshot["houtiangubao"][0]["catalog_quality"] == 5
    assert snapshot["houtiangubao"][0]["knowledge_source"] == "item_catalog"
    assert snapshot["runtime_item_count"] == 1


def test_owned_only_runtime_refresh_preserves_catalogue_and_recomputes_effect_state() -> None:
    runtime = {
        "complete": True,
        "owned_only": True,
        "effects_complete": False,
        "source": "runtime_memory",
        "items": [
            {
                "id": "talisman-8",
                "talisman_id": 8,
                "name": "青竹蜂云剑",
                "owned": True,
                "rank": 40,
                "wujing_level": 11,
                "section_key": "houtiangubao",
                "upgrade_effects": [{"stage": 40, "description": "当前运行态行"}],
            }
        ],
    }
    existing = {
        "fabao": [
            {
                "id": "talisman-9",
                "talisman_id": 9,
                "name": "未拥有法宝",
                "owned": False,
                "category": "法宝",
                "date": "2026-01-01",
            }
        ],
        "houtiangubao": [
            {
                "id": "talisman-8",
                "talisman_id": 8,
                "name": "青竹蜂云剑",
                "owned": True,
                "category": "后天古宝",
                "rank": 39,
                "date": "2026-01-01",
                "original_effect": "完整静态效果",
                "upgrade_effects": [
                    {"stage": 39, "description": "三十九阶", "unlocked": True, "current": True},
                    {"stage": 40, "description": "四十阶", "unlocked": False, "current": False},
                ],
            }
        ],
    }

    snapshot = build_magic_treasure_database_snapshot(runtime, existing, {})

    owned = snapshot["houtiangubao"][0]
    assert owned["rank"] == 40
    assert owned["original_effect"] == "完整静态效果"
    assert [(row["stage"], row["unlocked"], row["current"]) for row in owned["upgrade_effects"]] == [
        (39, True, False),
        (40, True, True),
    ]
    assert snapshot["fabao"][0]["name"] == "未拥有法宝"
    assert snapshot["runtime_item_count"] == 2
    assert snapshot["runtime_debug"]["runtime_owned_item_count"] == 1


def test_owned_only_runtime_refresh_rejects_missing_previously_owned_item() -> None:
    runtime = {
        "complete": True,
        "owned_only": True,
        "items": [{"talisman_id": 8, "name": "青竹蜂云剑"}],
    }
    existing = {
        "fabao": [
            {"talisman_id": 8, "name": "青竹蜂云剑", "owned": True},
            {"talisman_id": 9, "name": "另一件已有法宝", "owned": True},
        ]
    }

    with pytest.raises(ValueError, match="遗漏既有法宝"):
        build_magic_treasure_database_snapshot(runtime, existing, {})


def test_collector_sorts_by_owned_catalog_quality_rank_and_shenlian() -> None:
    runtime = {
        "complete": True,
        "source": "runtime_memory",
        "items": [
            {
                "talisman_id": 1,
                "name": "高阶黄色",
                "owned": True,
                "quality": 7,
                "rank": 98,
                "wujing_level": 2,
                "section_key": "fabao",
            },
            {
                "talisman_id": 2,
                "name": "低神炼彩色",
                "owned": True,
                "quality": 7,
                "rank": 50,
                "wujing_level": 1,
                "section_key": "fabao",
            },
            {
                "talisman_id": 3,
                "name": "高神炼彩色",
                "owned": True,
                "quality": 7,
                "rank": 50,
                "wujing_level": 9,
                "section_key": "fabao",
            },
            {
                "talisman_id": 4,
                "name": "未拥有荧光绿",
                "owned": False,
                "quality": 8,
                "rank": 0,
                "wujing_level": 0,
                "section_key": "fabao",
            },
        ],
    }
    knowledge = {
        1: {"catalog_quality": 5},
        2: {"catalog_quality": 7},
        3: {"catalog_quality": 7},
        4: {"catalog_quality": 8},
    }

    snapshot = build_magic_treasure_database_snapshot(runtime, talisman_knowledge=knowledge)

    assert [item["name"] for item in snapshot["fabao"]] == [
        "高神炼彩色",
        "低神炼彩色",
        "高阶黄色",
        "未拥有荧光绿",
    ]


def test_projection_builds_current_and_next_shenlian_gradient() -> None:
    items = _build_projection(
        owned_rows=[
            {
                "talisman_id": 8,
                "stage": 35,
                "wujing_level": 11,
                "mix_level": 0,
                "bind_id": 0,
                "num": 1,
            }
        ],
        talisman_configs={
            8: {
                "name": "青竹蜂云剑",
                "descript": "贯穿几率提升",
                "talismanType": 0,
                "type": 1,
            }
        },
        grade_rows={
            8: {
                1: {
                    "descript": (
                        "<color=#864c00>【古意青锋】</color>：贯穿几率提升"
                        "<color=#2a4b10>5%</color>"
                    )
                },
                9: {"descript": "【古意青锋】：试剑增强"},
                20: {"quality": 6, "descript": "灵力恢复+750"},
                21: {"quality": 6, "descript": "气血上限+90000"},
                22: {"quality": 6, "descript": "攻击资质+0.1%"},
                23: {"quality": 6, "descript": "角色永久增加灵力加成1%"},
                29: {"quality": 6, "descript": "角色永久增加攻击加成1%"},
                31: {"quality": 6, "descript": "角色永久增加攻击加成1%"},
                35: {"quality": 6, "descript": "攻击+2400"},
                40: {"quality": 6, "descript": "灵力+288000"},
                41: {"quality": 6, "descript": "功法增伤+14400"},
                45: {"quality": 6, "descript": "【古意青锋增强】：贯穿增伤再次提升"},
            }
        },
        pin_rows={
            8: {
                0: {
                    "pin": 0,
                    "baseSkillDes": "贯穿几率提升<color=#2a4b10>5%</color>",
                },
                1: {
                    "pin": 0,
                    "baseSkillDes": "激活后贯穿几率提升<color=#2a4b10>6%</color>",
                    "scheduleDes": "再点亮7个节点可突破为壹炼",
                },
                9: {
                    "pin": 1,
                    "baseSkillDes": "贯穿几率提升<color=#2a4b10>12%</color>",
                },
                11: {
                    "pin": 1,
                    "pinHanzi": "壹炼",
                    "quality": 7,
                    "baseSkillDes": "贯穿几率提升<color=#2a4b10>14%</color>",
                    "scheduleDes": "再点亮6个节点可突破为贰炼",
                },
                18: {
                    "pin": 2,
                    "baseSkillDes": "贯穿几率提升<color=#2a4b10>20%</color>",
                },
            }
        },
        key_points={
            8: [
                {
                    "pin": 1,
                    "level": 9,
                    "pinHanzi": "壹炼",
                    "skillName": "古意青锋一阶",
                    "keyPointDes": "试剑提升<color=#2a4b10>7.5%</color>贯穿增伤",
                },
                {
                    "pin": 2,
                    "level": 18,
                    "pinHanzi": "贰炼",
                    "skillName": "古意青锋二阶",
                    "keyPointDes": "试剑效果再次提升",
                },
            ]
        },
        break_nodes={8: [8, 17]},
        reader=None,  # Strings need no Lua dereference.
        lang_map={},
    )

    item = items[0]
    assert item["category"] == "后天古宝"
    assert item["original_effect"] == (
        "【古意青锋】：贯穿几率提升5%\n【古意青锋】：试剑增强\n"
        "角色永久增加攻击加成1%\n攻击+2400\n灵力+288000"
        "\n【古意青锋增强】：贯穿增伤再次提升"
    )
    assert [
        (effect["stage"], effect["description"])
        for effect in item["upgrade_effects"]
    ] == [
        (1, "【古意青锋】：贯穿几率提升5%"),
        (9, "【古意青锋】：试剑增强"),
        (31, "角色永久增加攻击加成1%"),
        (35, "攻击+2400"),
        (40, "灵力+288000"),
        (45, "【古意青锋增强】：贯穿增伤再次提升"),
    ]
    assert item["upgrade_effects"][0]["segments"] == [
        {"text": "【古意青锋】", "color": "#864c00", "role": "skill"},
        {"text": "：贯穿几率提升", "color": "", "role": ""},
        {"text": "5%", "color": "#2a4b10", "role": "value"},
    ]
    assert item["shenlian_effect"] == "贯穿几率提升14%"
    assert item["shenlian_effect_segments"][-1] == {
        "text": "14%",
        "color": "#2a4b10",
        "role": "value",
    }
    assert item["upgrade_effects"][3]["current"] is True
    assert item["upgrade_effects"][4]["unlocked"] is False
    assert item["upgrade_effects"][5]["unlocked"] is False
    assert item["shenlian_gradients"][0]["pin_label"] == "激活"
    assert item["shenlian_gradients"][0]["level"] == 1
    assert item["shenlian_gradients"][0]["summary_description"] == "激活后贯穿几率提升6%"
    assert item["shenlian_gradients"][1]["effect_segments"][1]["text"] == "7.5%"
    assert item["shenlian_gradients"][1]["summary_description"] == "贯穿几率提升14%"
    assert item["shenlian_pin"] == 1
    assert item["shenlian_progress_nodes"] == 2
    assert item["shenlian_remaining_nodes"] == 6
    assert item["shenlian_next_pin"] == 2
    assert [gradient["active"] for gradient in item["shenlian_gradients"]] == [True, True, False]
    assert [gradient["current"] for gradient in item["shenlian_gradients"]] == [False, True, False]


def test_projection_maps_spirit_position_type() -> None:
    item = _build_projection(
        owned_rows=[{"talisman_id": 75, "stage": 50, "wujing_level": 0}],
        talisman_configs={
            75: {
                "name": "观天镜",
                "talismanType": 0,
                "type": 4,
            }
        },
        grade_rows={75: {}},
        pin_rows={75: {}},
        key_points={75: []},
        break_nodes={75: []},
        reader=None,
        lang_map={},
    )[0]

    assert item["type"] == "灵力"


def test_projection_keeps_authoritative_progress_when_display_name_is_unresolved() -> None:
    item = _build_projection(
        owned_rows=[
            {
                "talisman_id": 2063,
                "stage": 35,
                "wujing_level": 8,
                "owned": True,
            }
        ],
        talisman_configs={
            2063: {
                # This models a live config localization id absent from the
                # currently exported language map.
                "name": 987654321,
                "talismanType": 0,
                "type": 1,
            }
        },
        grade_rows={2063: {35: {"quality": 7}}},
        pin_rows={2063: {}},
        key_points={2063: []},
        break_nodes={2063: []},
        reader=None,
        lang_map={},
    )[0]

    assert item["talisman_id"] == 2063
    assert item["rank"] == 35
    assert item["wujing_level"] == 8
    assert item["name"] == "法宝 #2063"
    assert item["name_resolved"] is False


def test_projection_keeps_lv1_activation_as_current_gradient_before_first_pin() -> None:
    item = _build_projection(
        owned_rows=[{"talisman_id": 45, "stage": 98, "wujing_level": 2}],
        talisman_configs={45: {"name": "狼首玉如意", "talismanType": 0, "type": 3}},
        grade_rows={45: {}},
        pin_rows={
            45: {
                0: {"pin": 0, "baseSkillDes": "（未激活）灵宝伤害加深100%"},
                1: {"pin": 0, "baseSkillDes": "灵宝伤害加深100%"},
                2: {"pin": 0, "baseSkillDes": "灵宝伤害加深110%"},
                9: {"pin": 1, "pinHanzi": "壹", "baseSkillDes": "灵宝伤害加深170%"},
            }
        },
        key_points={
            45: [
                {
                    "pin": 1,
                    "level": 9,
                    "pinHanzi": "壹",
                    "skillName": "【狼影吞天】一阶",
                    "keyPointDes": "每次获得的层数提升至2层",
                }
            ]
        },
        break_nodes={45: [8]},
        reader=None,
        lang_map={},
    )[0]

    assert [(row["pin_label"], row["level"]) for row in item["shenlian_gradients"]] == [
        ("激活", 1),
        ("壹", 9),
    ]
    assert [row["active"] for row in item["shenlian_gradients"]] == [True, False]
    assert [row["current"] for row in item["shenlian_gradients"]] == [True, False]
    assert item["shenlian_pin_label"] == "激活"
    assert item["shenlian_next_pin"] == 1


def test_projection_starts_shenlian_progression_at_locked_lv1() -> None:
    item = _build_projection(
        owned_rows=[{"talisman_id": 45, "stage": 98, "wujing_level": 0}],
        talisman_configs={45: {"name": "狼首玉如意", "talismanType": 0, "type": 3}},
        grade_rows={45: {}},
        pin_rows={
            45: {
                0: {"pin": 0, "baseSkillDes": "（未激活）灵宝伤害加深100%"},
                1: {"pin": 0, "baseSkillDes": "灵宝伤害加深100%"},
                9: {"pin": 1, "pinHanzi": "壹", "baseSkillDes": "灵宝伤害加深170%"},
            }
        },
        key_points={
            45: [
                {
                    "pin": 1,
                    "level": 9,
                    "pinHanzi": "壹",
                    "skillName": "【狼影吞天】一阶",
                    "keyPointDes": "每次获得的层数提升至2层",
                }
            ]
        },
        break_nodes={45: [8]},
        reader=None,
        lang_map={},
    )[0]

    assert [(row["pin_label"], row["level"]) for row in item["shenlian_gradients"]] == [
        ("激活", 1),
        ("壹", 9),
    ]
    assert [row["active"] for row in item["shenlian_gradients"]] == [False, False]
    assert [row["current"] for row in item["shenlian_gradients"]] == [False, False]
    assert item["shenlian_next_level"] == 1
    assert item["shenlian_remaining_nodes"] == 1


def test_projection_includes_unowned_configs_but_sorts_them_after_owned_items() -> None:
    projection_rows = _complete_talisman_rows(
        [{"talisman_id": 1, "stage": 20, "wujing_level": 0}],
        {1: {"name": "已有法宝"}, 2: {"name": "未有高品质法宝"}},
    )
    assert projection_rows == [
        {
            "talisman_id": 1,
            "stage": 20,
            "wujing_level": 0,
            "mix_level": 0,
            "bind_id": 0,
            "num": 0,
            "owned": True,
        },
        {
            "talisman_id": 2,
            "stage": 0,
            "wujing_level": 0,
            "mix_level": 0,
            "bind_id": 0,
            "num": 0,
            "owned": False,
        },
    ]

    items = _build_projection(
        owned_rows=projection_rows,
        talisman_configs={
            1: {"name": "已有法宝", "talismanType": 0, "type": 1},
            2: {"name": "未有高品质法宝", "talismanType": 0, "type": 3},
        },
        grade_rows={
            1: {20: {"quality": 4, "descript": "攻击+100"}},
            2: {1: {"quality": 6, "descript": "【潜在能力】：高品质效果"}},
        },
        pin_rows={1: {}, 2: {}},
        key_points={1: [], 2: []},
        break_nodes={1: [], 2: []},
        reader=None,
        lang_map={},
    )

    assert [item["name"] for item in items] == ["已有法宝", "未有高品质法宝"]
    assert items[1]["owned"] is False
    assert items[1]["rank"] == 0
    assert items[1]["quality"] == 6
    assert items[1]["original_effect"] == "【潜在能力】：高品质效果"


@pytest.mark.parametrize(
    ("raw_type", "expected_label"),
    [(1, "攻击"), (2, "辅助"), (3, "防御")],
)
def test_projection_maps_runtime_bag_type(raw_type: int, expected_label: str) -> None:
    items = _build_projection(
        owned_rows=[
            {
                "talisman_id": 45,
                "stage": 98,
                "wujing_level": 0,
                "mix_level": 0,
                "bind_id": 0,
                "num": 1,
            }
        ],
        talisman_configs={
            45: {
                "name": "狼首玉如意",
                "descript": "防御类法宝",
                "talismanType": 0,
                "type": raw_type,
            }
        },
        grade_rows={45: {98: {"quality": 6}}},
        pin_rows={45: {}},
        key_points={45: []},
        break_nodes={45: []},
        reader=None,
        lang_map={},
    )

    assert items[0]["type"] == expected_label


def test_runtime_localization_keeps_numeric_format_arguments_raw() -> None:
    class Reader:
        def table(self, _address: int) -> dict:
            return {
                "array": [None, 27588, 14, 1, 7.5, 0.5],
                "fields": {},
            }

    text = _localized_text(
        LuaRef(kind="table", address=123),
        reader=Reader(),
        lang_map={
            1: "不应作为参数翻译",
            14: "也不应作为参数翻译",
            27588: "几率%s%%(+%s%%)，提升%s%%(+%s%%)",
        },
    )

    assert text == "几率14%(+1%)，提升7.5%(+0.5%)"
