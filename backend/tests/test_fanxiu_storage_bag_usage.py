from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine, select

from backend.core.fanxiu.storage_bag_settings import apply_storage_bag_item_settings
from backend.core.fanxiu.storage_bag_usage import (
    analyze_storage_bag_item,
    derive_storage_bag_open_delta,
    ensure_storage_bag_item_analysis,
    record_storage_bag_open_event,
)
from backend.models import (
    FanxiuStorageBagItemSetting,
    FanxiuStorageBagOpenEvent,
    FanxiuStorageBagYieldAggregate,
)


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[
        FanxiuStorageBagItemSetting.__table__,
        FanxiuStorageBagOpenEvent.__table__,
        FanxiuStorageBagYieldAggregate.__table__,
    ])
    return Session(engine)


def _row(base_id: int, name: str, type_name: str, **item_overrides):
    return {
        "base_id": base_id,
        "item": {
            "name": name,
            "type_name": type_name,
            **item_overrides,
        },
    }


def test_catalog_analysis_uses_reusable_semantic_templates() -> None:
    assert analyze_storage_bag_item(
        _row(1, "仙币灵石随机匣", "礼包宝匣", effect_description="随机获得奖励")
    ).operation_template == "random_box"
    assert analyze_storage_bag_item(
        _row(2, "仙府甄选礼", "礼包宝匣", effect_description="获得以下道具")
    ).operation_template == "fixed_box"
    assert analyze_storage_bag_item(
        _row(
            20,
            "仙府甄选礼",
            "礼包宝匣",
            effect_description="获得以下道具",
            effect_detail_preview="奖励列表：九曜玄墨随机匣 x1",
        )
    ).operation_template == "fixed_box"
    assert analyze_storage_bag_item(
        _row(3, "誓约自选匣", "自选匣")
    ).operation_template == "choice_box"
    assert analyze_storage_bag_item(
        _row(4, "炎帝法印", "NPC礼物")
    ).operation_template == "npc_gift"
    assert analyze_storage_bag_item(
        _row(5, "异变弟子拜师函", "礼包宝匣", effect_description="随机弟子")
    ).operation_template == "special_use"
    assert analyze_storage_bag_item(
        _row(6, "VIP经验", "货币", can_use=True)
    ).operation_template == "direct_use"
    assert analyze_storage_bag_item(
        _row(7, "通用可用道具", "道具", can_use=1)
    ).operation_template == "direct_use"


def test_analysis_is_reused_until_catalog_fingerprint_changes() -> None:
    with _session() as session:
        row = _row(10, "随机宝箱", "礼包宝匣", effect_description="随机获得")
        first = ensure_storage_bag_item_analysis(session, row)
        first_analyzed_at = first.analyzed_at
        first_fingerprint = first.analysis_fingerprint
        second = ensure_storage_bag_item_analysis(session, row)
        assert second.analyzed_at == first_analyzed_at
        assert second.operation_template == "random_box"

        changed = _row(10, "固定宝箱", "礼包宝匣", effect_description="获得以下道具")
        third = ensure_storage_bag_item_analysis(session, changed)
        assert third.operation_template == "fixed_box"
        assert third.analysis_fingerprint != first_fingerprint


def test_verified_events_are_idempotent_and_render_cumulative_average() -> None:
    with _session() as session:
        ensure_storage_bag_item_analysis(
            session,
            _row(390051001, "仙币灵石随机匣", "礼包宝匣", effect_description="随机获得"),
        )
        first = record_storage_bag_open_event(
            session,
            action_key="batch-1",
            base_id=390051001,
            operation_template="random_box",
            opened_count=10,
            rewards=[
                {"item_id": 2, "name": "仙币", "quantity": 221},
                {"item_id": 1, "name": "灵石", "quantity": 8673},
            ],
            runtime_before_fingerprint="before-1",
            runtime_after_fingerprint="after-1",
        )
        assert first.average_yield == "仙币22.1，灵石867.3"

        duplicate = record_storage_bag_open_event(
            session,
            action_key="batch-1",
            base_id=390051001,
            operation_template="random_box",
            opened_count=10,
            rewards=[{"item_id": 1, "name": "灵石", "quantity": 9999}],
            runtime_before_fingerprint="before-1",
            runtime_after_fingerprint="after-1",
        )
        assert duplicate.opened_count == 10

        second = record_storage_bag_open_event(
            session,
            action_key="batch-2",
            base_id=390051001,
            operation_template="random_box",
            opened_count=2,
            rewards=[
                {"item_id": 2, "name": "仙币", "quantity": 45},
                {"item_id": 1, "name": "灵石", "quantity": 1735},
            ],
            runtime_before_fingerprint="before-2",
            runtime_after_fingerprint="after-2",
        )
        assert second.opened_count == 12
        assert second.average_yield == "仙币22.17，灵石867.33"
        assert len(session.exec(select(FanxiuStorageBagOpenEvent)).all()) == 2

        atlas = apply_storage_bag_item_settings(
            session,
            {"items": [{"base_id": 390051001, "average_yield": "stale"}]},
        )
        assert atlas["items"][0]["average_yield"] == "仙币22.17，灵石867.33"
        assert atlas["items"][0]["yield_sample_count"] == 12


def test_unverified_or_non_statistical_events_fail_closed() -> None:
    with _session() as session:
        try:
            record_storage_bag_open_event(
                session,
                action_key="bad",
                base_id=1,
                operation_template="choice_box",
                opened_count=1,
                rewards=[{"name": "灵石", "quantity": 1}],
                runtime_before_fingerprint="",
                runtime_after_fingerprint="after",
            )
        except ValueError as exc:
            assert "不产生可统计" in str(exc)
        else:
            raise AssertionError("choice_box must not enter the yield ledger")


