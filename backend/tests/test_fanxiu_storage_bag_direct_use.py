from __future__ import annotations

from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation.tasks.storage_bag_direct_use import (
    StorageBagDirectUseBlocked,
    StorageBagDirectUseRequest,
    StorageBagSpiritStoneGuiAdapter,
    parse_exact_positive_quantity,
    verify_spirit_stone_direct_use_delta,
)
from backend.core.fanxiu.runtime_gui.storage_bag_alignment import (
    StorageBagItemClickPlan,
)


REQUEST = StorageBagDirectUseRequest(1001, "stone", "灵石", 57_810)


def _bag(
    items: list[tuple[str, int, int]],
    fingerprint: str,
    *,
    pid: int = 123,
) -> dict:
    return {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "fingerprint": fingerprint,
        "evidence": {"pid": pid, "process_start_ticks": 456},
        "items": [
            {
                "ui_index": index,
                "instance_id": instance_id,
                "base_id": base_id,
                "num": quantity,
                "is_padding": False,
            }
            for index, (instance_id, base_id, quantity) in enumerate(items)
        ],
    }


def _wallet(amount: int, captured_at: str, *, pid: int = 123) -> dict:
    return {
        "source": "runtime_memory",
        "currency_type": 1,
        "exchange_currency": amount,
        "captured_at": captured_at,
        "evidence": {"pid": pid, "process_start_ticks": 456},
    }


BEFORE = _bag([("stone", 1001, 57_810), ("other", 200, 3)], "before")
AFTER = _bag([("other", 200, 3)], "after")
WALLET_BEFORE = _wallet(1_000, "2026-08-20T00:30:00+08:00")
WALLET_AFTER = _wallet(58_810, "2026-08-20T00:30:02+08:00")


def _ready(*_args) -> StorageBagItemClickPlan:
    return StorageBagItemClickPlan(
        "ready",
        "unique",
        runtime_index=0,
        runtime_item={
            "instance_id": "stone",
            "base_id": 1001,
            "num": 57_810,
        },
        point=(365.0, 712.0),
        viewport_runtime_start=0,
    )


