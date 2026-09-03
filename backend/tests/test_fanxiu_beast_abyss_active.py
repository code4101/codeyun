from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime
from fractions import Fraction
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.beast_abyss_challenge_planning import (
    BeastAbyssAutoSettings,
    BeastAbyssBatchMeasurement,
    BeastAbyssResourceLedger,
    build_beast_abyss_yield_scatter_model,
)
from backend.core.fanxiu.data_annotation.tasks import beast_abyss_active
from backend.core.fanxiu.data_annotation.tasks import beast_abyss_exchange
from backend.core.fanxiu.data_annotation.tasks.beast_abyss_native_auto import (
    BeastAbyssNativeAutoRequest,
    BeastAbyssAutoTerminal,
)
from backend.models import FanxiuExchangeActivity, FanxiuExchangeRanking


def _finish(generator):
    with pytest.raises(StopIteration) as stopped:
        next(generator)
    return stopped.value.value


def _generator_result(value):
    if False:
        yield None
    return value


def _ledger(**changes):
    values = {
        "activity_instance_id": "current-occurrence",
        "shop_snapshot_key": "shop",
        "hierarchy": 1,
        "cumulative_currency": 100,
        "current_currency": 100,
        "explore_points": 1000,
        "explore_items": 0,
        "challenge_points": 1000,
        "challenge_items": 0,
        "personal_score": 50,
    }
    values.update(changes)
    return BeastAbyssResourceLedger(**values)


def _measurement(currency: int, score: int = 10) -> BeastAbyssBatchMeasurement:
    from fractions import Fraction

    return BeastAbyssBatchMeasurement(
        "current-occurrence", "shop", 1, 100, 100, 1.0, currency, currency,
        score, 0, 0, 0, Fraction(currency, 100), 0.01, Fraction(0, 1),
    )


def _run_initialization(monkeypatch, batches, *, initial_state=None, max_batches=5):
    initial_state = initial_state if initial_state is not None else {}
    activity = SimpleNamespace(
        id="current-occurrence",
        instance_data={beast_abyss_active.BEAST_ABYSS_INITIALIZATION_KEY: initial_state},
    )
    context = SimpleNamespace(
        current_scene=lambda *_args, **_kwargs: (535, 100.0, object()),
        go_scene=lambda *_args, **_kwargs: _generator_result(None),
    )
    runner = SimpleNamespace(_behavior_tree_context=lambda *_args, **_kwargs: context)
    monkeypatch.setattr(beast_abyss_active, "_load_activity", lambda _occurrence: activity)
    monkeypatch.setattr(beast_abyss_active, "_load_detail", lambda _activity_id: object())
    entry_calls = []
    monkeypatch.setattr(
        beast_abyss_active,
        "_enter_beast_abyss_occurrence_home",
        lambda *_args, **kwargs: (
            entry_calls.append(kwargs.get("label"))
            or _generator_result(535)
        ),
    )
    monkeypatch.setattr(
        beast_abyss_active, "enter_beast_abyss_explore",
        lambda *_args, **_kwargs: _generator_result(657),
    )
    queue = list(batches)
    settled = [
        beast_abyss_active._measurement_from_dict(item)
        for item in initial_state.get("measurements") or ()
    ]

    def execute(*_args, **_kwargs):
        settled.append(queue.pop(0))
        return _generator_result(list(settled))

    reward_calls = []
    monkeypatch.setattr(beast_abyss_active, "_execute_or_recover_initialization_batch", execute)
    monkeypatch.setattr(
        beast_abyss_active, "claim_beast_abyss_task_rewards",
        lambda *_args, **_kwargs: (
            reward_calls.append(len(settled))
            or _generator_result(
                {"checked": True, "gui_opened": True, "remaining_claimable": []}
            )
        ),
    )
    saved = []
    monkeypatch.setattr(
        beast_abyss_active, "_persist_initialization_progress",
        lambda _id, rows, completed, **kwargs: (
            saved.append((list(rows), completed, kwargs.get("final_reward_check")))
            or {"status": "completed", "model": {"points": []}}
        ),
    )
    generator = beast_abyss_active.execute_beast_abyss_initialization_checkpoint(
        runner, {}, {"max_initialization_batches": max_batches}, threading.Event(),
        occurrence=SimpleNamespace(),
    )
    return generator, reward_calls, saved, entry_calls


def test_occurrence_validation_requires_runtime_and_instance_identity() -> None:
    occurrence = SimpleNamespace(
        activity_type="beast-abyss",
        runtime_id="8150001400004",
        instance_key="runtime:exact-occurrence",
        activity_id=8150001,
        cross_count=8,
        start_at=datetime(2026, 9, 2, 10, 0),
        end_at=datetime(2026, 9, 3, 22, 0),
    )
    exact = SimpleNamespace(
        activity_type="beast-abyss",
        runtime_id="8150001400004",
        instance_key="runtime:exact-occurrence",
        game_activity_id=8150001,
        cross_count=8,
        start_date="2026-09-02",
        end_date="2026-09-03",
    )
    wrong_runtime = SimpleNamespace(**{
        **exact.__dict__,
        "runtime_id": "other-runtime",
        "instance_key": "runtime:other-occurrence",
    })

    assert beast_abyss_active.validate_beast_abyss_occurrence(
        occurrence, [wrong_runtime, exact]
    ) is exact
    with pytest.raises(RuntimeError, match="无法唯一对齐"):
        beast_abyss_active.validate_beast_abyss_occurrence(
            occurrence, [wrong_runtime]
        )


def test_occurrence_entry_reuses_existing_beast_home() -> None:
    class Context:
        def current_scene(self, **_kwargs):
            return 535, 100.0, "frame"

    result = _finish(
        beast_abyss_active._enter_beast_abyss_occurrence_home(
            Context(), SimpleNamespace(), label="兽渊初始化"
        )
    )

    assert result == 535


def test_occurrence_entry_opens_schedule_from_unrecognized_world_skin(monkeypatch) -> None:
    actions = []

    class Context:
        def current_scene(self, **_kwargs):
            return None, 87.0, "world-skin-frame"

        def click_shape(self, scene, shape, **kwargs):
            actions.append(("click", scene, shape, kwargs.get("frame_data_url")))

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            return _generator_result(None)

        def wait_scene(self, scene, **_kwargs):
            actions.append(("wait", scene))
            return _generator_result(scene)

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.schedule_navigation.select_schedule_activity",
        lambda *_args, **_kwargs: _generator_result(
            SimpleNamespace(runtime_key="8150001|8150001400004|6400002")
        ),
    )
    occurrence = SimpleNamespace(
        activity_id=8150001,
        runtime_id="8150001400004",
        cross_count=8,
        end_at=datetime(2026, 9, 3, 22, 0),
    )

    result = _finish(
        beast_abyss_active._enter_beast_abyss_occurrence_home(
            Context(), occurrence, label="兽渊初始化"
        )
    )

    assert result is None
    assert actions == [
        ("click", 34, "日程", "world-skin-frame"),
        ("settle", 1.5),
        ("wait", 66),
        ("wait", 535),
    ]


