from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import threading

from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.core.fanxiu.data_annotation.tasks import tiandi_yiju as tiandi_task
from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju import (
    configure_tiandi_yiju_auto_dialog,
    execute_tiandi_yiju_checkpoint,
    open_tiandi_yiju_recommended_target,
    run_tiandi_yiju_bounded_batch,
)


def _run(generator):
    try:
        while True:
            next(generator)
    except StopIteration as done:
        return done.value


def _occurrence() -> RankingOccurrence:
    now = datetime(2026, 8, 28, 10, 5, tzinfo=timezone.utc)
    return RankingOccurrence(
        activity_type="tiandi-yiju",
        family="gameplay_rank",
        runtime_id="runtime-1",
        activity_id=8090004,
        start_at=now,
        end_at=now,
        prepare_at=now,
        close_at=now,
        cross_count=8,
    )


def _auto_snapshot(*, enabled: bool) -> dict:
    choices = {
        "auto_use_strength_item": enabled,
        "continue_after_defeat": enabled,
        "skip_animation": enabled,
        "master_skill_item": enabled,
        "quadruple_chess_token_item": enabled,
    }
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "cross_count": 8,
        "auto_challenge_choices": choices,
    }


class _Shape:
    def __init__(self, title):
        self.title = title


class _View:
    def __init__(self, scene):
        self.scene = scene

    def get_shapes(self):
        if self.scene == 680:
            return [
                _Shape("单次对弈"),
                _Shape("对弈次数_减少"),
                _Shape("对弈次数_增加"),
                _Shape("对弈次数_滑块"),
                _Shape("自动使用仙弈盒开关"),
                _Shape("对弈失败时不中断自动对弈开关"),
                _Shape("跳过动画开关"),
                _Shape("妙手珠开关"),
                _Shape("四倍棋符开关"),
            ]
        if self.scene == 999:
            return [_Shape("确认")]
        return [_Shape("棋点001-天元"), _Shape("棋点002-中腹·四")]


class _Runtime:
    def __init__(self):
        self.clicks = []

    def wait_click_then_view(self, source, shape, target, **_options):
        self.clicks.append((source, shape, target))
        if False:
            yield None
        return target

    def click_shape_center(self, source, shape):
        self.clicks.append((source, shape, None))

    def wait_action_settle(self, _seconds):
        if False:
            yield None
        return None

    def wait_click(self, source, shape, **_options):
        self.clicks.append((source, shape, None))
        if False:
            yield None
        return None

    def current_scene(self, candidates, **_options):
        if 681 in candidates:
            return 681, 100.0, "result-frame"
        if 304 in candidates:
            return 304, 100.0, "activity-frame"
        return None, 0.0, "frame"

    def ocr_text(self, _frame):
        return ""

    def wait_scene(self, target, **_options):
        if False:
            yield None
        return target

    def view(self, scene):
        return _View(scene)

    def goto_view(self, target):
        self.clicks.append(("goto", target, None))
        if False:
            yield None
        return target


def test_recommended_target_uses_exact_runtime_piece_shape_and_waits_transition() -> None:
    runtime = _Runtime()
    target = {
        "ok": True,
        "complete": True,
        "target": {"piece_id": 2, "total_score": 100},
    }

    result = _run(
        open_tiandi_yiju_recommended_target(runtime, target_reader=lambda: target)
    )

    assert result["target"]["piece_id"] == 2
    assert runtime.clicks[:3] == [
        (678, "弈局", 686),
        (686, runtime.clicks[1][1], None),
        (686, "跳转", None),
    ]
    assert runtime.clicks[1][1].title == "棋点002-中腹·四"


def test_tianyuan_override_reuses_prior_runtime_recommendation() -> None:
    runtime = _Runtime()
    recommendation = {
        "ok": True,
        "complete": True,
        "target": {"piece_id": 1, "total_score": 100},
    }

    result = _run(
        open_tiandi_yiju_recommended_target(
            runtime,
            target_reader=lambda: (_ for _ in ()).throw(
                AssertionError("天元锁定后不应重新规划")
            ),
            recommendation_override=recommendation,
        )
    )

    assert result["target"]["piece_id"] == 1
    assert runtime.clicks[1][1].title == "棋点001-天元"


def test_auto_configuration_clicks_runtime_differences_then_rechecks() -> None:
    runtime = _Runtime()
    snapshots = iter([_auto_snapshot(enabled=False), _auto_snapshot(enabled=True)])

    result = _run(
        configure_tiandi_yiju_auto_dialog(
            runtime,
            cross_count=8,
            reader=lambda: next(snapshots),
        )
    )

    assert result["plan"]["mode"] == "cross_server"
    assert result["after"]["auto_challenge_choices"]["skip_animation"] is True
    assert [shape for scene, shape, _ in runtime.clicks if scene == 680] == [
        "自动使用仙弈盒开关",
        "对弈失败时不中断自动对弈开关",
        "跳过动画开关",
        "妙手珠开关",
        "四倍棋符开关",
    ]


