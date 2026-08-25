from __future__ import annotations

import json

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_plan import (
    StorageBagAutoClaimBlocked,
    build_storage_bag_auto_claim_plan,
    dispatch_storage_bag_auto_claim_plan,
    load_storage_bag_auto_claim_plan,
)
from backend.core.fanxiu.storage_bag_settings import set_storage_bag_auto_claim
from backend.models import FanxiuStorageBagItemSetting
from backend.core.fanxiu.storage_bag_usage import storage_bag_analysis_fingerprint


def _row(
    base_id: int,
    *,
    order: int,
    name: str,
    type_name: str,
    operation_template: str,
    yield_mode: str = "none",
    note: str = "",
    auto_claim: bool = True,
    effect: str = "",
    can_use: int = 1,
) -> dict:
    row = {
        "atlas_order": order,
        "base_id": base_id,
        "auto_claim": auto_claim,
        "note": note,
        "analysis_status": "classified",
        "operation_template": operation_template,
        "yield_mode": yield_mode,
        "analysis_reason": "test persisted classification",
        "item": {
            "name": name,
            "type_name": type_name,
            "effect_description": effect,
            "can_use": can_use,
        },
    }
    row["analysis_fingerprint"] = storage_bag_analysis_fingerprint(row)
    return row


def _runtime(*items: dict) -> dict:
    return {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "fingerprint": "bag-live-1",
        "items": list(items),
    }


def _instance(ui_index: int, base_id: int, instance_id: str, num: int) -> dict:
    return {
        "ui_index": ui_index,
        "base_id": base_id,
        "instance_id": instance_id,
        "num": num,
    }


def test_plan_uses_live_ui_order_and_unique_instances_while_routing_npc_gifts():
    atlas = {
        "items": [
            _row(10, order=1, name="随机匣", type_name="礼包宝匣", operation_template="random_box", yield_mode="random", effect="随机获得"),
            _row(20, order=2, name="固定匣", type_name="礼包宝匣", operation_template="fixed_box", yield_mode="fixed", effect="获得以下道具"),
            _row(30, order=3, name="炎帝法印", type_name="NPC礼物", operation_template="npc_gift", note="去仙缘送礼"),
        ]
    }
    plan = build_storage_bag_auto_claim_plan(
        atlas,
        _runtime(
            _instance(0, 20, "fixed-a", 2),
            _instance(1, 30, "gift-a", 8),
            _instance(2, 10, "random-a", 3),
            _instance(3, 10, "random-b", 4),
        ),
    )

    assert plan.ready
    assert [(item.base_id, item.instance_id) for item in plan.action_queue] == [
        (20, "fixed-a"),
        (10, "random-a"),
        (10, "random-b"),
    ]
    assert [item.template for item in plan.action_queue] == [
        "open_fixed_box",
        "open_random_box",
        "open_random_box",
    ]
    assert len(plan.routed) == 1
    assert plan.routed[0].base_id == 30
    assert plan.routed[0].external_route == "xianyuan_auto_gift"


def test_plan_defers_absent_and_unproven_conditional_items_but_fails_missing_choice_policy():
    atlas = {
        "items": [
            _row(10, order=1, name="未来随机匣", type_name="礼包宝匣", operation_template="random_box", yield_mode="random", effect="随机获得"),
            _row(
                11,
                order=2,
                name="洗灵随机匣",
                type_name="礼包宝匣",
                operation_template="random_box",
                yield_mode="random",
                effect="随机获得",
                note="洗灵祈愿周使用",
            ),
            _row(12, order=3, name="仙侣自选匣", type_name="自选匣", operation_template="choice_box"),
        ]
    }
    plan = build_storage_bag_auto_claim_plan(
        atlas,
        _runtime(
            _instance(0, 11, "conditional", 9),
            _instance(1, 12, "choice", 1),
        ),
    )

    assert not plan.ready
    assert [(item.base_id, item.reason) for item in plan.deferred] == [
        (10, "当前完整 Runtime 中没有该图鉴物品"),
        (11, "备注包含活动时机；必须由权威活动 Runtime 证明当前窗口"),
    ]
    assert len(plan.failures) == 1
    assert plan.failures[0].base_id == 12
    assert "自选匣备注为空" in plan.failures[0].reason


def test_plan_rejects_duplicate_runtime_instance_identity():
    atlas = {
        "items": [
            _row(10, order=1, name="随机匣", type_name="礼包宝匣", operation_template="random_box", yield_mode="random", effect="随机获得"),
        ]
    }
    plan = build_storage_bag_auto_claim_plan(
        atlas,
        _runtime(
            _instance(0, 10, "same", 1),
            _instance(1, 10, "same", 2),
        ),
    )

    assert not plan.ready
    assert [(item.instance_id, item.disposition) for item in plan.action_queue] == [
        ("same", "action")
    ]
    assert plan.failures[0].instance_id == "same"
    assert "缺失或重复" in plan.failures[0].reason