def test_completed_initialization_is_an_occurrence_scoped_noop(monkeypatch) -> None:
    rows = [_measurement(100, 20), _measurement(140, 30)]
    state = {
        "status": "completed",
        "activity_instance_id": "current-occurrence",
        "batch_size": 100,
        "measurements": [
            beast_abyss_active._jsonable_dataclass(item) for item in rows
        ],
        "model": beast_abyss_active._jsonable_dataclass(
            build_beast_abyss_yield_scatter_model(rows)
        ),
        "pending_batch": None,
        "first_reward_check": {
            "checked": True, "gui_opened": True, "remaining_claimable": []
        },
        "final_reward_check": {
            "checked": True, "gui_opened": True, "remaining_claimable": []
        },
    }
    monkeypatch.setattr(
        beast_abyss_active,
        "_load_activity",
        lambda occurrence: SimpleNamespace(
            id="current-occurrence",
            instance_data={beast_abyss_active.BEAST_ABYSS_INITIALIZATION_KEY: state},
        ),
    )

    result = _finish(
        beast_abyss_active.execute_beast_abyss_initialization_checkpoint(
            SimpleNamespace(), {}, {}, threading.Event(), occurrence=SimpleNamespace()
        )
    )

    assert result["status"] == "completed"
    assert result["performed_actions"] is False
    assert result["initialization"] == state


def test_completed_state_requires_stability_reward_proof_and_no_pending() -> None:
    rows = [_measurement(100), _measurement(140)]
    base = {
        "status": "completed",
        "batch_size": 100,
        "measurements": [
            beast_abyss_active._jsonable_dataclass(item) for item in rows
        ],
        "model": beast_abyss_active._jsonable_dataclass(
            build_beast_abyss_yield_scatter_model(rows)
        ),
        "pending_batch": None,
        "first_reward_check": {
            "checked": True, "gui_opened": True, "remaining_claimable": []
        },
        "final_reward_check": {
            "checked": True, "gui_opened": True, "remaining_claimable": []
        },
    }

    assert beast_abyss_active._initialization_state_complete(base) is True
    assert beast_abyss_active._initialization_state_complete(
        {**base, "pending_batch": {"batch_id": "pending"}}
    ) is False
    assert beast_abyss_active._initialization_state_complete(
        {**base, "batch_size": 50}
    ) is False
    assert beast_abyss_active._initialization_state_complete(
        {**base, "pending_batch": "corrupt-marker"}
    ) is False
    assert beast_abyss_active._initialization_state_complete(
        {**base, "first_reward_check": None}
    ) is False
    assert beast_abyss_active._initialization_state_complete(
        {**base, "final_reward_check": {"checked": True}}
    ) is False
    assert beast_abyss_active._initialization_state_complete(
        {
            **base,
            "final_reward_check": {
                "checked": True, "gui_opened": False, "remaining_claimable": []
            },
        }
    ) is False
    assert beast_abyss_active._initialization_state_complete(
        {**base, "model": None}
    ) is False
    assert beast_abyss_active._initialization_state_complete(
        {**base, "model": {**base["model"], "points": [[100, 999, 10]]}}
    ) is False
    assert beast_abyss_active._initialization_state_complete(
        {**base, "model": {**base["model"], "shop_snapshot_key": "other"}}
    ) is False
    assert beast_abyss_active._initialization_state_complete(
        {**base, "model": {**base["model"], "hierarchy": "corrupt"}}
    ) is False
    for field, corrupt in (
        ("seconds_per_explore", 999.0),
        ("challenge_per_explore", "99"),
        ("hierarchy_transitions", [[1, 3]]),
    ):
        assert beast_abyss_active._initialization_state_complete(
            {**base, "model": {**base["model"], field: corrupt}}
        ) is False
    assert beast_abyss_active._initialization_state_complete(
        {**base, "measurements": [*base["measurements"], "corrupt-row"]}
    ) is False
    assert beast_abyss_active._initialization_state_complete(
        {**base, "measurements": [*base["measurements"], {"broken": True}]}
    ) is False
    short_rows = [
        replace(_measurement(100), requested_explores=50, completed_explores=50),
        replace(_measurement(140), requested_explores=50, completed_explores=50),
    ]
    assert beast_abyss_active._initialization_state_complete(
        {
            **base,
            "batch_size": 50,
            "measurements": [
                beast_abyss_active._jsonable_dataclass(item) for item in short_rows
            ],
            "model": beast_abyss_active._jsonable_dataclass(
                build_beast_abyss_yield_scatter_model(short_rows)
            ),
        }
    ) is False
    legacy_measurements = [dict(item) for item in base["measurements"]]
    for item in legacy_measurements:
        item.pop("duration_reliable", None)
    assert beast_abyss_active._initialization_state_complete(
        {**base, "measurements": legacy_measurements}
    ) is False
    unstable = [_measurement(100), _measurement(201)]
    assert beast_abyss_active._initialization_state_complete(
        {
            **base,
            "measurements": [
                beast_abyss_active._jsonable_dataclass(item)
                for item in unstable
            ],
        }
    ) is False


def test_formal_plan_rebases_shop_targets_on_current_currency_ledgers() -> None:
    detail = SimpleNamespace(exchange_plan={
        "target_budgets": {
            "其他折扣": {
                "target_total_tokens": 258_500,
                "target_remaining_tokens": 258_500,
            },
            "收尾道具": {
                "target_total_tokens": 422_100,
                "target_remaining_tokens": 422_100,
            },
        },
    })

    plan = beast_abyss_active._rebase_formal_exchange_plan(
        detail,
        _ledger(current_currency=250_000, cumulative_currency=250_000),
    )

    assert plan["budget_ready"] is True
    assert plan["target_budgets"]["其他折扣"]["required_new_currency"] == 8_500
    assert plan["target_budgets"]["收尾道具"]["required_new_currency"] == 172_100