def _consume(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


class _Runtime:
    def __init__(
        self,
        *,
        detail_held: str = "57810",
        quantity_held: str = "持有数量57810",
        current_quantity: str = "57810",
        quantity_title: str = "灵石",
    ) -> None:
        self.detail_held = detail_held
        self.quantity_held = quantity_held
        self.current_quantity = current_quantity
        self.quantity_title = quantity_title
        self.events: list[tuple] = []

    def wait_action_settle(self, seconds):
        self.events.append(("settle", seconds))
        if False:
            yield None

    def click_frame_point(self, scene, x, y):
        self.events.append(("point", scene, x, y))

    def wait_view(self, *scenes, **_options):
        self.events.append(("view", scenes))
        if False:
            yield None
        return 525 if scenes == (525, 578) else scenes[0]

    def cur_frame(self, update=False):
        self.events.append(("frame", update))
        return "frame"

    def ocr_tokens_in_shapes(self, scene, shapes, **_options):
        shape = shapes[0]
        if shape == "物品标题":
            text = "灵石" if scene == 610 else self.quantity_title
        elif shape == "持有数量":
            text = self.detail_held if scene == 610 else self.quantity_held
        elif shape == "当前数量":
            text = self.current_quantity
        else:
            raise AssertionError((scene, shape))
        return [{"text": text, "x": 1, "y": 1}]

    def wait_click(self, scene, shape, **_options):
        self.events.append(("click", scene, shape))
        if False:
            yield None


def test_exact_transaction_accepts_only_bag_minus_n_and_wallet_plus_n() -> None:
    result = verify_spirit_stone_direct_use_delta(
        BEFORE,
        AFTER,
        request=REQUEST,
        wallet_before=WALLET_BEFORE,
        wallet_after=WALLET_AFTER,
    )

    assert result.consumed_quantity == 57_810
    assert result.wallet_delta == 57_810
    assert result.before_fingerprint == "before"
    assert result.after_fingerprint == "after"


@pytest.mark.parametrize(
    ("after", "wallet_after", "message"),
    [
        (
            _bag([("stone", 1001, 1), ("other", 200, 3)], "partial"),
            WALLET_AFTER,
            "未精确减少",
        ),
        (
            _bag([("other", 200, 4)], "other-changed"),
            WALLET_AFTER,
            "其它背包实例",
        ),
        (
            AFTER,
            _wallet(58_809, "2026-08-20T00:30:02+08:00"),
            "未精确增加",
        ),
        (
            _bag([("other", 200, 3)], "new-process", pid=999),
            WALLET_AFTER,
            "不是同一游戏进程",
        ),
        (
            AFTER,
            _wallet(58_810, "2026-08-20T00:29:59+08:00"),
            "时间发生倒退",
        ),
    ],
)
def test_transaction_fails_closed_on_any_non_exact_delta(
    after: dict, wallet_after: dict, message: str
) -> None:
    with pytest.raises(StorageBagDirectUseBlocked, match=message):
        verify_spirit_stone_direct_use_delta(
            BEFORE,
            after,
            request=REQUEST,
            wallet_before=WALLET_BEFORE,
            wallet_after=wallet_after,
        )


def test_quantity_parser_requires_label_when_contract_has_one() -> None:
    assert parse_exact_positive_quantity(
        [{"text": "持有数量", "x": 1, "y": 1}, {"text": "57810", "x": 2, "y": 1}],
        label="持有数量",
    ) == 57_810
    with pytest.raises(StorageBagDirectUseBlocked, match="缺少标签"):
        parse_exact_positive_quantity(
            [{"text": "57810", "x": 1, "y": 1}], label="持有数量"
        )


def test_gui_adapter_verifies_610_and_584_before_final_use() -> None:
    bag_snapshots = iter((BEFORE, BEFORE, AFTER))
    wallet_snapshots = iter((WALLET_BEFORE, WALLET_AFTER))
    runtime = _Runtime()
    adapter = StorageBagSpiritStoneGuiAdapter(
        runtime=runtime,
        snapshot_reader=lambda: next(bag_snapshots),
        wallet_snapshot_reader=lambda currency_type: (
            next(wallet_snapshots) if currency_type == 1 else None
        ),
        click_planner=_ready,
        clock=lambda: datetime.fromisoformat("2026-08-20T00:30:03+08:00"),
    )

    result = _consume(adapter.execute(REQUEST))

    assert result.wallet_delta == 57_810
    assert result.detail_observed_name == "灵石"
    assert result.quantity_observed_name == "灵石"
    assert ("point", 525, 365.0, 712.0) in runtime.events
    assert ("click", 610, "使用（高风险）") in runtime.events
    assert ("click", 584, "使用") in runtime.events


@pytest.mark.parametrize(
    "runtime",
    [
        _Runtime(detail_held="57809"),
        _Runtime(quantity_held="持有数量57809"),
        _Runtime(current_quantity="57809"),
        _Runtime(quantity_title="VIP经验"),
    ],
)
def test_gui_contract_mismatch_never_reaches_final_584_use(runtime: _Runtime) -> None:
    adapter = StorageBagSpiritStoneGuiAdapter(
        runtime=runtime,
        snapshot_reader=lambda: BEFORE,
        wallet_snapshot_reader=lambda _currency_type: WALLET_BEFORE,
        click_planner=_ready,
        clock=lambda: datetime.fromisoformat("2026-08-20T00:30:03+08:00"),
    )

    with pytest.raises(StorageBagDirectUseBlocked):
        _consume(adapter.execute(REQUEST))

    assert ("click", 584, "使用") not in runtime.events


def test_changed_pre_click_backpack_invalidates_the_planned_coordinate() -> None:
    snapshots = iter((BEFORE, _bag([("stone", 1001, 57_809)], "changed")))
    runtime = _Runtime()
    adapter = StorageBagSpiritStoneGuiAdapter(
        runtime=runtime,
        snapshot_reader=lambda: next(snapshots),
        wallet_snapshot_reader=lambda _currency_type: WALLET_BEFORE,
        click_planner=_ready,
        clock=lambda: datetime.fromisoformat("2026-08-20T00:30:03+08:00"),
    )

    with pytest.raises(StorageBagDirectUseBlocked, match="旧计划失效"):
        _consume(adapter.execute(REQUEST))

    assert not any(event[0] == "point" for event in runtime.events)


def test_stale_wallet_baseline_blocks_before_the_first_click() -> None:
    snapshots = iter((BEFORE, BEFORE))
    stale_wallet = _wallet(1_000, "2026-08-20T00:00:00+08:00")
    runtime = _Runtime()
    adapter = StorageBagSpiritStoneGuiAdapter(
        runtime=runtime,
        snapshot_reader=lambda: next(snapshots),
        wallet_snapshot_reader=lambda _currency_type: stale_wallet,
        click_planner=_ready,
        clock=lambda: datetime.fromisoformat("2026-08-20T00:30:03+08:00"),
    )

    with pytest.raises(StorageBagDirectUseBlocked, match="不新鲜"):
        _consume(adapter.execute(REQUEST))

    assert not any(event[0] == "point" for event in runtime.events)


def test_adapter_is_not_added_to_the_production_whitelist() -> None:
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_execution import (
        SUPPORTED_PRODUCTION_TEMPLATES,
    )

    assert "direct_use" not in SUPPORTED_PRODUCTION_TEMPLATES
