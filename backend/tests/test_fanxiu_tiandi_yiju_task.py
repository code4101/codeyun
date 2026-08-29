from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import threading

import pytest

from backend.core.fanxiu.activity.exchange_planning import (
    ExchangeYieldFeatureSpec,
    ExchangeYieldScatterSample,
)
from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.core.fanxiu.data_annotation.tasks import tiandi_yiju as tiandi_task
from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju import (
    configure_tiandi_yiju_auto_dialog,
    execute_tiandi_yiju_checkpoint,
    open_tiandi_yiju_recommended_target,
    run_tiandi_yiju_bounded_batch,
)
from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju_count import (
    set_tiandi_yiju_round_count,
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


FEATURE_ITEMS_AVAILABLE = {
    "master_skill_item": True,
    "quadruple_chess_token_item": True,
}


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
        if self.scene in {688, 692, 999}:
            return [_Shape("点击屏幕继续"), _Shape("点击屏幕关闭")]
        if self.scene == 687:
            return [_Shape("不再提醒"), _Shape("仍要对弈")]
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

    def ocr_numbers_in_shapes(self, _scene, _shapes, **_options):
        return [1], "次数：1"

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

    def wait_scene(self, target, *_targets, **_options):
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


class _DelayedCountRuntime:
    def __init__(self):
        self.values = iter([1, 1, 2, 2])
        self.clicks = []

    def ocr_numbers_in_shapes(self, _scene, _shapes, **_options):
        value = next(self.values)
        return [value], str(value)

    def click_shape_center(self, scene, shape):
        self.clicks.append((scene, shape))

    def wait_action_settle(self, _seconds):
        if False:
            yield None
        return None


def test_round_count_waits_for_delayed_single_step_repaint() -> None:
    runtime = _DelayedCountRuntime()

    result = _run(set_tiandi_yiju_round_count(runtime, 2))

    assert result["after"] == 2
    assert runtime.clicks == [(680, "对弈次数_增加")]


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
            feature_item_available=FEATURE_ITEMS_AVAILABLE,
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


def test_batch_fails_before_any_click_when_result_scene_is_pending(monkeypatch) -> None:
    import pytest

    runtime = _Runtime()
    monkeypatch.setattr(tiandi_task, "TIANDI_YIJU_RESULT_OVERLAY_SCENE", None)
    with pytest.raises(RuntimeError, match="结果浮层尚未接入正式 scene"):
        _run(
            run_tiandi_yiju_bounded_batch(
                runtime,
                requested_rounds=10,
                cross_count=8,
            )
        )

    assert runtime.clicks == []


def test_batch_fails_before_any_click_when_result_confirm_shape_is_missing(
    monkeypatch,
) -> None:
    import pytest

    runtime = _Runtime()
    monkeypatch.setattr(tiandi_task, "TIANDI_YIJU_RESULT_OVERLAY_SCENE", 998)
    with pytest.raises(RuntimeError, match="缺少正式『点击屏幕关闭』Shape"):
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
            return "批战结束 总计获得奖励 天地棋玉 450 体力消耗 10"

    result = _run(
        tiandi_task._start_one_tiandi_yiju_round_and_wait_result(
            _OverlayRuntime(),
            timeout=1,
        )
    )

    assert result["terminal_kind"] == "new_result_overlay"
    assert result["scene_id"] == 692


def test_ally_point_confirmation_is_handled_before_result() -> None:
    class _AllyConfirmRuntime(_Runtime):
        def __init__(self):
            super().__init__()
            self.confirmed = False

        def current_scene(self, candidates, **_options):
            if 687 in candidates and not self.confirmed:
                return 687, 100.0, "ally-confirm-frame"
            if 681 in candidates:
                return 681, 100.0, "result-frame"
            return None, 0.0, "frame"

        def wait_click(self, source, shape, **_options):
            self.clicks.append((source, shape, None))
            if shape == "仍要对弈":
                self.confirmed = True
            if False:
                yield None
            return None

    runtime = _AllyConfirmRuntime()

    result = _run(
        tiandi_task._start_one_tiandi_yiju_round_and_wait_result(runtime, timeout=1)
    )

    assert result["terminal_kind"] == "legacy_scene"
    assert runtime.clicks == [
        (687, "不再提醒", None),
        (687, "仍要对弈", None),
    ]


def test_running_scene_can_transition_to_result() -> None:
    class _RunningRuntime(_Runtime):
        def __init__(self):
            super().__init__()
            self.polls = 0

        def current_scene(self, candidates, **_options):
            self.polls += 1
            if self.polls == 1:
                return None, 0.0, "config"
            if self.polls == 2:
                return 690, 100.0, "running"
            return 681, 100.0, "result"

    result = _run(
        tiandi_task._start_one_tiandi_yiju_round_and_wait_result(
            _RunningRuntime(), timeout=1
        )
    )

    assert result["terminal_kind"] == "legacy_scene"


def test_board_without_running_evidence_is_not_a_success() -> None:
    class _UnstartedRuntime(_Runtime):
        def current_scene(self, _candidates, **_options):
            return 678, 100.0, "board"

    with pytest.raises(TimeoutError, match="未出现结果终态"):
        _run(
            tiandi_task._start_one_tiandi_yiju_round_and_wait_result(
                _UnstartedRuntime(), timeout=0
            )
        )


def test_completed_prompt_is_confirmed_before_result() -> None:
    class _CompletedRuntime(_Runtime):
        def __init__(self):
            super().__init__()
            self.polls = 0

        def current_scene(self, candidates, **_options):
            self.polls += 1
            if self.polls == 1:
                return None, 0.0, "config"
            if self.polls == 2:
                return 691, 100.0, "completed"
            return 681, 100.0, "result"

    runtime = _CompletedRuntime()
    result = _run(
        tiandi_task._start_one_tiandi_yiju_round_and_wait_result(runtime, timeout=1)
    )

    assert result["terminal_kind"] == "legacy_scene"
    assert (691, "确认", None) in runtime.clicks


def test_formal_new_result_scene_uses_its_own_confirm_transaction(monkeypatch) -> None:
    class _FormalOverlayRuntime(_Runtime):
        def current_scene(self, candidates, **_options):
            if 999 in candidates:
                return 999, 100.0, "overlay-frame"
            return super().current_scene(candidates, **_options)

        def wait_scene(self, target, *targets, **_options):
            candidates = (target, *targets)
            if 680 in candidates and 678 in candidates:
                result = 680
            elif 999 in candidates:
                result = 999
            else:
                result = target
            if False:
                yield None
            return result

    def fake_set_count(_runtime, target, available):
        if False:
            yield None
        return {"target": target, "after": target, "available": available}

    runtime = _FormalOverlayRuntime()
    snapshots = iter([_auto_snapshot(enabled=True), _auto_snapshot(enabled=True)])
    target = {
        "ok": True,
        "complete": True,
        "target": {"piece_id": 1, "total_score": 100},
    }
    monkeypatch.setattr(tiandi_task, "TIANDI_YIJU_RESULT_OVERLAY_SCENE", 999)
    monkeypatch.setattr(tiandi_task, "set_tiandi_yiju_funded_rounds", fake_set_count)

    result = _run(
        run_tiandi_yiju_bounded_batch(
            runtime,
            requested_rounds=10,
            cross_count=8,
            snapshot_reader=lambda: next(snapshots),
            target_reader=lambda: target,
            verified_available_rounds=100,
            feature_item_available=FEATURE_ITEMS_AVAILABLE,
        )
    )

    assert result["result"]["terminal_kind"] == "new_result_overlay"
    assert runtime.clicks.count(
        (999, tiandi_task.TIANDI_YIJU_RESULT_CONFIRM_SHAPE, None)
    ) == 2
    assert (680, "关闭", None) in runtime.clicks
    assert (681, "点击屏幕继续", None) not in runtime.clicks


def test_empty_point_forces_one_round_and_reuses_public_count_setter(monkeypatch) -> None:
    runtime = _Runtime()
    snapshots = iter([_auto_snapshot(enabled=True), _auto_snapshot(enabled=True)])
    count_calls = []
    monkeypatch.setattr(tiandi_task, "TIANDI_YIJU_RESULT_OVERLAY_SCENE", 999)

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
        "read_tiandi_yiju_auto_dialog_snapshot",
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
            feature_item_available=FEATURE_ITEMS_AVAILABLE,
        )
    )

    assert result["requested_rounds"] == 1
    assert count_calls == [1]
    assert runtime.clicks[-2:] == [
        (681, "点击屏幕继续", None),
        (680, "关闭", None),
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


def test_yield_ledger_keeps_large_history_when_bounded() -> None:
    evidence: dict = {}
    for rounds in [1_479, *range(1, 40)]:
        evidence = tiandi_task._append_tiandi_yiju_yield_evidence(
            evidence,
            occurrence_instance_key="occurrence-1",
            rounds=rounds,
            currency_delta=rounds * 40,
            process_identity=(13, "runtime_memory", 11, 22),
            feature_item_usage={},
        )

    rows = evidence[tiandi_task.TIANDI_YIJU_YIELD_LEDGER_KEY]
    assert len(rows) == tiandi_task.TIANDI_YIJU_YIELD_LEDGER_LIMIT
    assert any(row["rounds"] == 1_479 for row in rows)
    loaded = tiandi_task._load_tiandi_yiju_yield_samples(
        evidence,
        occurrence_instance_key="occurrence-1",
        allowed_feature_keys=set(),
    )
    assert sum(sample.attempt_count for sample in loaded) >= 1_479


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


def test_exchange_loop_supplies_shortfall_and_reuses_persisted_shop_target(
    monkeypatch,
) -> None:
    activity = SimpleNamespace(
        id="activity-1",
        instance_key=_occurrence().instance_key,
        evidence={},
    )

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

        def get(self, _model, _identity):
            return activity

        def add(self, _row):
            return None

        def commit(self):
            return None

    detail = SimpleNamespace(
        id="activity-1",
        activity_type="tiandi-yiju",
        is_active=True,
        budget_ready=True,
        currency_type=13,
        exchange_plan={
            "budget_ready": True,
            "target_budgets": {
                "收尾道具": {
                    "target_total_tokens": 51_000,
                    "target_remaining_tokens": 50_100,
                    "required_new_currency": 50_000,
                },
            },
        },
    )

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
            "exchange_currency": 40_100,
            "cumulative_currency": 41_000,
            "evidence": {"pid": 11, "process_start_ticks": 22},
        },
        {
            "currency_type": 13,
            "source": "runtime_memory",
            "exchange_currency": 45_100,
            "cumulative_currency": 46_000,
            "evidence": {"pid": 11, "process_start_ticks": 22},
        },
        {
            "currency_type": 13,
            "source": "runtime_memory",
            "exchange_currency": 50_100,
            "cumulative_currency": 51_000,
            "evidence": {"pid": 11, "process_start_ticks": 22},
        },
    ])
    wallet_calls = []

    def wallet(currency_type, *, allow_discovery):
        wallet_calls.append((currency_type, allow_discovery))
        return next(wallets)

    batch_calls = []
    locked_recommendation = {
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
            "recommendation": locked_recommendation,
        }

    import sqlmodel
    from backend.core.fanxiu.activity import exchange_event as exchange_event_module
    from backend.core.fanxiu.instrumentation import backpack as backpack_module
    from backend.core.fanxiu.instrumentation import wallet as wallet_module

    monkeypatch.setattr(sqlmodel, "Session", _Session)
    shop_snapshot_calls = []
    monkeypatch.setattr(
        exchange_event_module,
        "list_exchange_activity_snapshot",
        lambda *_args, **_kwargs: (
            shop_snapshot_calls.append(1)
            or SimpleNamespace(selected_activity=detail)
        ),
    )
    monkeypatch.setattr(
        tiandi_task,
        "read_tiandi_yiju_runtime_snapshot",
        lambda: {"strength_item_id": 100000004, "natural_play_budget": 0},
    )
    # The first supply is resource-capped and still leaves fewer than the
    # planned 100 rounds.  The loop must consume that verified remainder.
    inventory_counts = iter([40, 40, 1000, 1000])
    monkeypatch.setattr(
        backpack_module,
        "read_backpack_item_counts",
        lambda _ids, *, manager_key: (
            {100000004: next(inventory_counts), 100000008: 0, 100000002: 0},
            {},
        ),
    )
    monkeypatch.setattr(wallet_module, "read_wallet_currency_snapshot", wallet)
    monkeypatch.setattr(tiandi_task, "run_tiandi_yiju_bounded_batch", batch)
    supply_calls = []

    def supply(_runtime, **kwargs):
        supply_calls.append(kwargs)
        if False:
            yield None
        return {"status": "supplied"}

    result = _run(
        tiandi_task._run_tiandi_yiju_exchange_target_loop(
            _Runtime(),
            occurrence=_occurrence(),
            stop_event=threading.Event(),
            max_batches=3,
            supply_executor=supply,
        )
    )

    assert result["status"] == "completed"
    assert result["rounds"] == 240
    assert shop_snapshot_calls == [1]
    assert supply_calls == [{"required_boxes": 100}]
    assert [call["requested_rounds"] for call in batch_calls] == [40, 100, 100]
    assert [call["verified_available_rounds"] for call in batch_calls] == [40, 1000, 1000]
    assert batch_calls[0]["recommendation_override"] is None
    assert batch_calls[0]["feature_item_available"] == {
        "master_skill_item": False,
        "quadruple_chess_token_item": False,
    }
    assert batch_calls[1]["recommendation_override"] == locked_recommendation
    assert batch_calls[2]["recommendation_override"] == locked_recommendation
    assert wallet_calls == [(13, True), (13, False), (13, False), (13, False)]