def test_formal_progress_keeps_initialization_proof_and_fits_new_point(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("backend.db.engine", engine)
    initial = [_measurement(100, 20), _measurement(140, 30)]
    initialization = {
        "status": "completed",
        "activity_instance_id": "current-occurrence",
        "batch_size": 100,
        "measurements": [
            beast_abyss_active._jsonable_dataclass(item) for item in initial
        ],
        "model": beast_abyss_active._jsonable_dataclass(
            build_beast_abyss_yield_scatter_model(initial)
        ),
        "pending_batch": None,
        "first_reward_check": {
            "checked": True, "gui_opened": True, "remaining_claimable": []
        },
        "final_reward_check": {
            "checked": True, "gui_opened": True, "remaining_claimable": []
        },
    }
    activity = FanxiuExchangeActivity(
        id="current-occurrence",
        instance_key="runtime:beast:current",
        family="gameplay_rank",
        activity_type="beast-abyss",
        runtime_id="8150001400004",
        start_date="2026-09-02",
        end_date="2026-09-03",
        instance_data={
            beast_abyss_active.BEAST_ABYSS_INITIALIZATION_KEY: initialization
        },
    )
    with Session(engine) as session:
        session.add(activity)
        session.commit()
    with Session(engine) as session:
        stored_initialization = session.get(
            FanxiuExchangeActivity,
            "current-occurrence",
        ).instance_data[beast_abyss_active.BEAST_ABYSS_INITIALIZATION_KEY]

    formal = replace(
        _measurement(80, 15),
        requested_explores=50,
        completed_explores=50,
        currency_per_explore=Fraction(8, 5),
        seconds_per_explore=0.02,
    )
    state = beast_abyss_active._persist_formal_progress(
        "current-occurrence",
        initial,
        [formal],
        status="in_progress",
    )

    assert state["model"]["points"] == (
        (100, 100, 20),
        (100, 140, 30),
        (50, 80, 15),
    )
    with Session(engine) as session:
        stored = session.get(FanxiuExchangeActivity, "current-occurrence")
        assert stored.instance_data[
            beast_abyss_active.BEAST_ABYSS_INITIALIZATION_KEY
        ] == stored_initialization


def test_retained_complete_shop_identity_does_not_block_initialization(monkeypatch) -> None:
    item = SimpleNamespace(model_dump=lambda: {
        "goods_id": 1, "source_order": 1, "purchase_limit": 1, "purchased_count": 0,
    })
    detail = SimpleNamespace(
        id="current-occurrence", shop_fact_fresh=False, shop_items=[item],
        shop_snapshot_captured_at="2026-09-02T17:34:14+08:00", captured_at="",
        budget_ready=False, budget_block_reason="old wallet",
    )
    assert beast_abyss_active._validate_initialization_detail(
        detail, "current-occurrence"
    ) is detail


def test_ledger_restore_migrates_legacy_rank_revision_field() -> None:
    raw = beast_abyss_active._jsonable_dataclass(_ledger())
    raw.pop("personal_rank_object_identity", None)
    raw["personal_rank_revision"] = "7:9:11"

    restored = beast_abyss_active._ledger_from_dict(raw)

    assert restored.personal_rank_object_identity == "7:9:11"


def test_ledger_uses_retained_rank_snapshot_after_leaving_rank_page(
    monkeypatch,
) -> None:
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.beast_abyss_runtime.read_beast_abyss_budget_snapshot",
        lambda: {
            "current_hierarchy": 3,
            "count_configs": {
                1: {"supplement_item_id": 101},
                2: {"supplement_item_id": 202},
            },
            "supplement_item_counts": {101: 4, 202: 5},
            "explore": {"count": 600},
            "challenge": {"count": 700},
        },
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.wallet.read_wallet_currency_snapshot",
        lambda *_args, **_kwargs: {
            "source": "runtime_memory",
            "cumulative_currency": 1234,
            "exchange_currency": 567,
            "captured_at": captured_at,
            "evidence": {"pid": 7, "process_start_ticks": 9},
        },
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.activity_rank_runtime.read_activity_rank_runtime_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("离开榜单页后不得重新读取已卸载的榜单缓存")
        ),
    )
    item = SimpleNamespace(model_dump=lambda: {"goods_id": 1})
    detail = SimpleNamespace(
        shop_items=[item],
        shop_snapshot_captured_at=captured_at,
        captured_at=captured_at,
    )
    activity = SimpleNamespace(
        id="current-occurrence",
        game_rank_activity_id=110108,
        evidence={
            "rank_scope_identities": {
                "personal": {"runtime_rank_activity_id": 110108}
            }
        },
    )
    retained_rank = {
        "ok": True,
        "complete": True,
        "rank_activity_id": 110108,
        "self_ranking": {"score": 4321},
        "runtime_object_identity": "7:8:9",
        "captured_at": captured_at,
        "evidence": {"pid": 7, "process_start_ticks": 9},
    }

    ledger, _budget = beast_abyss_active._read_ledger(
        activity,
        detail,
        ranking_snapshot=retained_rank,
    )

    assert ledger.cumulative_currency == 1234
    assert ledger.personal_score == 4321
    assert ledger.personal_rank_object_identity == "7:8:9"


def test_first_batch_reward_then_stable_final_reward_and_dual_y_save(monkeypatch) -> None:
    generator, reward_calls, saved, entry_calls = _run_initialization(
        monkeypatch, [_measurement(100, 20), _measurement(140, 30)]
    )
    result = _finish(generator)
    assert entry_calls == ["兽渊10:00初始化"]
    assert reward_calls == [1, 2]
    assert result["scatter_points"] == [(100, 100, 20), (100, 140, 30)]
    assert saved[-1][1] is True


def test_five_unstable_batches_remain_retryable_without_losing_samples(monkeypatch) -> None:
    generator, reward_calls, saved, entry_calls = _run_initialization(
        monkeypatch,
        [_measurement(value) for value in (100, 201, 100, 201, 100)],
    )
    result = _finish(generator)
    assert result["status"] == "pending"
    assert result["batch_count"] == 5
    assert entry_calls == ["兽渊10:00初始化"]
    assert reward_calls == [1]
    assert saved[-1][1] is False


def test_retry_with_persisted_first_reward_proof_skips_initial_reward_check(
    monkeypatch,
) -> None:
    first = _measurement(100, 20)
    first_reward_check = {
        "checked": True,
        "gui_opened": True,
        "remaining_claimable": [],
    }
    generator, reward_calls, saved, entry_calls = _run_initialization(
        monkeypatch,
        [_measurement(140, 30)],
        initial_state={
            "status": "in_progress",
            "measurements": [beast_abyss_active._jsonable_dataclass(first)],
            "first_reward_check": first_reward_check,
            "pending_batch": None,
        },
    )

    result = _finish(generator)

    assert entry_calls == ["兽渊10:00初始化"]
    assert reward_calls == [2]
    assert result["status"] == "completed"
    assert result["first_batch_rewards"] == first_reward_check
    assert saved[-1][1] is True