def test_batch_fails_before_any_click_when_count_shapes_are_missing() -> None:
    import pytest

    class _MissingCountRuntime(_Runtime):
        def view(self, scene):
            if scene == 680:
                return SimpleNamespace(
                    get_shapes=lambda: [_Shape("单次对弈")],
                )
            return super().view(scene)

    runtime = _MissingCountRuntime()
    with pytest.raises(RuntimeError, match="对弈次数_减少"):
        _run(
            run_tiandi_yiju_bounded_batch(
                runtime,
                requested_rounds=10,
                cross_count=8,
            )
        )

    assert runtime.clicks == []


def test_new_result_overlay_is_distinct_from_legacy_scene() -> None:
    class _OverlayRuntime(_Runtime):
        def current_scene(self, _candidates, **_options):
            return None, 0.0, "overlay-frame"

        def ocr_text(self, _frame):
            return "对弈 个人棋符 +100 体力消耗 10 确认"

    result = _run(
        tiandi_task._start_one_tiandi_yiju_round_and_wait_result(
            _OverlayRuntime(),
            timeout=1,
        )
    )

    assert result["terminal_kind"] == "new_result_overlay"
    assert result["scene_id"] is None


def test_formal_new_result_scene_uses_its_own_confirm_transaction(monkeypatch) -> None:
    class _FormalOverlayRuntime(_Runtime):
        def current_scene(self, candidates, **_options):
            if 999 in candidates:
                return 999, 100.0, "overlay-frame"
            return super().current_scene(candidates, **_options)

    def fake_set_count(_runtime, target):
        if False:
            yield None
        return {"target": target, "after": target}

    runtime = _FormalOverlayRuntime()
    snapshots = iter([_auto_snapshot(enabled=True), _auto_snapshot(enabled=True)])
    target = {
        "ok": True,
        "complete": True,
        "target": {"piece_id": 1, "total_score": 100},
    }
    monkeypatch.setattr(tiandi_task, "TIANDI_YIJU_RESULT_OVERLAY_SCENE", 999)
    monkeypatch.setattr(tiandi_task, "set_tiandi_yiju_round_count", fake_set_count)

    result = _run(
        run_tiandi_yiju_bounded_batch(
            runtime,
            requested_rounds=10,
            cross_count=8,
            snapshot_reader=lambda: next(snapshots),
            target_reader=lambda: target,
        )
    )

    assert result["result"]["terminal_kind"] == "new_result_overlay"
    assert (999, "确认", None) in runtime.clicks
    assert (681, "点击屏幕继续", None) not in runtime.clicks


def test_empty_point_forces_one_round_and_reuses_public_count_setter(monkeypatch) -> None:
    runtime = _Runtime()
    snapshots = iter([_auto_snapshot(enabled=True), _auto_snapshot(enabled=True)])
    count_calls = []

    def fake_set_count(_runtime, target):
        count_calls.append(target)
        if False:
            yield None
        return {"target": target, "after": target}

    monkeypatch.setattr(tiandi_task, "set_tiandi_yiju_round_count", fake_set_count)
    target = {
        "ok": True,
        "complete": True,
        "target": {"piece_id": 1, "total_score": 0},
    }
    monkeypatch.setattr(
        tiandi_task,
        "read_tiandi_yiju_runtime_snapshot",
        lambda: next(snapshots),
    )
    monkeypatch.setattr(
        tiandi_task,
        "read_tiandi_yiju_recommended_target",
        lambda: target,
    )

    result = _run(
        run_tiandi_yiju_bounded_batch(
            runtime,
            requested_rounds=100,
            cross_count=8,
        )
    )

    assert result["requested_rounds"] == 1
    assert count_calls == [1]
    assert runtime.clicks[-2:] == [
        (681, "点击屏幕继续", None),
        (680, "关闭", 678),
    ]


def test_unified_closing_gap_is_consumed_without_recalculation() -> None:
    detail = SimpleNamespace(
        id="activity-1",
        activity_type="tiandi-yiju",
        is_active=True,
        budget_ready=True,
        exchange_plan={
            "budget_ready": True,
            "target_budgets": {
                "收尾道具": {"required_new_currency": 12_345},
            },
        },
    )

    assert tiandi_task._required_closing_currency(
        detail,
        activity_id="activity-1",
    ) == 12_345


