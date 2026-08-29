import threading
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.core.fanxiu.data_annotation.tasks import tiandi_yiju_tail
from backend.core.fanxiu.data_annotation.tasks import tiandi_yiju as tiandi_yiju_task
from backend.core.fanxiu.data_annotation.tasks.exchange_tail_planning import (
    ExchangeTailPurchase,
)


TZ = ZoneInfo("Asia/Shanghai")


def _run(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stopped:
            return stopped.value


def _occurrence() -> RankingOccurrence:
    return RankingOccurrence(
        activity_type="tiandi-yiju",
        family="gameplay_rank",
        runtime_id="8090001400004",
        activity_id=8090001,
        start_at=datetime(2026, 8, 27, 10, tzinfo=TZ),
        end_at=datetime(2026, 8, 27, 22, tzinfo=TZ),
        prepare_at=datetime(2026, 8, 26, 10, tzinfo=TZ),
        close_at=datetime(2026, 8, 30, 23, 59, 59, tzinfo=TZ),
        cross_count=1,
    )


def _detail(*, wallet: int, purchased: int):
    row = SimpleNamespace(
        goods_id=1009922,
        source_order=1,
        name="诛首秘法残页",
        token_cost=50,
        purchase_limit=1,
        purchased_count=purchased,
    )
    return SimpleNamespace(
        id="activity-1",
        game_activity_id=8090001,
        current_currency=wallet,
        shop_items=[row],
        exchange_plan={"budget_ready": True},
    )


class _Shape:
    def __init__(self, box):
        self._box = box

    def box(self):
        return self._box


class _Runtime:
    def __init__(self):
        self.clicks = []

    def view(self, _scene):
        shapes = {
            "商品列表": _Shape({"x": 50, "y": 300, "w": 800, "h": 1000}),
            **{
                f"商品行{slot}": _Shape(
                    {"x": 50, "y": 300 + (slot - 1) * 180, "w": 800, "h": 160}
                )
                for slot in range(1, 6)
            },
        }
        return SimpleNamespace(get_shape=lambda name: shapes.get(name))

    def full_frame_ocr_tokens(self, **_kwargs):
        return [
            {"parent_line_id": "name", "text": "诛首秘法残页", "x": 210, "y": 330, "w": 180, "h": 30},
            {"parent_line_id": "price", "text": "50", "x": 220, "y": 385, "w": 40, "h": 25},
        ]

    def click_shape_center(self, scene, shape):
        self.clicks.append((scene, shape))

    def click_shape_center_fast(self, scene, shape):
        self.clicks.append((scene, shape))

    def click_frame_point(self, scene, x, y):
        self.clicks.append((scene, x, y))

    def drag_frame_point(self, *_args, **_kwargs):
        pass

    def wait_action_settle(self, _seconds):
        if False:
            yield None

    def wait_view(self, *_args, **_kwargs):
        if False:
            yield None

    def goto_view(self, scene):
        self.clicks.append(("goto", scene))
        if False:
            yield None

    def ocr_text_in_shapes(self, *_args, **_kwargs):
        return "诛首秘法残页"

    def ocr_numbers_in_shapes(self, *_args, **_kwargs):
        return [50], "50"


def test_exchange_tail_buys_once_and_persists_wallet_and_count(monkeypatch) -> None:
    occurrence = _occurrence()
    before = _detail(wallet=100, purchased=0)
    after = _detail(wallet=50, purchased=1)
    purchase = ExchangeTailPurchase(1009922, 1, "诛首秘法残页", 1, 50)
    refreshes = iter((before, after))
    receipts = []

    monkeypatch.setattr(
        tiandi_yiju_tail,
        "_load_persisted_detail",
        lambda _occurrence: (before, {}),
    )
    monkeypatch.setattr(
        tiandi_yiju_tail,
        "_refresh_persisted_detail",
        lambda _activity_id: next(refreshes),
    )
    monkeypatch.setattr(
        tiandi_yiju_tail,
        "_store_completion_receipt",
        lambda **kwargs: receipts.append(kwargs),
    )

    def plan(detail, **_kwargs):
        purchases = [] if detail.current_currency == 50 else [purchase]
        return purchases, set(), {
            "reserved_tokens": 0,
            "planned_remaining_tokens": 50,
            "complete": not purchases,
        }

    monkeypatch.setattr(tiandi_yiju_tail, "plan_exchange_tail_purchases", plan)
    runtime = _Runtime()
    result = _run(tiandi_yiju_tail.execute_tiandi_yiju_exchange_tail(
        None,
        {},
        occurrence=occurrence,
        stop_event=threading.Event(),
        runtime=runtime,
        return_to_world=True,
    ))

    assert result["purchases"] == [{
        "goods_id": 1009922,
        "name": "诛首秘法残页",
        "quantity": 1,
        "unit_price": 50,
    }]
    assert (566, "购买") in runtime.clicks
    assert runtime.clicks[-1] == ("goto", 34)
    assert receipts[0]["current_currency"] == 50


def test_completed_receipt_replay_creates_no_runtime_and_clicks_nothing(
    monkeypatch,
) -> None:
    occurrence = _occurrence()
    detail = _detail(wallet=50, purchased=1)
    receipt = {
        "status": "completed",
        "instance_key": occurrence.instance_key,
        "current_currency": 50,
    }
    monkeypatch.setattr(
        tiandi_yiju_tail,
        "_load_persisted_detail",
        lambda _occurrence: (detail, receipt),
    )
    monkeypatch.setattr(
        tiandi_yiju_tail,
        "plan_exchange_tail_purchases",
        lambda *_args, **_kwargs: (
            [], set(), {"complete": True, "planned_remaining_tokens": 50}
        ),
    )

    class _Runner:
        def _fanxiu_runtime(self, *_args, **_kwargs):
            raise AssertionError("幂等回放不得创建 Runtime")

    result = _run(tiandi_yiju_tail.execute_tiandi_yiju_exchange_tail(
        _Runner(),
        {},
        occurrence=occurrence,
        stop_event=threading.Event(),
        return_to_world=True,
    ))

    assert result["purchases"] == []


def test_active_target_reached_immediately_runs_the_same_exchange_tail(
    monkeypatch,
) -> None:
    events = []

    def done(value):
        if False:
            yield None
        return value

    monkeypatch.setattr(
        tiandi_yiju_task,
        "_wait_tiandi_yiju_home_ready",
        lambda *_args, **_kwargs: done({}),
    )
    monkeypatch.setattr(
        tiandi_yiju_task,
        "claim_tiandi_yiju_task_rewards",
        lambda *_args, **_kwargs: done({"claimed_task_ids": []}),
    )
    monkeypatch.setattr(
        tiandi_yiju_task,
        "_refresh_tiandi_yiju_exchange_facts",
        lambda *_args, **_kwargs: done({
            "shop_item_count": 1,
            "required_new_currency": 0,
        }),
    )
    monkeypatch.setattr(
        tiandi_yiju_task,
        "_assert_tiandi_yiju_production_asset_contract",
        lambda _runtime: None,
    )
    monkeypatch.setattr(
        tiandi_yiju_task,
        "_run_tiandi_yiju_exchange_target_loop",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("目标已达时不得再次进入棋盘挑战循环")
        ),
    )

    def tail(*_args, **kwargs):
        events.append(("tail", kwargs["start"]))
        return (yield from done({"status": "completed", "purchases": []}))

    monkeypatch.setattr(tiandi_yiju_tail, "execute_tiandi_yiju_exchange_tail", tail)

    class Runtime:
        def click_shape_center(self, scene, shape):
            events.append(("click", scene, shape))

        def wait_scene(self, scene, **_kwargs):
            events.append(("wait", scene))
            return (yield from done(scene))

        def goto_view(self, scene):
            events.append(("goto", scene))
            return (yield from done(scene))

    result = _run(tiandi_yiju_task.run_tiandi_yiju_exchange_target_loop(
        Runtime(),
        occurrence=_occurrence(),
        stop_event=threading.Event(),
    ))

    assert events == [("tail", "home")]
    assert result["rounds"] == 0
    assert result["exchange_tail"]["purchases"] == []