def test_pending_terminal_is_settled_without_replaying_start(monkeypatch) -> None:
    assets = beast_abyss_active.DEFAULT_BEAST_ABYSS_NATIVE_AUTO_ASSETS
    before = _ledger()
    after = _ledger(cumulative_currency=200, current_currency=200, personal_score=80)
    settings = BeastAbyssAutoSettings(False, True, True, True, False, True, True, 100)
    marker = {
        "batch_id": "b1", "target_explores": 100,
        "before": beast_abyss_active._jsonable_dataclass(before),
        "settings": settings.__dict__, "armed_epoch": 1,
    }
    context = SimpleNamespace(
        current_scene=lambda *_args, **_kwargs: (assets.terminal_scene_ids[0], 100, "frame"),
        ocr_text=lambda _frame: (
            "探查结束 第185次探查 总共获得积分99999 "
            "总共获得功勋88888 点击屏幕关闭"
        ),
    )
    monkeypatch.setattr(beast_abyss_active, "_read_ledger", lambda *_args, **_kwargs: (after, {"count_configs": {2: {"automatic": 0}}}))
    monkeypatch.setattr(
        beast_abyss_active, "run_prepared_beast_abyss_native_auto",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not replay")),
    )
    settled = []
    monkeypatch.setattr(
        beast_abyss_active, "_settle_initialization_batch",
        lambda _id, _marker, measurement: settled.append(measurement) or [measurement],
    )
    monkeypatch.setattr(
        beast_abyss_active,
        "_confirm_initialization_batch_terminal",
        lambda _id, value, **_kwargs: {**value, "terminal_confirmed": True, "terminal_scene": 382},
    )
    monkeypatch.setattr(beast_abyss_active, "_close_auto_terminal", lambda *_args: _generator_result(None))
    monkeypatch.setattr(
        beast_abyss_active,
        "_refresh_personal_rank_for_measurement",
        lambda *_args, **_kwargs: _generator_result({"score": 80}),
    )
    monkeypatch.setattr(
        beast_abyss_active,
        "_seal_initialization_batch_after",
        lambda _id, value, ledger, **_kwargs: {
            **value,
            "after": beast_abyss_active._jsonable_dataclass(ledger),
            "after_challenge_item_automatic": 0,
        },
    )
    result = _finish(beast_abyss_active._execute_or_recover_initialization_batch(
        context, SimpleNamespace(id="current-occurrence"), object(),
        SimpleNamespace(requested_explores=100, measurement=True), pending=marker,
    ))
    assert result == settled
    assert settled[0].completed_explores == 100
    assert settled[0].new_currency == 100
    assert settled[0].personal_score_delta == 30


def test_new_batch_persists_pending_before_start_click(monkeypatch) -> None:
    assets = beast_abyss_active.DEFAULT_BEAST_ABYSS_NATIVE_AUTO_ASSETS
    before = _ledger()
    after = _ledger(cumulative_currency=220, current_currency=220, personal_score=90)
    settings = BeastAbyssAutoSettings(False, True, True, True, False, True, True, 100)
    request = BeastAbyssNativeAutoRequest(
        auto_use_explore_items=True,
        requested_explores=100,
        measurement=True,
    )
    context = SimpleNamespace(current_scene=lambda *_args, **_kwargs: (658, 100, "frame"))
    order = []
    monkeypatch.setattr(
        beast_abyss_active,
        "_refresh_personal_rank_for_measurement",
        lambda *_args, **_kwargs: _generator_result(order.append("rank_refreshed")),
    )
    monkeypatch.setattr(
        beast_abyss_active, "prepare_beast_abyss_native_auto",
        lambda *_args, **_kwargs: _generator_result(settings),
    )
    reads = iter([
        (before, {
            "count_configs": {1: {"automatic": 4}, 2: {"automatic": 0}},
            "capacity": {
                "explore_cost": 1,
                "max_explore_cost": 1,
                "explore_attempts_with_items": 1000,
            },
        }),
        (before, {}),
        (after, {"count_configs": {2: {"automatic": 0}}}),
    ])
    monkeypatch.setattr(beast_abyss_active, "_read_ledger", lambda *_args, **_kwargs: next(reads))
    marker = {
        "batch_id": "b2", "target_explores": 100,
        "before": beast_abyss_active._jsonable_dataclass(before),
        "settings": settings.__dict__, "armed_epoch": 1, "protocol_version": 2,
    }
    monkeypatch.setattr(
        beast_abyss_active, "_arm_initialization_batch",
        lambda *_args: order.append("armed") or marker,
    )
    monkeypatch.setattr(
        beast_abyss_active,
        "_record_initialization_batch_start_intent",
        lambda _id, value: order.append("start_intent") or {
            **value,
            "start_click_intent_at": "2026-09-03T10:00:00+08:00",
            "start_click_intent_epoch": 1,
        },
    )
    monkeypatch.setattr(
        beast_abyss_active, "read_beast_abyss_native_auto_settings",
        lambda *_args, **_kwargs: settings,
    )

    def run(*_args, **_kwargs):
        assert order == ["rank_refreshed", "armed", "start_intent"]
        order.append("started")
        return _generator_result(SimpleNamespace(
            terminal=BeastAbyssAutoTerminal.COMPLETED, scene_id=382,
            terminal_evidence=SimpleNamespace(terminal_observed_explore_index=185),
        ))

    monkeypatch.setattr(beast_abyss_active, "run_prepared_beast_abyss_native_auto", run)
    settled = []

    def settle(_activity_id, _marker, measurement):
        settled.append(measurement)
        return [measurement]

    monkeypatch.setattr(beast_abyss_active, "_settle_initialization_batch", settle)
    monkeypatch.setattr(
        beast_abyss_active,
        "_confirm_initialization_batch_terminal",
        lambda _id, value, **_kwargs: {**value, "terminal_confirmed": True, "terminal_scene": 382},
    )
    monkeypatch.setattr(beast_abyss_active, "_close_auto_terminal", lambda *_args: _generator_result(None))
    monkeypatch.setattr(
        beast_abyss_active,
        "_seal_initialization_batch_after",
        lambda _id, value, ledger, **_kwargs: {
            **value,
            "after": beast_abyss_active._jsonable_dataclass(ledger),
            "after_challenge_item_automatic": 0,
        },
    )

    _finish(beast_abyss_active._execute_or_recover_initialization_batch(
        context, SimpleNamespace(id="current-occurrence"), object(), request, pending=None,
    ))
    assert order == [
        "rank_refreshed", "armed", "start_intent", "started", "rank_refreshed"
    ]
    assert settled[0].completed_explores == 100
    assert settled[0].new_currency == 120
    assert settled[0].personal_score_delta == 40