def test_formal_challenge_checks_rewards_before_entering_board(monkeypatch) -> None:
    events = []

    class _FormalRuntime(_Runtime):
        def click_shape_center(self, scene, shape):
            events.append(("click", scene, shape))

        def wait_scene(self, scene, **_options):
            events.append(("wait", scene))
            if False:
                yield None
            return scene

    def claim(*_args, **_kwargs):
        events.append(("claim",))
        if False:
            yield None
        return {"claimed_task_ids": []}

    def challenge(*_args, **_kwargs):
        events.append(("challenge",))
        if False:
            yield None
        return {"status": "completed"}

    def refresh(*_args, **_kwargs):
        events.append(("refresh",))
        if False:
            yield None
        return {"shop_item_count": 19}

    def home_ready(*_args, **_kwargs):
        events.append(("home-ready",))
        if False:
            yield None
        return {"snapshot": {"ok": True}}

    monkeypatch.setattr(tiandi_task, "claim_tiandi_yiju_task_rewards", claim)
    monkeypatch.setattr(tiandi_task, "_refresh_tiandi_yiju_exchange_facts", refresh)
    monkeypatch.setattr(
        tiandi_task,
        "_assert_tiandi_yiju_production_asset_contract",
        lambda _runtime: events.append(("asset-gate",)),
    )
    monkeypatch.setattr(
        tiandi_task,
        "_wait_tiandi_yiju_home_ready",
        home_ready,
    )
    monkeypatch.setattr(
        tiandi_task,
        "_run_tiandi_yiju_exchange_target_loop",
        challenge,
    )

    result = _run(
        tiandi_task.run_tiandi_yiju_exchange_target_loop(
            _FormalRuntime(),
            occurrence=_occurrence(),
            stop_event=threading.Event(),
        )
    )

    assert events == [
        ("home-ready",),
        ("claim",),
        ("refresh",),
        ("asset-gate",),
        ("click", 677, "进入弈局"),
        ("wait", 678),
        ("challenge",),
    ]
    assert result["task_rewards"] == {"claimed_task_ids": []}
    assert result["exchange_facts"] == {"shop_item_count": 19}


