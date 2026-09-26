from __future__ import annotations

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
from backend.models import FanxiuExchangeActivity, FanxiuExchangeRanking


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


def test_completed_state_requires_one_sample_reward_proof_and_no_pending() -> None:
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
    single = [rows[0]]
    assert beast_abyss_active._initialization_state_complete({**base,
        "measurements": [beast_abyss_active._jsonable_dataclass(single[0])],
        "model": beast_abyss_active._jsonable_dataclass(build_beast_abyss_yield_scatter_model(single))})

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
    ) is True
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


def test_formal_plan_reads_each_commodity_and_keeps_locked_reservations() -> None:
    detail = SimpleNamespace(shop_items=[
        {"goods_id": 1, "name": "第一行", "priority_order": 2, "source_order": 0,
         "token_cost": 400, "purchase_limit": 230, "purchased_count": 10, "cumulative_tokens": 92000},
        {"goods_id": 2, "name": "锁定行", "priority_order": 5, "source_order": 1, "locked": True,
         "token_cost": 100, "purchase_limit": 100, "purchased_count": 0, "cumulative_tokens": 102000}])
    plan = beast_abyss_active._rebase_formal_exchange_plan(detail, _ledger())
    assert [r["target_total_tokens"] for r in plan["milestones"]] == [92000, 102000]
    assert [r["target_remaining_tokens"] for r in plan["milestones"]] == [88000, 98000]


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
    passed = beast_abyss_active._persist_formal_progress(
        "current-occurrence", initial, [formal], status="unavailable",
        terminal_reason="resource_insufficient", last_plan={"deficit": 1})
    assert passed["status"] == "unavailable" and passed["completed_at"] is None
    assert passed["last_plan"]["deficit"] == 1

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

    marker = beast_abyss_active._arm_auto_batch(
        "current-occurrence", before, settings
    )
    with Session(engine) as session:
        stored = session.get(FanxiuExchangeActivity, "current-occurrence")
        state = stored.instance_data[beast_abyss_active.BEAST_ABYSS_INITIALIZATION_KEY]
        assert state["pending_batch"]["batch_id"] == marker["batch_id"]
        assert state.get("measurements") in (None, [])

    marker = beast_abyss_active._record_auto_batch_start_intent(
        "current-occurrence", marker
    )
    assert marker["start_click_intent_at"]
    with pytest.raises(RuntimeError, match="禁止重复点击"):
        beast_abyss_active._record_auto_batch_start_intent(
            "current-occurrence", marker
        )

    marker = beast_abyss_active._confirm_auto_batch_terminal(
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
    marker = beast_abyss_active._seal_auto_batch_after(
        "current-occurrence",
        marker,
        sealed_after,
        challenge_item_automatic=4,
    )
    assert marker["after"] == beast_abyss_active._jsonable_dataclass(sealed_after)
    assert marker["after_challenge_item_automatic"] == 4

    rows = beast_abyss_active._settle_auto_batch(
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
    with pytest.raises(RuntimeError, match="末次任务奖励"):
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
    final_only = dict(completed, first_reward_check=None)
    assert beast_abyss_active._initialization_state_complete(
        final_only, expected_activity_id="current-occurrence",
    )
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


@pytest.mark.parametrize(
    ("purchase_limit", "purchased_count", "wallet", "unit_price", "expected"),
    (
        (100, 20, 500, 10, 50),
        (100, 20, 2_000, 10, 80),
        (-1, 0, 500, 10, 50),
    ),
)
def test_exchange_dialog_maximum_respects_wallet_and_remaining_limit(
    purchase_limit: int,
    purchased_count: int,
    wallet: int,
    unit_price: int,
    expected: int,
) -> None:
    row = SimpleNamespace(
        purchase_limit=purchase_limit,
        purchased_count=purchased_count,
    )

    assert beast_abyss_exchange._exchange_dialog_maximum(
        row,
        wallet=wallet,
        unit_price=unit_price,
    ) == expected


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




def test_formal_journal_authorizes_variable_count_once_and_preserves_initialization(monkeypatch, tmp_path):
    from backend.core.fanxiu.activity.beast_abyss_challenge_planning import measure_beast_abyss_completed_batch
    engine = create_engine(f"sqlite:///{tmp_path / 'formal.db'}")
    SQLModel.metadata.create_all(engine)
    initial = {"sentinel": "keep"}
    activity = FanxiuExchangeActivity(id="current-occurrence", activity_type="beast-abyss",
        instance_key="runtime:beast:current", family="gameplay_rank", runtime_id="8150001400004",
        game_activity_id=8150001, cross_count=8, prepare_at="2026-09-27T00:00:00+08:00",
        start_at="2026-09-27T10:00:00+08:00", end_at="2026-09-27T22:00:00+08:00",
        close_at="2026-09-28T23:59:00+08:00", start_date="2026-09-27", end_date="2026-09-27",
        instance_data={beast_abyss_active.BEAST_ABYSS_INITIALIZATION_KEY: initial})
    with Session(engine) as session:
        session.add(activity)
        session.commit()
    monkeypatch.setattr("backend.db.engine", engine)
    key = beast_abyss_active.BEAST_ABYSS_FORMAL_KEY
    before, after = _ledger(), _ledger(cumulative_currency=3800, current_currency=3800, personal_score=124)
    settings = BeastAbyssAutoSettings(False, True, True, True, False, True, True, 37)
    marker = beast_abyss_active._arm_auto_batch("current-occurrence", before, settings, state_key=key)
    marker = beast_abyss_active._record_auto_batch_start_intent("current-occurrence", marker, state_key=key)
    with pytest.raises(RuntimeError, match="禁止重复点击"):
        beast_abyss_active._record_auto_batch_start_intent("current-occurrence", marker, state_key=key)
    marker = beast_abyss_active._confirm_auto_batch_terminal("current-occurrence", marker,
        terminal_scene=382, duration_seconds=10, duration_reliable=True, state_key=key)
    marker = beast_abyss_active._seal_auto_batch_after("current-occurrence", marker, after,
        challenge_item_automatic=4, state_key=key)
    sample = measure_beast_abyss_completed_batch(before, after, requested_explores=37,
        completed_explores=37, duration_seconds=10)
    for _ in range(2):
        rows = beast_abyss_active._settle_auto_batch("current-occurrence", marker, sample, state_key=key)
        assert len(rows) == 1 and rows[0].requested_explores == 37
    with pytest.raises(RuntimeError, match="重复结算内容冲突"):
        beast_abyss_active._settle_auto_batch("current-occurrence", marker,
            replace(sample, personal_score_delta=75), state_key=key)
    with Session(engine) as session:
        data = session.get(FanxiuExchangeActivity, "current-occurrence").instance_data
        assert data[beast_abyss_active.BEAST_ABYSS_INITIALIZATION_KEY] == initial
        assert data[key]["pending_batch"] is None
        assert data[key]["settled_batch_ids"] == [marker["batch_id"]]
    aborted = beast_abyss_active._arm_auto_batch("current-occurrence", after,
        replace(settings, requested_explores=12), state_key=key)
    beast_abyss_active._record_beast_abyss_resource_stop("current-occurrence", aborted,
        state_key=key, terminal_scene=382)
    with Session(engine) as session:
        state = session.get(FanxiuExchangeActivity, "current-occurrence").instance_data[key]
        assert state["pending_batch"] is None and len(state["measurements"]) == 1
        assert state["aborted_batches"][0]["batch_id"] == aborted["batch_id"]