def test_terminal_confirmed_batch_resumes_rank_refresh_without_replay(monkeypatch) -> None:
    assets = beast_abyss_active.DEFAULT_BEAST_ABYSS_NATIVE_AUTO_ASSETS
    before = _ledger()
    after = _ledger(cumulative_currency=240, current_currency=240, personal_score=95)
    settings = BeastAbyssAutoSettings(False, True, True, True, False, True, True, 100)
    marker = {
        "batch_id": "b3",
        "target_explores": 100,
        "before": beast_abyss_active._jsonable_dataclass(before),
        "settings": settings.__dict__,
        "armed_epoch": 1,
        "terminal_confirmed": True,
        "terminal_scene": 382,
    }
    context = SimpleNamespace(
        current_scene=lambda *_args, **_kwargs: (657, 100, "frame"),
    )
    refreshed = []
    monkeypatch.setattr(
        beast_abyss_active,
        "_refresh_personal_rank_for_measurement",
        lambda *_args, **_kwargs: _generator_result(refreshed.append(True)),
    )
    monkeypatch.setattr(
        beast_abyss_active,
        "run_prepared_beast_abyss_native_auto",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not replay")),
    )
    monkeypatch.setattr(
        beast_abyss_active,
        "_close_auto_terminal",
        lambda *_args: (_ for _ in ()).throw(AssertionError("terminal already closed")),
    )
    monkeypatch.setattr(
        beast_abyss_active,
        "_read_ledger",
        lambda *_args, **_kwargs: (after, {"count_configs": {2: {"automatic": 0}}}),
    )
    settled = []
    monkeypatch.setattr(
        beast_abyss_active,
        "_settle_initialization_batch",
        lambda _id, _marker, measurement: settled.append(measurement) or [measurement],
    )
    monkeypatch.setattr(
        beast_abyss_active,
        "_seal_initialization_batch_after",
        lambda _id, value, ledger, **_kwargs: {
            **value,
            "after": beast_abyss_active._jsonable_dataclass(ledger),
            "after_challenge_item_automatic": 0,
        },
    )

    result = _finish(beast_abyss_active._execute_or_recover_initialization_batch(
        context,
        SimpleNamespace(id="current-occurrence"),
        object(),
        SimpleNamespace(requested_explores=100, measurement=True),
        pending=marker,
    ))

    assert result == settled
    assert refreshed == [True]
    assert settled[0].new_currency == 140
    assert settled[0].personal_score_delta == 45


def test_sealed_after_ledger_resumes_without_reading_mutable_game_state(monkeypatch) -> None:
    before = _ledger()
    after = _ledger(cumulative_currency=260, current_currency=260, personal_score=105)
    settings = BeastAbyssAutoSettings(False, True, True, True, False, True, True, 100)
    marker = {
        "batch_id": "b4",
        "target_explores": 100,
        "before": beast_abyss_active._jsonable_dataclass(before),
        "settings": settings.__dict__,
        "terminal_confirmed": True,
        "terminal_scene": 382,
        "duration_seconds": 90.0,
        "duration_reliable": True,
        "after": beast_abyss_active._jsonable_dataclass(after),
        "after_challenge_item_automatic": 4,
    }
    context = SimpleNamespace(
        current_scene=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("sealed recovery must not inspect the mutable UI")
        )
    )
    monkeypatch.setattr(
        beast_abyss_active,
        "_read_ledger",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("sealed recovery must not reread the mutable ledger")
        ),
    )
    monkeypatch.setattr(
        beast_abyss_active,
        "_refresh_personal_rank_for_measurement",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("sealed recovery must not refresh rank again")
        ),
    )
    settled = []
    monkeypatch.setattr(
        beast_abyss_active,
        "_settle_initialization_batch",
        lambda _id, _marker, measurement: settled.append(measurement) or [measurement],
    )

    result = _finish(beast_abyss_active._execute_or_recover_initialization_batch(
        context,
        SimpleNamespace(id="current-occurrence"),
        object(),
        SimpleNamespace(requested_explores=100, measurement=True),
        pending=marker,
    ))

    assert result == settled
    assert settled[0].new_currency == 160
    assert settled[0].personal_score_delta == 55


def test_measurement_rank_refresh_opens_personal_tab_before_accepting_score(
    monkeypatch,
) -> None:
    actions = []
    rank_reads = iter([
        {
            "ok": True,
            "complete": True,
            "rank_activity_id": 110108,
            "self_ranking": {"score": 122},
            "runtime_object_identity": "1:2:3",
        },
        {
            "ok": True,
            "complete": True,
            "rank_activity_id": 110108,
            "self_ranking": {"score": 123},
            "runtime_object_identity": "1:2:3",
        },
        {
            "ok": True,
            "complete": True,
            "rank_activity_id": 110108,
            "self_ranking": {"score": 123},
            "runtime_object_identity": "1:2:3",
        },
    ])

    class Context:
        def go_scene(self, scene):
            actions.append(("goto", scene))
            return _generator_result(scene)

        def wait_click_then_scene(self, source, shape, target, **_kwargs):
            actions.append(("enter", source, shape, target))
            return _generator_result(target)

        def click_shape_center(self, scene, shape):
            actions.append(("click", scene, shape))

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            return _generator_result(None)

        def wait_scene(self, scene, **_kwargs):
            actions.append(("wait", scene))
            return _generator_result(scene)

    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.activity_rank_runtime.prepare_activity_rank_runtime",
        lambda ids: actions.append(("prepare", tuple(ids))) or {
            "ok": True,
            "loaded_activity_ids": list(ids),
        },
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.activity_rank_runtime.read_activity_rank_runtime_snapshot",
        lambda activity_id: actions.append(("read", activity_id)) or next(rank_reads),
    )
    monkeypatch.setattr(
        beast_abyss_active,
        "enter_beast_abyss_explore",
        lambda *_args: _generator_result(actions.append(("restore", 657))),
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.schedule_navigation.select_schedule_activity",
        lambda *_args, **_kwargs: _generator_result(SimpleNamespace(runtime_key="exact")),
    )
    activity = SimpleNamespace(
        evidence={
            "rank_scope_identities": {
                "personal": {"runtime_rank_activity_id": 110108}
            }
        },
        game_rank_activity_id=110108,
        game_activity_id=8150001,
        runtime_id="8150001400004",
        cross_count=8,
    )

    result = _finish(
        beast_abyss_active._refresh_personal_rank_for_measurement(
            Context(), activity
        )
    )

    assert result["self_ranking"]["score"] == 123
    assert actions == [
        ("goto", 535),
        ("enter", 535, "兽渊榜", 537),
        ("click", 537, "个人"),
        ("settle", 2.0),
        ("settle", 1.0),
        ("read", 110108),
        ("settle", 1.0),
        ("read", 110108),
        ("settle", 1.0),
        ("read", 110108),
        ("click", 537, "返回"),
        ("wait", 34),
        ("goto", 34),
        ("goto", 66),
        ("wait", 535),
        ("restore", 657),
    ]