def test_formal_challenge_rechecks_rewards_idempotently_on_replay(monkeypatch) -> None:
    reward_results = iter(
        [
            {"claimed_task_ids": [101], "idempotent": False},
            {"claimed_task_ids": [], "idempotent": True},
        ]
    )
    claim_calls = []
    refresh_calls = []
    challenge_calls = []

    def claim(*_args, **_kwargs):
        claim_calls.append(1)
        if False:
            yield None
        return next(reward_results)

    def challenge(*_args, **_kwargs):
        challenge_calls.append(1)
        if False:
            yield None
        return {"status": "completed"}

    def refresh(*_args, **_kwargs):
        refresh_calls.append(1)
        if False:
            yield None
        return {"shop_item_count": 19}

    def home_ready(*_args, **_kwargs):
        if False:
            yield None
        return {"snapshot": {"ok": True}}

    monkeypatch.setattr(tiandi_task, "claim_tiandi_yiju_task_rewards", claim)
    monkeypatch.setattr(tiandi_task, "_refresh_tiandi_yiju_exchange_facts", refresh)
    monkeypatch.setattr(
        tiandi_task,
        "_assert_tiandi_yiju_production_asset_contract",
        lambda _runtime: None,
    )
    monkeypatch.setattr(tiandi_task, "_wait_tiandi_yiju_home_ready", home_ready)
    monkeypatch.setattr(
        tiandi_task,
        "_run_tiandi_yiju_exchange_target_loop",
        challenge,
    )
    runtime = _Runtime()

    first = _run(
        tiandi_task.run_tiandi_yiju_exchange_target_loop(
            runtime,
            occurrence=_occurrence(),
            stop_event=threading.Event(),
        )
    )
    second = _run(
        tiandi_task.run_tiandi_yiju_exchange_target_loop(
            runtime,
            occurrence=_occurrence(),
            stop_event=threading.Event(),
        )
    )

    assert len(claim_calls) == len(refresh_calls) == len(challenge_calls) == 2
    assert first["task_rewards"]["claimed_task_ids"] == [101]
    assert second["task_rewards"] == {
        "claimed_task_ids": [],
        "idempotent": True,
    }