def test_unified_closing_gap_fails_closed_when_freshness_is_missing() -> None:
    detail = SimpleNamespace(
        id="activity-1",
        activity_type="tiandi-yiju",
        is_active=True,
        budget_ready=False,
        budget_block_reason="shop stale",
        exchange_plan={
            "budget_ready": False,
            "target_budgets": {
                "收尾道具": {"required_new_currency": 12_345},
            },
        },
    )

    import pytest

    with pytest.raises(RuntimeError, match="freshness.*shop stale"):
        tiandi_task._required_closing_currency(detail, activity_id="activity-1")


def test_shared_batch_planner_is_the_activity_neutral_implementation() -> None:
    plan = tiandi_task._plan_shared_exchange_batch(
        required_new_currency=50_000,
    )

    assert plan.requested_challenges == 10
    assert plan.planning_mode == "probe"


def test_wallet_identity_rejects_non_runtime_source() -> None:
    import pytest

    with pytest.raises(RuntimeError, match="钱包 Runtime 身份不完整"):
        tiandi_task._wallet_identity(
            {
                "currency_type": 13,
                "source": "cached_fixture",
                "evidence": {"pid": 11, "process_start_ticks": 22},
            },
            currency_type=13,
        )


def test_exchange_loop_reuses_public_plan_caps_recollects_and_locks_tianyuan(
    monkeypatch,
) -> None:
    activity = SimpleNamespace(id="activity-1")

    class _ExecResult:
        def first(self):
            return activity

    class _Session:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def exec(self, _statement):
            return _ExecResult()

    details = iter([
        SimpleNamespace(
            id="activity-1",
            activity_type="tiandi-yiju",
            is_active=True,
            budget_ready=True,
            currency_type=13,
            exchange_plan={
                "budget_ready": True,
                "target_budgets": {
                    "收尾道具": {"required_new_currency": 50_000},
                },
            },
        ),
        SimpleNamespace(
            id="activity-1",
            activity_type="tiandi-yiju",
            is_active=True,
            budget_ready=True,
            currency_type=13,
            exchange_plan={
                "budget_ready": True,
                "target_budgets": {
                    "收尾道具": {"required_new_currency": 10_000},
                },
            },
        ),
        SimpleNamespace(
            id="activity-1",
            activity_type="tiandi-yiju",
            is_active=True,
            budget_ready=True,
            currency_type=13,
            exchange_plan={
                "budget_ready": True,
                "target_budgets": {
                    "收尾道具": {"required_new_currency": 5_000},
                },
            },
        ),
        SimpleNamespace(
            id="activity-1",
            activity_type="tiandi-yiju",
            is_active=True,
            budget_ready=True,
            currency_type=13,
            exchange_plan={
                "budget_ready": True,
                "target_budgets": {
                    "收尾道具": {"required_new_currency": 0},
                },
            },
        ),
    ])
    collect_calls = []

    def collect(_session, *, activity_id):
        collect_calls.append(activity_id)
        return next(details)

    wallets = iter([
        {
            "currency_type": 13,
            "source": "runtime_memory",
            "exchange_currency": 100,
            "cumulative_currency": 1_000,
            "evidence": {"pid": 11, "process_start_ticks": 22},
        },
        {
            "currency_type": 13,
            "source": "runtime_memory",
            "exchange_currency": 200,
            "cumulative_currency": 1_100,
            "evidence": {"pid": 11, "process_start_ticks": 22},
        },
        {
            "currency_type": 13,
            "source": "runtime_memory",
            "exchange_currency": 300,
            "cumulative_currency": 1_200,
            "evidence": {"pid": 11, "process_start_ticks": 22},
        },
        {
            "currency_type": 13,
            "source": "runtime_memory",
            "exchange_currency": 600,
            "cumulative_currency": 1_500,
            "evidence": {"pid": 11, "process_start_ticks": 22},
        },
    ])
    wallet_calls = []

    def wallet(currency_type, *, allow_discovery):
        wallet_calls.append((currency_type, allow_discovery))
        return next(wallets)

    planner_calls = []

    def planner(**kwargs):
        planner_calls.append(kwargs)
        return SimpleNamespace(requested_challenges=500)

    batch_calls = []
    tianyuan_recommendation = {
        "ok": True,
        "complete": True,
        "target": {"piece_id": 1, "total_score": 20},
    }

    def batch(_runtime, **kwargs):
        batch_calls.append(kwargs)
        if False:
            yield None
        return {
            "requested_rounds": kwargs["requested_rounds"],
            "target": {"piece_id": 1},
            "recommendation": tianyuan_recommendation,
        }

    import sqlmodel
    from backend.core.fanxiu.activity import tiandi_yiju as activity_module
    from backend.core.fanxiu.instrumentation import wallet as wallet_module

    monkeypatch.setattr(sqlmodel, "Session", _Session)
    monkeypatch.setattr(activity_module, "collect_and_store_tiandi_yiju_activity", collect)
    monkeypatch.setattr(wallet_module, "read_wallet_currency_snapshot", wallet)
    monkeypatch.setattr(tiandi_task, "_plan_shared_exchange_batch", planner)
    monkeypatch.setattr(tiandi_task, "run_tiandi_yiju_bounded_batch", batch)
    result = _run(
        tiandi_task.run_tiandi_yiju_exchange_target_loop(
            object(),
            occurrence=_occurrence(),
            stop_event=threading.Event(),
            max_batches=3,
        )
    )

    assert result["status"] == "completed"
    assert result["rounds"] == 300
    assert len(planner_calls) == 3
    assert [call["requested_rounds"] for call in batch_calls] == [100, 100, 100]
    assert batch_calls[0]["recommendation_override"] is None
    assert batch_calls[1]["recommendation_override"] == tianyuan_recommendation
    assert batch_calls[2]["recommendation_override"] == tianyuan_recommendation
    assert planner_calls == [
        {
            "required_new_currency": 50_000,
            "measured_currency_delta": None,
            "measured_challenges": None,
            "previous_currency_delta": None,
            "previous_challenges": None,
        },
        {
            "required_new_currency": 10_000,
            "measured_currency_delta": 100,
            "measured_challenges": 100,
            "previous_currency_delta": None,
            "previous_challenges": None,
        },
        {
            "required_new_currency": 5_000,
            "measured_currency_delta": 100,
            "measured_challenges": 100,
            "previous_currency_delta": 100,
            "previous_challenges": 100,
        },
    ]
    assert collect_calls == ["activity-1"] * 4
    assert wallet_calls == [(13, True), (13, False), (13, False), (13, False)]