def test_measurement_rank_refresh_accepts_fresh_unchanged_score(monkeypatch) -> None:
    reads = iter([
        {
            "ok": True,
            "complete": True,
            "rank_activity_id": 110108,
            "self_ranking": {"score": 100},
            "runtime_object_identity": "4:5:6",
        },
        {
            "ok": True,
            "complete": True,
            "rank_activity_id": 110108,
            "self_ranking": {"score": 100},
            "runtime_object_identity": "4:5:6",
        },
    ])
    clicks = []

    class Context:
        def go_scene(self, _scene):
            return _generator_result(None)

        def wait_click_then_scene(self, *_args, **_kwargs):
            return _generator_result(None)

        def click_shape_center(self, scene, shape):
            clicks.append((scene, shape))

        def wait_action_settle(self, _seconds):
            return _generator_result(None)

        def wait_scene(self, *_args, **_kwargs):
            return _generator_result(None)

    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.activity_rank_runtime.prepare_activity_rank_runtime",
        lambda ids: {"ok": True, "loaded_activity_ids": list(ids)},
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.activity_rank_runtime.read_activity_rank_runtime_snapshot",
        lambda _activity_id: next(reads),
    )
    monkeypatch.setattr(
        beast_abyss_active,
        "enter_beast_abyss_explore",
        lambda *_args: _generator_result(None),
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.schedule_navigation.select_schedule_activity",
        lambda *_args, **_kwargs: _generator_result(SimpleNamespace(runtime_key="exact")),
    )
    activity = SimpleNamespace(
        evidence={},
        game_rank_activity_id=110108,
        game_activity_id=8150001,
        runtime_id="8150001400004",
        cross_count=8,
    )

    snapshot = _finish(beast_abyss_active._refresh_personal_rank_for_measurement(
        Context(),
        activity,
    ))

    assert snapshot["self_ranking"]["score"] == 100
    assert clicks.count((537, "个人")) == 1


def test_pending_marker_and_measurement_settle_atomically(monkeypatch, tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'beast.db'}")
    SQLModel.metadata.create_all(engine)
    activity = FanxiuExchangeActivity(
        id="current-occurrence", instance_key="runtime:beast:current",
        family="gameplay_rank", activity_type="beast-abyss", runtime_id="8150001400004",
        game_activity_id=8150001, cross_count=8, prepare_at="2026-09-02T00:00:00+08:00",
        start_at="2026-09-02T10:00:00+08:00", end_at="2026-09-03T22:00:00+08:00",
        close_at="2026-09-04T23:58:59+08:00", start_date="2026-09-02",
        end_date="2026-09-03",
    )
    with Session(engine) as session:
        session.add(activity)
        session.commit()
    monkeypatch.setattr("backend.db.engine", engine)
    before = _ledger()
    settings = BeastAbyssAutoSettings(False, True, True, True, False, True, True, 100)

    marker = beast_abyss_active._arm_initialization_batch(
        "current-occurrence", before, settings
    )
    with Session(engine) as session:
        stored = session.get(FanxiuExchangeActivity, "current-occurrence")
        state = stored.instance_data[beast_abyss_active.BEAST_ABYSS_INITIALIZATION_KEY]
        assert state["pending_batch"]["batch_id"] == marker["batch_id"]
        assert state.get("measurements") in (None, [])

    marker = beast_abyss_active._record_initialization_batch_start_intent(
        "current-occurrence", marker
    )
    assert marker["start_click_intent_at"]
    with pytest.raises(RuntimeError, match="禁止重复点击"):
        beast_abyss_active._record_initialization_batch_start_intent(
            "current-occurrence", marker
        )

    marker = beast_abyss_active._confirm_initialization_batch_terminal(
        "current-occurrence", marker, terminal_scene=382
    )
    assert marker["terminal_confirmed"] is True
    assert marker["terminal_scene"] == 382
    with Session(engine) as session:
        stored = session.get(FanxiuExchangeActivity, "current-occurrence")
        pending = stored.instance_data[
            beast_abyss_active.BEAST_ABYSS_INITIALIZATION_KEY
        ]["pending_batch"]
        assert pending["terminal_confirmed"] is True
        assert pending["batch_id"] == marker["batch_id"]

    sealed_after = _ledger(
        cumulative_currency=220,
        current_currency=220,
        personal_score=90,
    )
    marker = beast_abyss_active._seal_initialization_batch_after(
        "current-occurrence",
        marker,
        sealed_after,
        challenge_item_automatic=4,
    )
    assert marker["after"] == beast_abyss_active._jsonable_dataclass(sealed_after)
    assert marker["after_challenge_item_automatic"] == 4

    rows = beast_abyss_active._settle_initialization_batch(
        "current-occurrence", marker, _measurement(120, 40)
    )
    assert len(rows) == 1
    with Session(engine) as session:
        stored = session.get(FanxiuExchangeActivity, "current-occurrence")
        state = stored.instance_data[beast_abyss_active.BEAST_ABYSS_INITIALIZATION_KEY]
        assert state["pending_batch"] is None
        assert state["settled_batch_ids"] == [marker["batch_id"]]
        assert len(state["measurements"]) == 1

    stable_rows = [_measurement(100, 20), _measurement(140, 30)]
    with pytest.raises(RuntimeError, match="首轮任务奖励"):
        beast_abyss_active._persist_initialization_progress(
            "current-occurrence", stable_rows, completed=True
        )
    first_reward_check = {
        "checked": True, "gui_opened": True, "remaining_claimable": []
    }
    with pytest.raises(RuntimeError, match="末次任务奖励"):
        beast_abyss_active._persist_initialization_progress(
            "current-occurrence",
            stable_rows,
            completed=True,
            first_reward_check=first_reward_check,
        )
    short_rows = [
        replace(item, requested_explores=50, completed_explores=50)
        for item in stable_rows
    ]
    with pytest.raises(RuntimeError, match="100次"):
        beast_abyss_active._persist_initialization_progress(
            "current-occurrence",
            short_rows,
            completed=True,
            first_reward_check=first_reward_check,
            final_reward_check={
                "checked": True, "gui_opened": True, "remaining_claimable": []
            },
        )
    foreign_rows = [
        replace(item, activity_instance_id="previous-occurrence")
        for item in stable_rows
    ]
    with pytest.raises(RuntimeError, match="occurrence 不一致"):
        beast_abyss_active._persist_initialization_progress(
            "current-occurrence",
            foreign_rows,
            completed=True,
            first_reward_check=first_reward_check,
            final_reward_check={
                "checked": True, "gui_opened": True, "remaining_claimable": []
            },
        )
    completed = beast_abyss_active._persist_initialization_progress(
        "current-occurrence",
        stable_rows,
        completed=True,
        first_reward_check=first_reward_check,
        final_reward_check={
            "checked": True, "gui_opened": True, "remaining_claimable": []
        },
    )
    assert completed["status"] == "completed"
    assert completed["activity_instance_id"] == "current-occurrence"
    assert completed["pending_batch"] is None
    assert completed["first_reward_check"] == first_reward_check
    assert completed["final_reward_check"] == {
        "checked": True,
        "gui_opened": True,
        "remaining_claimable": [],
    }
    assert completed["model"]["points"] == ((100, 100, 20), (100, 140, 30))
    assert beast_abyss_active._initialization_state_complete(
        completed,
        expected_activity_id="current-occurrence",
    ) is True
    assert beast_abyss_active._initialization_state_complete(
        completed,
        expected_activity_id="previous-occurrence",
    ) is False