def test_batch_policy_closes_estimated_tail_with_one_hundred_rounds() -> None:
    plan = tiandi_task._plan_tiandi_yiju_batch_rounds(
        required_currency=1_000,
        yield_samples=[ExchangeYieldScatterSample(4_100, 100)],
    )

    assert plan.estimated_remaining_rounds == 25
    assert plan.challenge_batch_rounds == 100
    assert plan.supply_target_rounds == 100
    assert plan.planning_mode == "tail_100"


def test_batch_policy_uses_all_scatter_weight_and_separates_supply_target() -> None:
    plan = tiandi_task._plan_tiandi_yiju_batch_rounds(
        required_currency=293_402,
        yield_samples=[
            ExchangeYieldScatterSample(450, 10),
            ExchangeYieldScatterSample(
                97_899,
                1_479,
                feature_item_usage=(("fourfold", 284), ("master", 480)),
            ),
            ExchangeYieldScatterSample(1_638, 41),
        ],
        feature_specs=[
            ExchangeYieldFeatureSpec("fourfold", 3.0),
            ExchangeYieldFeatureSpec("master", 1.0),
        ],
    )

    assert plan.estimated_remaining_rounds == 7_167
    assert plan.challenge_batch_rounds == 3_584
    assert plan.supply_target_rounds == 7_167
    assert plan.planning_mode == "evidence_50pct"