def test_yield_ledger_requires_the_persisted_classification_to_match() -> None:
    with _session() as session:
        ensure_storage_bag_item_analysis(
            session,
            _row(8, "固定礼包", "礼包宝匣", effect_description="获得以下道具"),
        )
        try:
            record_storage_bag_open_event(
                session,
                action_key="wrong-template",
                base_id=8,
                operation_template="random_box",
                opened_count=1,
                rewards=[{"name": "灵石", "quantity": 1}],
                runtime_before_fingerprint="before",
                runtime_after_fingerprint="after",
            )
        except ValueError as exc:
            assert "持久化物品分类" in str(exc)
        else:
            raise AssertionError("mismatched persisted classification must fail closed")


def test_yield_ledger_refuses_a_matching_template_when_db_yield_mode_is_none() -> None:
    with _session() as session:
        setting = ensure_storage_bag_item_analysis(
            session,
            _row(9, "随机礼包", "礼包宝匣", effect_description="随机获得以下道具"),
        )
        assert setting.operation_template == "random_box"
        setting.yield_mode = "none"
        session.add(setting)
        session.flush()
        try:
            record_storage_bag_open_event(
                session,
                action_key="db-yield-mode-none",
                base_id=9,
                operation_template="random_box",
                opened_count=1,
                rewards=[{"name": "灵石", "quantity": 1}],
                runtime_before_fingerprint="before",
                runtime_after_fingerprint="after",
            )
        except ValueError as exc:
            assert "持久化物品分类或收益模式" in str(exc)
        else:
            raise AssertionError("DB yield_mode=none must block average-yield accounting")


def test_open_delta_uses_instance_decrement_and_aggregated_reward_increase() -> None:
    before = {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "fingerprint": "before",
        "items": [
            {"instance_id": "box", "base_id": 10, "num": 5},
            {"instance_id": "coin-a", "base_id": 20, "num": 7},
            {"instance_id": "stone", "base_id": 30, "num": 2},
        ],
    }
    after = {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "fingerprint": "after",
        "items": [
            {"instance_id": "box", "base_id": 10, "num": 3},
            {"instance_id": "coin-a", "base_id": 20, "num": 9},
            {"instance_id": "coin-b", "base_id": 20, "num": 4},
            {"instance_id": "stone", "base_id": 30, "num": 3},
        ],
    }

    delta = derive_storage_bag_open_delta(
        before,
        after,
        target_base_id=10,
        target_instance_id="box",
        catalog_cards_by_id={"20": {"name": "仙币"}, "30": {"name": "灵石"}},
    )

    assert delta.opened_count == 2
    assert delta.rewards == (
        {"item_id": 20, "name": "仙币", "quantity": 6},
        {"item_id": 30, "name": "灵石", "quantity": 1},
    )


def test_open_delta_merges_verified_non_backpack_reward_increase() -> None:
    before = {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "fingerprint": "before",
        "items": [{"instance_id": "box", "base_id": 10, "num": 2}],
    }
    after = {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "fingerprint": "after",
        "items": [],
    }

    delta = derive_storage_bag_open_delta(
        before,
        after,
        target_base_id=10,
        target_instance_id="box",
        catalog_cards_by_id={},
        additional_rewards=[
            {"item_id": 1, "name": "灵石", "quantity": 176},
            {"item_id": 1, "name": "灵石", "quantity": 24},
        ],
    )

    assert delta.opened_count == 2
    assert delta.rewards == ({"item_id": 1, "name": "灵石", "quantity": 200},)


def test_open_delta_keeps_same_numeric_backpack_and_wallet_ids_separate() -> None:
    before = {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "fingerprint": "before",
        "items": [{"instance_id": "box", "base_id": 10, "num": 1}],
    }
    after = {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "fingerprint": "after",
        "items": [{"instance_id": "spirit", "base_id": 1001, "num": 1140}],
    }

    delta = derive_storage_bag_open_delta(
        before,
        after,
        target_base_id=10,
        target_instance_id="box",
        catalog_cards_by_id={"1001": {"name": "灵石"}},
        additional_rewards=[
            {
                "reward_key": "wallet:1001",
                "item_id": 1001,
                "name": "充值代币(6元)",
                "quantity": 6,
            }
        ],
    )

    assert delta.rewards == (
        {"item_id": 1001, "name": "灵石", "quantity": 1140},
        {
            "item_id": 1001,
            "name": "充值代币(6元)",
            "quantity": 6,
            "reward_key": "wallet:1001",
        },
    )


def test_open_delta_rejects_unrelated_consumption() -> None:
    before = {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "fingerprint": "before",
        "items": [
            {"instance_id": "box", "base_id": 10, "num": 2},
            {"instance_id": "other", "base_id": 20, "num": 2},
        ],
    }
    after = {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "fingerprint": "after",
        "items": [
            {"instance_id": "box", "base_id": 10, "num": 1},
            {"instance_id": "other", "base_id": 20, "num": 1},
            {"instance_id": "reward", "base_id": 30, "num": 1},
        ],
    }

    try:
        derive_storage_bag_open_delta(
            before,
            after,
            target_base_id=10,
            target_instance_id="box",
            catalog_cards_by_id={"30": {"name": "灵石"}},
        )
    except ValueError as exc:
        assert "非目标物品" in str(exc)
    else:
        raise AssertionError("unrelated consumption must fail closed")