def test_production_checkpoint_fails_before_navigation_when_assets_are_pending(
    monkeypatch,
) -> None:
    import pytest

    runtime = _Runtime()

    class _Runner:
        def _fanxiu_runtime(self, *_args, **_kwargs):
            return runtime

    monkeypatch.setattr(
        "backend.core.fanxiu.activity.runtime_schedule.read_fanxiu_activity_runtime_schedule",
        lambda **_kwargs: {"available": True, "complete": True},
    )

    with pytest.raises(RuntimeError, match="结果浮层尚未接入正式 scene"):
        _run(
            execute_tiandi_yiju_checkpoint(
                _Runner(),
                {"asset_tree_path": "tree.json"},
                {},
                threading.Event(),
                occurrence=_occurrence(),
            )
        )

    assert runtime.clicks == []


def test_production_checkpoint_routes_to_runtime_target_batch_loop(monkeypatch) -> None:
    runtime = _Runtime()
    loop_calls = []
    monkeypatch.setattr(tiandi_task, "TIANDI_YIJU_RESULT_OVERLAY_SCENE", 999)

    class _Runner:
        def _fanxiu_runtime(self, *_args, **_kwargs):
            return runtime

    def empty_generator(result=None):
        if False:
            yield None
        return result

    monkeypatch.setattr(
        "backend.core.fanxiu.activity.runtime_schedule.read_fanxiu_activity_runtime_schedule",
        lambda **_kwargs: {"available": True, "complete": True},
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.schedule_navigation.select_schedule_activity",
        lambda *_args, **_kwargs: empty_generator({"ok": True}),
    )
    monkeypatch.setattr(
        tiandi_task,
        "_wait_tiandi_yiju_home_ready",
        lambda *_args, **_kwargs: empty_generator({"ok": True}),
    )
    monkeypatch.setattr(
        tiandi_task,
        "claim_tiandi_yiju_task_rewards",
        lambda *_args, **_kwargs: empty_generator({"claimed": 0}),
    )

    def fake_loop(_runtime, **kwargs):
        loop_calls.append(kwargs)
        return (yield from empty_generator({
            "status": "completed",
            "message": "done",
        }))

    monkeypatch.setattr(tiandi_task, "run_tiandi_yiju_exchange_target_loop", fake_loop)
    result = _run(
        execute_tiandi_yiju_checkpoint(
            _Runner(),
            {"asset_tree_path": "tree.json"},
            {"max_batches": 7},
            threading.Event(),
            occurrence=_occurrence(),
        )
    )

    assert result["status"] == "completed"
    assert loop_calls and loop_calls[0]["max_batches"] == 7
    assert all(click[1] != "己方中心棋点候选" for click in runtime.clicks)