def test_batch_policy_fails_closed_when_feature_multiplier_is_unknown() -> None:
    with pytest.raises(ValueError, match="缺少道具增益规格"):
        tiandi_task._plan_tiandi_yiju_batch_rounds(
            required_currency=10_000,
            yield_samples=[
                ExchangeYieldScatterSample(
                    20_000,
                    100,
                    feature_item_usage=(("unknown_item", 10),),
                )
            ],
        )


def test_batch_policy_without_authoritative_scatter_uses_probe_only() -> None:
    plan = tiandi_task._plan_tiandi_yiju_batch_rounds(
        required_currency=999_999,
    )

    assert plan.estimated_remaining_rounds is None
    assert plan.challenge_batch_rounds == 100
    assert plan.supply_target_rounds == 100


def test_production_checkpoint_checks_reward_before_exchange_and_asset_gate(
    monkeypatch,
) -> None:
    import pytest

    runtime = _Runtime()
    events = []
    monkeypatch.setattr(tiandi_task, "TIANDI_YIJU_RESULT_OVERLAY_SCENE", None)

    class _Runner:
        def _fanxiu_runtime(self, *_args, **_kwargs):
            return runtime

    monkeypatch.setattr(
        "backend.core.fanxiu.activity.runtime_schedule.read_fanxiu_activity_runtime_schedule",
        lambda **_kwargs: {"available": True, "complete": True},
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.schedule_navigation.select_schedule_activity",
        lambda *_args, **_kwargs: (
            yield from (_value for _value in ())
        ),
    )
    monkeypatch.setattr(
        tiandi_task,
        "_goto_tiandi_yiju_schedule",
        lambda *_args, **_kwargs: (yield from (_value for _value in ())),
    )
    monkeypatch.setattr(
        tiandi_task,
        "_wait_tiandi_yiju_home_ready",
        lambda *_args, **_kwargs: (
            yield from (_value for _value in ())
        ),
    )

    def refresh_exchange(*_args, **_kwargs):
        events.append("exchange_refreshed")
        if False:
            yield None
        return {"shop_item_count": 19}

    def claim(*_args, **_kwargs):
        events.append("reward_checked")
        if False:
            yield None
        return {"claimed_task_ids": []}

    monkeypatch.setattr(
        tiandi_task,
        "_refresh_tiandi_yiju_exchange_facts",
        refresh_exchange,
    )
    monkeypatch.setattr(tiandi_task, "claim_tiandi_yiju_task_rewards", claim)

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

    assert events == ["reward_checked", "exchange_refreshed"]
    assert runtime.clicks == []