def test_close_real_terminal_accepts_transition_to_cutscene() -> None:
    class Context:
        def __init__(self):
            self.scene = 382
            self.clicks = []

        def current_scene(self, _expected, update=False):
            return self.scene, 100.0, "terminal"

        def ocr_text(self, _frame):
            return "探查结束 总共获得积分：1000 总共获得功勋：20 点击屏幕关闭"

        def click_shape_center(self, scene, title):
            self.clicks.append((scene, title))
            self.scene = 185

        def wait_action_settle(self, _seconds):
            if False:
                yield None

    context = Context()
    _finish(beast_abyss_active._close_auto_terminal(context, 382))
    assert context.clicks == [(382, "关闭")]
    assert context.scene == 185


def test_exchange_tail_reenters_exact_occurrence_and_syncs_both_final_ranks(
    monkeypatch,
) -> None:
    calls = []

    class Context:
        def current_scene(self, **_kwargs):
            return 66, 100.0, "schedule"

        def go_scene(self, scene):
            calls.append(("goto", scene))
            return _generator_result(None)

        def wait_scene(self, scene, **kwargs):
            calls.append(("wait", scene, kwargs.get("label")))
            return _generator_result(None)

        def wait_click_then_scene(self, scene, shape, target, **kwargs):
            calls.append(("enter", scene, shape, target))
            return _generator_result(None)

        def click_shape_center(self, scene, shape):
            calls.append(("click", scene, shape))

        def wait_action_settle(self, seconds):
            calls.append(("settle", seconds))
            return _generator_result(None)

    context = Context()
    runner = SimpleNamespace(
        _behavior_tree_context=lambda *_args, **_kwargs: context,
    )
    occurrence = SimpleNamespace(
        activity_id=8150001,
        runtime_id="8150001400004",
        cross_count=8,
        end_at=datetime(2026, 9, 3, 22, 0),
    )
    monkeypatch.setattr(
        beast_abyss_active,
        "_load_activity",
        lambda _occurrence: SimpleNamespace(
            id="old-occurrence",
            evidence={
                "rank_scope_identities": {
                    "personal": {"runtime_rank_activity_id": 110108},
                    "team": {"runtime_rank_activity_id": 110208},
                }
            },
        ),
    )

    def select_exact(_context, pattern, **kwargs):
        calls.append((
            "select",
            pattern,
            kwargs["expected_activity_id"],
            kwargs["now"],
        ))
        return _generator_result(SimpleNamespace(runtime_key="8150001|8150001400004"))

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.schedule_navigation.select_schedule_activity",
        select_exact,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.beast_abyss_exchange.execute_beast_abyss_exchange",
        lambda *_args, **kwargs: (
            calls.append(("exchange", kwargs["activity_id"]))
            or _generator_result({"result": "success"})
        ),
    )
    monkeypatch.setattr(
        beast_abyss_active,
        "store_beast_abyss_final_rankings",
        lambda activity_id: (
            calls.append(("store-ranks", activity_id))
            or {"personal_count": 1, "team_count": 1}
        ),
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.activity_rank_runtime.prepare_activity_rank_runtime",
        lambda rank_ids: {
            "ok": True,
            "loaded_activity_ids": list(rank_ids),
        },
    )
    rank_read_counts = {}

    def read_rank(rank_id):
        rank_read_counts[rank_id] = rank_read_counts.get(rank_id, 0) + 1
        return {
            "ok": True,
            "complete": True,
            "rank_activity_id": rank_id,
            "runtime_object_identity": f"{rank_id}:stable",
        }

    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.activity_rank_runtime.read_activity_rank_runtime_snapshot",
        read_rank,
    )

    result = _finish(
        beast_abyss_active.execute_beast_abyss_exchange_tail_checkpoint(
            runner,
            {},
            {},
            threading.Event(),
            occurrence=occurrence,
        )
    )

    select_calls = [item for item in calls if item[0] == "select"]
    assert len(select_calls) == 2
    assert all(item[2] == 8150001 for item in select_calls)
    assert all(item[3] == datetime(2026, 9, 3, 12, 0) for item in select_calls)
    assert calls.index(("exchange", "old-occurrence")) < calls.index(
        ("enter", 535, "兽渊榜", 537)
    )
    assert ("click", 537, "个人") in calls
    assert ("click", 537, "团队") in calls
    assert ("store-ranks", "old-occurrence") in calls
    assert calls[-2][0:3] == ("click", 537, "返回")
    assert calls[-1][0:2] == ("wait", 34)
    assert result["final_rankings"] == {
        "personal_count": 1,
        "team_count": 1,
    }


def test_exchange_shop_collection_never_reuses_rank_cache(monkeypatch) -> None:
    calls = []
    detail = SimpleNamespace(
        current_currency=100,
        shop_items=[],
        currency_fact_fresh=True,
        shop_fact_fresh=True,
        currency_captured_at="",
        shop_snapshot_captured_at="",
    )
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("backend.db.engine", engine)
    with Session(engine) as session:
        session.add(FanxiuExchangeActivity(
            id="old-occurrence",
            instance_key="runtime:test:beast",
            activity_type="beast-abyss",
            start_date="2026-09-02",
            end_date="2026-09-03",
            evidence={
                "refresh_status": {"currency": "updated", "shop": "updated"},
                "currency_runtime": {"pid": 7, "process_start_ticks": 9},
                "shop": {"pid": 7, "process_start_ticks": 9},
            },
        ))
        session.commit()
    def collect(*_args, **kwargs):
        calls.append(kwargs)
        captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
        detail.currency_captured_at = captured_at
        detail.shop_snapshot_captured_at = captured_at
        return detail

    monkeypatch.setattr(
        beast_abyss_exchange,
        "collect_and_store_beast_abyss_activity",
        collect,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.wallet.read_wallet_currency_snapshot",
        lambda *_args, **_kwargs: {
            "exchange_currency": 100,
            "source": "runtime_memory",
        },
    )
    monkeypatch.setattr(
        beast_abyss_exchange,
        "plan_exchange_tail_purchases",
        lambda *_args, **_kwargs: (
            [],
            set(),
            {"reserved_tokens": 0, "planned_remaining_tokens": 100},
        ),
    )
    monkeypatch.setattr(
        beast_abyss_exchange,
        "plan_yunmeng_tail_physical_actions",
        lambda *_args, **_kwargs: [],
    )
    context = SimpleNamespace(
        current_scene=lambda *_args, **_kwargs: (536, 100.0, object()),
    )
    runner = SimpleNamespace(
        _behavior_tree_context=lambda *_args, **_kwargs: context,
    )

    result = _finish(
        beast_abyss_exchange.execute_beast_abyss_exchange(
            runner,
            {},
            activity_id="old-occurrence",
            stop_event=threading.Event(),
        )
    )

    assert len(calls) == 2
    assert all(call["collect_runtime_shop"] is True for call in calls)
    assert all(call["collect_runtime_rank"] is False for call in calls)
    assert result["currency_remaining"] == 100