def test_dispatch_validates_all_adapters_before_the_first_callback():
    atlas = {
        "items": [
            _row(10, order=1, name="随机匣", type_name="礼包宝匣", operation_template="random_box", yield_mode="random", effect="随机获得"),
            _row(20, order=2, name="固定匣", type_name="礼包宝匣", operation_template="fixed_box", yield_mode="fixed"),
        ]
    }
    plan = build_storage_bag_auto_claim_plan(
        atlas,
        _runtime(
            _instance(0, 10, "random", 1),
            _instance(1, 20, "fixed", 1),
        ),
    )
    called: list[int] = []

    with pytest.raises(StorageBagAutoClaimBlocked, match="open_fixed_box"):
        dispatch_storage_bag_auto_claim_plan(
            plan,
            {"open_random_box": lambda entry: called.append(entry.base_id)},
        )

    assert called == []


def test_dispatch_runs_physical_queue_when_every_adapter_exists():
    atlas = {
        "items": [
            _row(10, order=1, name="随机匣", type_name="礼包宝匣", operation_template="random_box", yield_mode="random", effect="随机获得"),
        ]
    }
    plan = build_storage_bag_auto_claim_plan(
        atlas,
        _runtime(_instance(0, 10, "random", 2)),
    )

    result = dispatch_storage_bag_auto_claim_plan(
        plan,
        {"open_random_box": lambda entry: (entry.instance_id, entry.quantity)},
    )

    assert result == (("random", 2),)


def test_load_plan_merges_database_flags_into_the_cumulative_atlas(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    atlas_path = tmp_path / "storage_bag_atlas.json"
    atlas_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "updated_at": "test",
                "items": [
                    _row(
                        10,
                        order=1,
                        name="随机匣",
                        type_name="礼包宝匣",
                        operation_template="random_box",
                        yield_mode="random",
                        effect="随机获得",
                        auto_claim=False,
                    ),
                    _row(
                        20,
                        order=2,
                        name="未勾选匣",
                        type_name="礼包宝匣",
                        operation_template="fixed_box",
                        yield_mode="fixed",
                        auto_claim=True,
                    ),
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with Session(engine) as session:
        set_storage_bag_auto_claim(session, base_id=10, auto_claim=True)
        setting = session.get(FanxiuStorageBagItemSetting, 10)
        assert setting is not None
        setting.analysis_status = "classified"
        setting.operation_template = "random_box"
        setting.yield_mode = "random"
        setting.analysis_reason = "test persisted classification"
        atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
        setting.analysis_fingerprint = atlas["items"][0]["analysis_fingerprint"]
        session.add(setting)
        session.commit()
        plan = load_storage_bag_auto_claim_plan(
            session,
            _runtime(_instance(0, 10, "selected", 2)),
            atlas_path=atlas_path,
        )

    assert plan.selected_base_count == 1
    assert [(item.base_id, item.instance_id) for item in plan.action_queue] == [
        (10, "selected")
    ]


def test_load_plan_persists_fresh_classification_without_a_prior_wiki_request(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    atlas_path = tmp_path / "storage_bag_atlas.json"
    raw_row = {
        "atlas_order": 1,
        "base_id": 10,
        "item": {
            "name": "新获得的随机匣",
            "type_name": "礼包宝匣",
            "effect_description": "随机获得以下道具",
            "can_use": 1,
        },
    }
    atlas_path.write_text(
        json.dumps(
            {"schema_version": 2, "updated_at": "test", "items": [raw_row]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with Session(engine) as session:
        set_storage_bag_auto_claim(session, base_id=10, auto_claim=True)
        session.commit()
        plan = load_storage_bag_auto_claim_plan(
            session,
            _runtime(_instance(0, 10, "new-random", 3)),
            atlas_path=atlas_path,
        )

    assert plan.ready
    assert plan.action_queue[0].template == "open_random_box"
    assert plan.action_queue[0].reward_mode == "random"
    with Session(engine) as session:
        setting = session.get(FanxiuStorageBagItemSetting, 10)
        assert setting is not None
        assert setting.analysis_status == "classified"
        assert setting.operation_template == "random_box"
        assert setting.yield_mode == "random"
        assert setting.analysis_fingerprint == storage_bag_analysis_fingerprint(raw_row)


def test_plan_does_not_reclassify_from_misleading_name_type_or_effect_text():
    atlas = {
        "items": [
            _row(
                10,
                order=1,
                name="看起来像自选随机礼物匣",
                type_name="NPC礼物",
                operation_template="fixed_box",
                yield_mode="fixed",
                effect="随机获得任一种道具并赠送 NPC",
            )
        ]
    }

    plan = build_storage_bag_auto_claim_plan(
        atlas,
        _runtime(_instance(0, 10, "persisted-fixed", 1)),
    )

    assert plan.ready
    assert plan.routed == ()
    assert plan.action_queue[0].template == "open_fixed_box"
    assert plan.action_queue[0].reward_mode == "fixed"


@pytest.mark.parametrize(
    "snapshot",
    [
        {"complete": False, "source": "active_backpack_panel_item_info_list", "items": []},
        {"complete": True, "source": "legacy_packet", "items": []},
    ],
)
def test_plan_refuses_incomplete_or_non_panel_runtime(snapshot):
    with pytest.raises(ValueError):
        build_storage_bag_auto_claim_plan({"items": []}, snapshot)