def test_schedule_entry_uses_observed_cover_hop() -> None:
    events = []

    class _ScheduleRuntime:
        def goto_view(self, scene):
            events.append(("goto", scene))
            if False:
                yield None
            return scene

        def wait_click_then_view(self, source, shape, targets, **_options):
            events.append(("click", source, shape, tuple(targets)))
            if False:
                yield None
            return SimpleNamespace(id=477 if source == 34 else 66)

    _run(tiandi_task._goto_tiandi_yiju_schedule(_ScheduleRuntime()))

    assert events == [
        ("goto", 34),
        ("click", 34, "日程", (66, 477)),
        ("click", 477, "返回", (66,)),
    ]


def test_refresh_exchange_facts_uses_exact_occurrence_and_returns_by_runtime_gui(
    monkeypatch,
) -> None:
    import sqlmodel
    from types import SimpleNamespace

    occurrence = _occurrence()
    activity = SimpleNamespace(id="activity-1", instance_key=occurrence.instance_key)
    collected_ids = []

    class _Result:
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
            return _Result()

    class _RunnerAdapter:
        @staticmethod
        def _frame_size(_raw):
            return 900, 1600

    class _ExchangeRuntime(_Runtime):
        runner = _RunnerAdapter()

        def cur_frame(self, *, update=False):
            assert update is True
            return "frame"

        def full_frame_ocr_tokens(self, frame):
            assert frame == "frame"
            return [{
                "text": "天地弈局",
                "x": 800,
                "y": 1200,
                "w": 40,
                "h": 180,
                "score": 99,
            }]

        def view(self, scene):
            assert scene == 677
            return SimpleNamespace(raw={})

        def click_frame_point(self, scene, x, y):
            self.clicks.append((scene, round(x), round(y)))

    monkeypatch.setattr(sqlmodel, "Session", _Session)
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.tiandi_yiju.collect_and_store_tiandi_yiju_activity",
        lambda _session, *, activity_id: (
            collected_ids.append(activity_id)
            or SimpleNamespace(
                id=activity_id,
                instance_key=occurrence.instance_key,
                activity_type="tiandi-yiju",
                is_active=True,
                budget_ready=True,
                exchange_plan={
                    "budget_ready": True,
                    "target_budgets": {
                        "收尾道具": {"required_new_currency": 0}
                    },
                },
                shop_items=[1, 2, 3],
                current_currency=66,
                cumulative_currency=88,
            )
        ),
    )
    monkeypatch.setattr(tiandi_task, "group_ocr_tokens", lambda tokens: tokens)
    monkeypatch.setattr(
        tiandi_task,
        "_wait_tiandi_yiju_home_ready",
        lambda *_args, **_kwargs: (
            yield from (_value for _value in ())
        ),
    )

    runtime = _ExchangeRuntime()
    result = _run(
        tiandi_task._refresh_tiandi_yiju_exchange_facts(
            runtime,
            occurrence=occurrence,
        )
    )

    assert collected_ids == ["activity-1"]
    assert result == {
        "activity_id": "activity-1",
        "instance_key": occurrence.instance_key,
        "shop_item_count": 3,
        "current_currency": 66,
        "cumulative_currency": 88,
        "required_new_currency": 0,
    }
    assert runtime.clicks[0] == (677, "兑换宝阁", None)
    assert runtime.clicks[-1] == (677, 820, 1290)


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
    schedule_select_calls = []

    def select_activity(*_args, **kwargs):
        schedule_select_calls.append(kwargs)
        return empty_generator({"ok": True})

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.schedule_navigation.select_schedule_activity",
        select_activity,
    )
    monkeypatch.setattr(
        tiandi_task,
        "_goto_tiandi_yiju_schedule",
        lambda *_args, **_kwargs: empty_generator(66),
    )
    monkeypatch.setattr(
        tiandi_task,
        "_wait_tiandi_yiju_home_ready",
        lambda *_args, **_kwargs: empty_generator({"ok": True}),
    )
    refresh_calls = []

    def fake_refresh(_runtime, **kwargs):
        refresh_calls.append(kwargs)
        return (yield from empty_generator({"shop_item_count": 19}))

    monkeypatch.setattr(
        tiandi_task,
        "_refresh_tiandi_yiju_exchange_facts",
        fake_refresh,
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
    assert schedule_select_calls[0]["expected_activity_id"] == _occurrence().activity_id
    # The active-flow wrapper owns reward and shop refresh ordering; replacing
    # that wrapper here intentionally bypasses both details.
    assert refresh_calls == []
    assert loop_calls and loop_calls[0]["max_batches"] == 7
    assert all(click[1] != "己方中心棋点候选" for click in runtime.clicks)