def test_exchange_rejects_retained_shop_or_cross_process_snapshot() -> None:
    captured_at = datetime.now().astimezone()
    detail = SimpleNamespace(
        currency_fact_fresh=True,
        shop_fact_fresh=False,
        currency_captured_at=captured_at.isoformat(timespec="seconds"),
        shop_snapshot_captured_at=captured_at.isoformat(timespec="seconds"),
    )
    evidence = {
        "refresh_status": {"currency": "updated", "shop": "retained"},
        "currency_runtime": {"pid": 7, "process_start_ticks": 9},
        "shop": {"pid": 7, "process_start_ticks": 9},
    }
    with pytest.raises(RuntimeError, match="不是本次刷新事实"):
        beast_abyss_exchange._validate_fresh_exchange_snapshot(
            detail,
            evidence,
            attempt_started_at=captured_at,
            label="兽渊_兑换",
        )

    detail.shop_fact_fresh = True
    evidence["refresh_status"]["shop"] = "updated"
    evidence["shop"]["pid"] = 8
    with pytest.raises(RuntimeError, match="同一游戏进程"):
        beast_abyss_exchange._validate_fresh_exchange_snapshot(
            detail,
            evidence,
            attempt_started_at=captured_at,
            label="兽渊_兑换",
        )


def test_final_rank_refresh_fails_closed_when_team_tab_did_not_load(
    monkeypatch,
) -> None:
    context = SimpleNamespace(
        click_shape_center=lambda *_args: None,
        wait_action_settle=lambda *_args: _generator_result(None),
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.activity_rank_runtime.prepare_activity_rank_runtime",
        lambda rank_ids: (
            {"ok": True, "loaded_activity_ids": [110108]}
            if list(rank_ids) == [110108]
            else {
                "ok": False,
                "loaded_activity_ids": [110108],
                "reason": "团队榜未加载",
            }
        ),
    )
    read_counts = {}

    def read_rank(rank_id):
        read_counts[rank_id] = read_counts.get(rank_id, 0) + 1
        return {
            "ok": True,
            "complete": True,
            "rank_activity_id": rank_id,
            "runtime_object_identity": f"{rank_id}:stable",
        }

    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.activity_rank_runtime.read_activity_rank_runtime_snapshot",
        read_rank,
    )
    monkeypatch.setattr(
        beast_abyss_active,
        "store_beast_abyss_final_rankings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("未加载团队榜时不得持久化")
        ),
    )

    generator = beast_abyss_active.refresh_beast_abyss_final_rankings(
        context,
        activity_id="old-occurrence",
        personal_rank_activity_id=110108,
        team_rank_activity_id=110208,
    )
    with pytest.raises(RuntimeError, match="团队榜未加载"):
        next(generator)


def test_store_final_rankings_reads_persisted_evidence_not_detail_dto(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("backend.db.engine", engine)
    activity_id = "current-occurrence"
    with Session(engine) as session:
        session.add(FanxiuExchangeActivity(
            id=activity_id,
            instance_key="runtime:test:activity:8150001:test",
            activity_type="beast-abyss",
            cross_count=8,
            start_date="2026-09-02",
            end_date="2026-09-03",
            evidence={
                "current_related_ranking_scopes": ["team"],
                "refresh_status": {
                    "ranking_scopes": ["personal", "team"],
                },
            },
        ))
        for scope in ("personal", "team"):
            session.add(FanxiuExchangeRanking(
                activity_id=activity_id,
                ranking_scope=scope,
                rank=1,
                score=100,
                role_key=f"{scope}:1",
            ))
        session.commit()

    detail_without_evidence = SimpleNamespace(
        id=activity_id,
        captured_at="2026-09-03T00:57:00+08:00",
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.beast_abyss.collect_and_store_beast_abyss_activity",
        lambda *_args, **_kwargs: detail_without_evidence,
    )

    result = beast_abyss_active.store_beast_abyss_final_rankings(activity_id)

    assert result["personal_count"] == 1
    assert result["team_count"] == 1
    assert result["captured_at"] == "2026-09-03T00:57:00+08:00"


def test_daily_reconcile_opens_shop_without_challenge_or_purchase(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("backend.db.engine", engine)
    calls = []
    occurrence = SimpleNamespace(
        activity_id=8150001,
        runtime_id="8150001400004",
        cross_count=8,
        start_at=datetime(2026, 9, 2, 10, 0),
        end_at=datetime(2026, 9, 3, 22, 0),
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.ranking_reconcile.seed_ranking_occurrence",
        lambda *_args, **_kwargs: SimpleNamespace(id="current-occurrence"),
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.ranking_reconcile.reconcile_ranking_occurrence",
        lambda *_args, **_kwargs: {
            "status": "completed",
            "activity_id": "current-occurrence",
            "facts": {"shop": "updated"},
        },
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.schedule_navigation.select_schedule_activity",
        lambda _context, pattern, **kwargs: (
            calls.append(("select", pattern, kwargs["now"]))
            or _generator_result(
                SimpleNamespace(runtime_key="8150001|8150001400004")
            )
        ),
    )

    class Context:
        def current_scene(self, **_kwargs):
            return 66, 100.0, "schedule"

        def go_scene(self, scene):
            calls.append(("goto", scene))
            return _generator_result(None)

        def wait_scene(self, scene, **_kwargs):
            calls.append(("wait", scene))
            return _generator_result(None)

        def wait_click_then_scene(self, scene, shape, target, **_kwargs):
            calls.append(("enter", scene, shape, target))
            return _generator_result(None)

    result = _finish(
        beast_abyss_active.execute_beast_abyss_daily_reconcile_checkpoint(
            SimpleNamespace(
                _behavior_tree_context=lambda *_args, **_kwargs: Context()
            ),
            {},
            threading.Event(),
            occurrence=occurrence,
            captured_at=datetime(2026, 9, 2, 0, 30),
            required_fact_watermark=datetime(2026, 9, 2, 0, 30),
        )
    )

    assert ("select", "兽渊探秘", datetime(2026, 9, 3, 12, 0)) in calls
    assert ("enter", 535, "兑换宝阁", 536) in calls
    assert calls[-1] == ("goto", 34)
    assert result["status"] == "completed"