def test_active_completed_receipt_replay_has_no_gui_click(monkeypatch) -> None:
    occurrence = _occurrence()
    detail = _detail(wallet=50, purchased=1)
    receipt = {
        "status": "completed",
        "instance_key": occurrence.instance_key,
        "current_currency": 50,
    }

    def done(value):
        if False:
            yield None
        return value

    monkeypatch.setattr(
        tiandi_yiju_task,
        "_wait_tiandi_yiju_home_ready",
        lambda *_args, **_kwargs: done({}),
    )
    monkeypatch.setattr(
        tiandi_yiju_task,
        "claim_tiandi_yiju_task_rewards",
        lambda *_args, **_kwargs: done({"claimed_task_ids": []}),
    )
    monkeypatch.setattr(
        tiandi_yiju_task,
        "_refresh_tiandi_yiju_exchange_facts",
        lambda *_args, **_kwargs: done({"required_new_currency": 0}),
    )
    monkeypatch.setattr(
        tiandi_yiju_tail,
        "_load_persisted_detail",
        lambda _occurrence: (detail, receipt),
    )
    monkeypatch.setattr(
        tiandi_yiju_tail,
        "plan_exchange_tail_purchases",
        lambda *_args, **_kwargs: (
            [], set(), {"complete": True, "planned_remaining_tokens": 50}
        ),
    )

    class Runtime:
        def click_shape_center(self, *_args, **_kwargs):
            raise AssertionError("完成态回放不得点击棋盘或购买")

    result = _run(tiandi_yiju_task.run_tiandi_yiju_exchange_target_loop(
        Runtime(),
        occurrence=occurrence,
        stop_event=threading.Event(),
    ))

    assert result["rounds"] == 0
    assert result["exchange_tail"]["purchases"] == []
