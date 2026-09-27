from __future__ import annotations

"""Occurrence-scoped Beast Abyss initialization and active-run coordinator."""

from dataclasses import asdict, replace
from datetime import datetime
from fractions import Fraction
import threading
import time
from typing import Any, Iterator, Mapping, Sequence
import uuid

from sqlmodel import Session, select

from backend.core.fanxiu.activity.beast_abyss import (
    BEAST_ABYSS_INITIALIZATION_STATE_KEY,
)
from backend.core.fanxiu.activity.beast_abyss_challenge_planning import (
    BEAST_ABYSS_MEASUREMENT_EXPLORES,
    BeastAbyssBatchMeasurement,
    BeastAbyssResourceLedger,
    build_beast_abyss_shop_snapshot_key,
    build_beast_abyss_yield_scatter_model,
    measure_beast_abyss_completed_batch,
    plan_beast_abyss_formal_batch,
    plan_beast_abyss_measurement_batch,
)
from backend.core.fanxiu.data_annotation.tasks.beast_abyss_native_auto import (
    DEFAULT_BEAST_ABYSS_NATIVE_AUTO_ASSETS,
    BeastAbyssAutoTerminal,
    BeastAbyssAutoSettings,
    BeastAbyssNativeAutoRequest,
    enter_beast_abyss_explore,
    enter_beast_abyss_explore_and_claim_rewards,
    classify_beast_abyss_auto_terminal,
    prepare_beast_abyss_native_auto,
    read_beast_abyss_native_auto_settings,
    run_prepared_beast_abyss_native_auto,
)
from backend.core.fanxiu.data_annotation.tasks.beast_abyss_task_rewards import (
    claim_beast_abyss_task_rewards,
)
from backend.models import FanxiuExchangeActivity


BEAST_ABYSS_INITIALIZATION_KEY = BEAST_ABYSS_INITIALIZATION_STATE_KEY
BEAST_ABYSS_FORMAL_KEY = "beast_abyss_formal"
BEAST_ABYSS_INITIALIZATION_MAX_BATCHES = 1


class BeastAbyssBatchResourceInsufficient(RuntimeError):
    """A full authorized batch cannot be funded; no smaller batch is started."""



def validate_beast_abyss_occurrence(
    occurrence: Any,
    activities: Sequence[FanxiuExchangeActivity],
) -> FanxiuExchangeActivity:
    if str(getattr(occurrence, "activity_type", "")) != "beast-abyss":
        raise RuntimeError("兽渊 checkpoint 收到非兽渊 occurrence")
    matches = [
        activity
        for activity in activities
        if activity.activity_type == "beast-abyss"
        and str(activity.runtime_id or "") == str(occurrence.runtime_id or "")
        and str(activity.instance_key or "") == str(occurrence.instance_key or "")
        and int(activity.game_activity_id or 0) == int(occurrence.activity_id)
        and int(activity.cross_count or 0) == int(occurrence.cross_count)
        and str(activity.start_date or "") == occurrence.start_at.date().isoformat()
        and str(activity.end_date or "") == occurrence.end_at.date().isoformat()
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "兽渊 occurrence 无法唯一对齐本期实例："
            f"activity_id={occurrence.activity_id}, cross={occurrence.cross_count}, "
            f"matches={len(matches)}"
        )
    return matches[0]


def _load_activity(occurrence: Any) -> FanxiuExchangeActivity:
    from backend.db import engine

    with Session(engine) as session:
        activities = list(session.exec(select(FanxiuExchangeActivity).where(
            FanxiuExchangeActivity.activity_type == "beast-abyss"
        )).all())
        activity = validate_beast_abyss_occurrence(occurrence, activities)
        session.expunge(activity)
        return activity


def _load_detail(activity_id: str) -> Any:
    from backend.core.fanxiu.activity.exchange_event import (
        list_exchange_activity_snapshot,
    )
    from backend.db import engine

    with Session(engine) as session:
        detail = list_exchange_activity_snapshot(
            session,
            activity_type="beast-abyss",
            activity_id=activity_id,
        ).selected_activity
    return _validate_initialization_detail(detail, activity_id)


def _validate_initialization_detail(detail: Any, activity_id: str) -> Any:
    if detail is None or str(detail.id) != str(activity_id):
        raise RuntimeError("兽渊兑换宝阁实例发生切换")
    # Historical wallet freshness belongs to challenge planning, not to the
    # initialization entry. Here we only need an occurrence-bound, complete
    # shop identity; fresh wallet/ranking facts are sampled after navigation.
    if not detail.shop_items:
        raise RuntimeError("兽渊本期兑换宝阁快照不完整")
    _shop_snapshot_key(detail)
    return detail


def _shop_snapshot_key(detail: Any) -> str:
    return build_beast_abyss_shop_snapshot_key(
        (item.model_dump() for item in detail.shop_items),
        captured_at=(
            str(detail.shop_snapshot_captured_at or "")
            or str(detail.captured_at or "")
        ),
    )


def _read_ledger(
    activity: FanxiuExchangeActivity,
    detail: Any,
    *,
    ranking_snapshot: Mapping[str, Any] | None = None,
    personal_score_override: int | None = None,
    personal_rank_object_identity_override: str = "",
) -> tuple[BeastAbyssResourceLedger, dict[str, Any]]:
    from backend.core.fanxiu.instrumentation.beast_abyss_runtime import (
        read_beast_abyss_budget_snapshot,
    )
    from backend.core.fanxiu.instrumentation.wallet import (
        read_wallet_currency_snapshot,
    )
    budget = read_beast_abyss_budget_snapshot()
    # The initialized game wallet omits an event currency until its first gain.
    wallet = read_wallet_currency_snapshot(14, allow_discovery=False, missing_as_zero=True)
    if wallet.get("source") != "runtime_memory":
        raise RuntimeError("兽渊钱包没有提供 Runtime 实时事实")
    rank_activity_id = int(activity.game_rank_activity_id or 0)
    personal_identity = dict(
        dict(activity.evidence or {}).get("rank_scope_identities", {}).get("personal")
        or {}
    )
    if rank_activity_id <= 0 or int(personal_identity.get("runtime_rank_activity_id") or 0) != rank_activity_id:
        raise RuntimeError("兽渊本期个人榜 Runtime 身份未得到实例证据")
    wallet_process = dict(wallet.get("evidence") or {})
    budget_process = dict(budget.get("evidence") or {})
    if not budget_process and ranking_snapshot is not None:
        budget_process = dict(ranking_snapshot.get("evidence") or {})
    if (
        int(wallet_process.get("pid") or 0) != int(budget_process.get("pid") or 0)
        or int(wallet_process.get("process_start_ticks") or 0)
        != int(budget_process.get("process_start_ticks") or 0)
    ):
        raise RuntimeError("兽渊兑币与个人积分来自不同游戏进程")
    personal_rank = dict(budget.get("personal_rank") or {})
    if ranking_snapshot is not None:
        if not (
            ranking_snapshot.get("ok")
            and ranking_snapshot.get("complete")
            and int(ranking_snapshot.get("rank_activity_id") or 0)
            == rank_activity_id
        ):
            raise RuntimeError("兽渊个人榜刷新快照不完整或实例不一致")
        personal_rank = {
            **dict(ranking_snapshot.get("self_ranking") or {}),
            "runtime_object_identity": str(
                ranking_snapshot.get("runtime_object_identity") or ""
            ),
        }
    explore_config = dict(budget["count_configs"][1])
    challenge_config = dict(budget["count_configs"][2])
    item_counts = dict(budget.get("supplement_item_counts") or {})
    explore_item_id = int(explore_config.get("supplement_item_id") or 0)
    challenge_item_id = int(challenge_config.get("supplement_item_id") or 0)
    ledger = BeastAbyssResourceLedger(
        activity_instance_id=str(activity.id),
        shop_snapshot_key=_shop_snapshot_key(detail),
        hierarchy=int(budget["current_hierarchy"]),
        cumulative_currency=int(wallet["cumulative_currency"]),
        current_currency=int(wallet["exchange_currency"]),
        explore_points=int(budget["explore"]["count"]),
        explore_items=int(item_counts.get(explore_item_id, 0)),
        challenge_points=int(budget["challenge"]["count"]),
        challenge_items=int(item_counts.get(challenge_item_id, 0)),
        personal_score=(
            int(personal_score_override)
            if personal_score_override is not None
            else int(personal_rank.get("score") or 0)
        ),
        personal_rank_object_identity=(
            str(personal_rank_object_identity_override or "")
            if personal_score_override is not None
            else str(personal_rank.get("runtime_object_identity") or "")
        ),
    )
    return ledger, {**budget, "baseline": {"wallet": wallet, "personal_rank": personal_rank}}


def _jsonable_dataclass(value: Any) -> dict[str, Any]:
    return {
        key: (str(current) if isinstance(current, Fraction) else current)
        for key, current in asdict(value).items()
    }


def _measurement_from_dict(raw: Mapping[str, Any]) -> BeastAbyssBatchMeasurement:
    values = dict(raw)
    values.pop("batch_id", None)
    # Rows written before the crash-safe timing protocol cannot prove that
    # their duration excludes downtime. Fail closed for speed modeling.
    values.setdefault("duration_reliable", False)
    for key in ("currency_per_explore", "challenge_per_explore"):
        values[key] = Fraction(str(values[key]))
    return BeastAbyssBatchMeasurement(**values)


def _ledger_from_dict(raw: Mapping[str, Any]) -> BeastAbyssResourceLedger:
    """Restore a persisted ledger across the rank-identity field rename."""

    values = dict(raw)
    legacy_identity = values.pop("personal_rank_revision", "")
    values.setdefault("personal_rank_object_identity", str(legacy_identity or ""))
    return BeastAbyssResourceLedger(**values)


def _reward_check_complete(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and value.get("checked") is True
        and value.get("gui_opened") is True
        and isinstance(value.get("remaining_claimable"), (list, tuple))
        and not value.get("remaining_claimable")
    )


def _stored_scatter_model_matches(
    value: Any,
    measurements: Sequence[BeastAbyssBatchMeasurement],
) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        expected = build_beast_abyss_yield_scatter_model(measurements)
        points = tuple(
            tuple(int(component) for component in point)
            for point in value.get("points") or ()
        )
        transitions = tuple(
            tuple(int(component) for component in point)
            for point in value.get("hierarchy_transitions") or ()
        )
        hierarchy = int(value.get("hierarchy") or 0)
        seconds_per_explore = (None if value.get("seconds_per_explore") is None
                               else float(value["seconds_per_explore"]))
    except (KeyError, TypeError, ValueError):
        return False
    return bool(
        points == expected.points
        and str(value.get("currency_per_explore"))
        == str(expected.currency_per_explore)
        and str(value.get("personal_score_per_explore"))
        == str(expected.personal_score_per_explore)
        and str(value.get("challenge_per_explore"))
        == str(expected.challenge_per_explore)
        and seconds_per_explore == expected.seconds_per_explore
        and transitions == expected.hierarchy_transitions
        and str(value.get("activity_instance_id") or "")
        == expected.activity_instance_id
        and str(value.get("shop_snapshot_key") or "")
        == expected.shop_snapshot_key
        and hierarchy == expected.hierarchy
    )


def _initialization_state_complete(
    state: Mapping[str, Any],
    *,
    expected_activity_id: str | None = None,
) -> bool:
    if (
        state.get("status") != "completed"
        or state.get("pending_batch") is not None
    ):
        return False
    try:
        if int(state.get("batch_size") or 0) != BEAST_ABYSS_MEASUREMENT_EXPLORES:
            return False
        raw_measurements = state.get("measurements") or ()
        if not isinstance(raw_measurements, (list, tuple)) or not all(
            isinstance(item, Mapping) for item in raw_measurements
        ):
            return False
        measurements = [
            _measurement_from_dict(item)
            for item in raw_measurements
        ]
        if expected_activity_id is not None and (
            str(state.get("activity_instance_id") or "")
            != str(expected_activity_id)
            or any(
                str(item.activity_instance_id) != str(expected_activity_id)
                for item in measurements
            )
        ):
            return False
        sampled = (
            len(measurements) >= 1
            and all(
                item.requested_explores == BEAST_ABYSS_MEASUREMENT_EXPLORES
                and item.completed_explores == BEAST_ABYSS_MEASUREMENT_EXPLORES
                for item in measurements
            )
        )
    except (KeyError, TypeError, ValueError):
        return False
    return bool(
        sampled
        and _reward_check_complete(state.get("final_reward_check"))
        and _stored_scatter_model_matches(state.get("model"), measurements)
    )


def _formal_measurements(activity: FanxiuExchangeActivity) -> list[BeastAbyssBatchMeasurement]:
    state = dict(dict(activity.instance_data or {}).get(BEAST_ABYSS_FORMAL_KEY) or {})
    rows = state.get("measurements") or ()
    if not isinstance(rows, (list, tuple)) or not all(
        isinstance(item, Mapping) for item in rows
    ):
        raise RuntimeError("兽渊正式运行散点记录损坏")
    measurements = [_measurement_from_dict(item) for item in rows]
    if any(
        str(item.activity_instance_id) != str(activity.id)
        for item in measurements
    ):
        raise RuntimeError("兽渊正式运行散点混入其他 occurrence")
    return measurements


def _initialization_measurements(
    activity: FanxiuExchangeActivity,
) -> list[BeastAbyssBatchMeasurement]:
    state = dict(
        dict(activity.instance_data or {}).get(BEAST_ABYSS_INITIALIZATION_KEY) or {}
    )
    if not _initialization_state_complete(
        state,
        expected_activity_id=str(activity.id),
    ):
        raise RuntimeError("兽渊10:05正式运行要求本期10:00初始化已完成")
    return [
        _measurement_from_dict(item)
        for item in state.get("measurements") or ()
    ]


def _rebase_formal_exchange_plan(
    detail: Any,
    ledger: BeastAbyssResourceLedger,
) -> dict[str, Any]:
    """Combine retained shop rows with the current live currency ledgers."""

    from backend.core.fanxiu.activity.exchange_challenge_planning import exchange_challenge_milestones
    return {"budget_ready": True, "milestones": exchange_challenge_milestones(
        [row.model_dump() if hasattr(row, "model_dump") else dict(row) for row in detail.shop_items]
    )}


def _persist_formal_progress(
    activity_id: str,
    initialization_measurements: Sequence[BeastAbyssBatchMeasurement],
    formal_measurements: Sequence[BeastAbyssBatchMeasurement],
    *,
    status: str,
    terminal_reason: str = "",
    last_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist formal samples separately while fitting one combined model."""

    from backend.db import engine

    if status not in {"in_progress", "completed", "unavailable", "deferred"}:
        raise ValueError(f"兽渊正式运行状态无效：{status}")
    combined = [*initialization_measurements, *formal_measurements]
    if not combined or any(
        str(item.activity_instance_id) != str(activity_id) for item in combined
    ):
        raise RuntimeError("兽渊正式运行模型与目标 occurrence 不一致")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    state = {
        "status": status,
        "measurements": [_jsonable_dataclass(item) for item in formal_measurements],
        "model": _jsonable_dataclass(build_beast_abyss_yield_scatter_model(combined)),
        "terminal_reason": str(terminal_reason or ""),
        "last_plan": dict(last_plan or {}),
        "updated_at": now,
        "completed_at": now if status == "completed" else None,
    }
    with Session(engine) as session:
        activity = session.get(FanxiuExchangeActivity, activity_id)
        if activity is None or activity.activity_type != "beast-abyss":
            raise RuntimeError("兽渊正式运行失去本期实例写入目标")
        current_initialization = dict(
            dict(activity.instance_data or {}).get(BEAST_ABYSS_INITIALIZATION_KEY)
            or {}
        )
        if not _initialization_state_complete(
            current_initialization,
            expected_activity_id=str(activity.id),
        ):
            raise RuntimeError("兽渊正式运行写入前发现初始化状态失效")
        state.update({
            "activity_instance_id": str(activity.id),
            "instance_key": str(activity.instance_key or ""),
            "runtime_id": str(activity.runtime_id or ""),
        })
        instance_data = dict(activity.instance_data or {})
        previous = dict(instance_data.get(BEAST_ABYSS_FORMAL_KEY) or {})
        if previous.get("pending_batch"):
            raise RuntimeError("兽渊正式批次尚未结算，拒绝覆盖证据")
        state["settled_batch_ids"] = list(previous.get("settled_batch_ids") or [])
        state["pending_batch"] = None
        state["aborted_batches"] = list(previous.get("aborted_batches") or [])
        for row, previous_row in zip(state["measurements"], previous.get("measurements") or []):
            if previous_row.get("batch_id"):
                row["batch_id"] = previous_row["batch_id"]
        instance_data[BEAST_ABYSS_FORMAL_KEY] = state
        activity.instance_data = instance_data
        activity.updated_at = time.time()
        session.add(activity)
        session.commit()
    return state


def _persist_initialization_progress(
    activity_id: str,
    measurements: Sequence[BeastAbyssBatchMeasurement],
    *,
    completed: bool,
    first_reward_check: Mapping[str, Any] | None = None,
    final_reward_check: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from backend.db import engine

    rows = [_jsonable_dataclass(item) for item in measurements]
    if any(
        str(item.activity_instance_id) != str(activity_id)
        for item in measurements
    ):
        raise RuntimeError("兽渊初始化样本与目标 occurrence 不一致")
    if completed:
        if any(
            item.requested_explores != BEAST_ABYSS_MEASUREMENT_EXPLORES
            or item.completed_explores != BEAST_ABYSS_MEASUREMENT_EXPLORES
            for item in measurements
        ):
            raise RuntimeError("兽渊初始化测速批次必须为完整100次")
        if not measurements:
            raise RuntimeError("兽渊首次100次样本尚未完成")
        if not _reward_check_complete(final_reward_check):
            raise RuntimeError("兽渊末次任务奖励未保底检查至无待领取")
    model = (
        build_beast_abyss_yield_scatter_model(measurements)
        if measurements
        else None
    )
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    state = {
        "status": "completed" if completed else "in_progress",
        "batch_size": BEAST_ABYSS_MEASUREMENT_EXPLORES,
        "measurements": rows,
        "model": _jsonable_dataclass(model) if model is not None else None,
        "first_reward_check": dict(first_reward_check) if first_reward_check else None,
        "final_reward_check": dict(final_reward_check) if completed else None,
        "updated_at": now,
        "completed_at": now if completed else None,
    }
    with Session(engine) as session:
        activity = session.get(FanxiuExchangeActivity, activity_id)
        if activity is None or activity.activity_type != "beast-abyss":
            raise RuntimeError("兽渊测速结果失去本期实例写入目标")
        state.update({
            "activity_instance_id": str(activity.id),
            "instance_key": str(activity.instance_key or ""),
            "runtime_id": str(activity.runtime_id or ""),
        })
        instance_data = dict(activity.instance_data or {})
        previous = dict(instance_data.get(BEAST_ABYSS_INITIALIZATION_KEY) or {})
        if completed and previous.get("pending_batch") is not None:
            raise RuntimeError("兽渊仍有未结批次，拒绝标记初始化完成")
        previous_rows = [
            dict(item)
            for item in previous.get("measurements") or ()
            if isinstance(item, Mapping)
        ]
        for index, row in enumerate(rows):
            if index >= len(previous_rows):
                break
            previous_row = previous_rows[index]
            try:
                same_measurement = (
                    _measurement_from_dict(previous_row) == measurements[index]
                )
            except (KeyError, TypeError, ValueError):
                same_measurement = False
            batch_id = str(previous_row.get("batch_id") or "")
            if same_measurement and batch_id:
                row["batch_id"] = batch_id
        if not state.get("first_reward_check"):
            previous_first = previous.get("first_reward_check")
            state["first_reward_check"] = (
                dict(previous_first) if isinstance(previous_first, Mapping) else None
            )
        state["settled_batch_ids"] = list(previous.get("settled_batch_ids") or ())
        state["aborted_batches"] = list(previous.get("aborted_batches") or [])
        state["pending_batch"] = previous.get("pending_batch")
        instance_data[BEAST_ABYSS_INITIALIZATION_KEY] = state
        activity.instance_data = instance_data
        activity.updated_at = time.time()
        session.add(activity)
        session.commit()
    return state


def _arm_auto_batch(
    activity_id: str,
    before: BeastAbyssResourceLedger,
    settings: BeastAbyssAutoSettings,
    state_key: str = BEAST_ABYSS_INITIALIZATION_KEY,
) -> dict[str, Any]:
    """Atomically persist and read back authorization before the irreversible click."""

    from backend.db import engine

    if str(before.activity_instance_id) != str(activity_id):
        raise RuntimeError("兽渊批次基线与授权 occurrence 不一致")
    if int(settings.requested_explores or 0) <= 0 or (state_key == BEAST_ABYSS_INITIALIZATION_KEY and settings.requested_explores != 100):
        raise RuntimeError("兽渊批次授权次数不是100")
    marker = {
        "protocol_version": 2,
        "batch_id": uuid.uuid4().hex,
        "target_explores": int(settings.requested_explores),
        "before": _jsonable_dataclass(before),
        "settings": asdict(settings),
        "armed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "armed_epoch": time.time(),
    }
    with Session(engine) as session:
        activity = session.get(FanxiuExchangeActivity, activity_id)
        if activity is None or activity.activity_type != "beast-abyss":
            raise RuntimeError("兽渊批次授权失去本期实例")
        instance_data = dict(activity.instance_data or {})
        state = dict(instance_data.get(state_key) or {})
        if isinstance(state.get("pending_batch"), Mapping):
            raise RuntimeError("兽渊仍有未结批次，拒绝覆盖防重证据")
        state.update({"status": "in_progress", "pending_batch": marker})
        instance_data[state_key] = state
        activity.instance_data = instance_data
        activity.updated_at = time.time()
        session.add(activity)
        session.commit()
        session.refresh(activity)
        persisted = dict(
            dict(activity.instance_data or {}).get(state_key)
            or {}
        ).get("pending_batch")
        if persisted != marker:
            raise RuntimeError("兽渊批次防重证据落库回读失败")
    return marker


def _record_auto_batch_start_intent(
    activity_id: str,
    marker: Mapping[str, Any],
    state_key: str = BEAST_ABYSS_INITIALIZATION_KEY,
) -> dict[str, Any]:
    """Durably cross the exactly-once boundary before clicking ``开启自动``.

    A crash after this write is deliberately ambiguous and must not be healed
    by clicking again.  A marker without the intent is still safe to start.
    """

    from backend.db import engine

    batch_id = str(marker.get("batch_id") or "")
    if not batch_id:
        raise RuntimeError("兽渊启动意图缺少 batch_id")
    if int(marker.get("protocol_version") or 0) < 2:
        raise RuntimeError("兽渊旧批次缺少启动防重协议，拒绝继续点击")
    with Session(engine) as session:
        activity = session.get(FanxiuExchangeActivity, activity_id)
        if activity is None or activity.activity_type != "beast-abyss":
            raise RuntimeError("兽渊启动意图失去本期实例")
        instance_data = dict(activity.instance_data or {})
        state = dict(instance_data.get(state_key) or {})
        pending = state.get("pending_batch")
        if not isinstance(pending, Mapping) or str(pending.get("batch_id") or "") != batch_id:
            raise RuntimeError("兽渊启动意图与当前防重标记不一致")
        updated = dict(pending)
        if updated.get("start_click_intent_at"):
            raise RuntimeError("兽渊开启自动点击结果不确定，禁止重复点击")
        updated.update({
            "start_click_intent_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "start_click_intent_epoch": time.time(),
        })
        state["pending_batch"] = updated
        instance_data[state_key] = state
        activity.instance_data = instance_data
        activity.updated_at = time.time()
        session.add(activity)
        session.commit()
        session.refresh(activity)
        persisted = dict(
            dict(
                dict(activity.instance_data or {}).get(
                    state_key
                )
                or {}
            ).get("pending_batch")
            or {}
        )
        if not persisted.get("start_click_intent_at"):
            raise RuntimeError("兽渊启动意图落库回读失败")
        return persisted


def _confirm_auto_batch_terminal(
    activity_id: str,
    marker: Mapping[str, Any],
    *,
    terminal_scene: int,
    duration_seconds: float | None = None,
    duration_reliable: bool = False,
    state_key: str = BEAST_ABYSS_INITIALIZATION_KEY,
) -> dict[str, Any]:
    """Persist terminal proof before leaving the result page.

    Rank data is refreshed only after the terminal is closed.  Recording this
    stage first lets a resumed attempt finish that refresh without replaying
    the irreversible native-auto batch.
    """

    from backend.db import engine

    batch_id = str(marker.get("batch_id") or "")
    if not batch_id:
        raise RuntimeError("兽渊终态确认缺少 batch_id")
    if int(terminal_scene) != 382:
        raise RuntimeError(f"兽渊终态确认场景无效：{terminal_scene}")
    with Session(engine) as session:
        activity = session.get(FanxiuExchangeActivity, activity_id)
        if activity is None or activity.activity_type != "beast-abyss":
            raise RuntimeError("兽渊终态确认失去本期实例")
        instance_data = dict(activity.instance_data or {})
        state = dict(instance_data.get(state_key) or {})
        pending = state.get("pending_batch")
        if not isinstance(pending, Mapping) or str(pending.get("batch_id") or "") != batch_id:
            raise RuntimeError("兽渊终态确认与当前防重标记不一致")
        updated = dict(pending)
        if not bool(updated.get("terminal_confirmed")):
            updated.update(
                {
                    "terminal_confirmed": True,
                    "terminal_scene": int(terminal_scene),
                    "terminal_confirmed_at": datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                    "duration_seconds": max(0.001, float(duration_seconds or 0.001)),
                    "duration_reliable": bool(duration_reliable),
                }
            )
            state["pending_batch"] = updated
            instance_data[state_key] = state
            activity.instance_data = instance_data
            activity.updated_at = time.time()
            session.add(activity)
            session.commit()
            session.refresh(activity)
            updated = dict(
                dict(
                    dict(activity.instance_data or {}).get(
                        state_key
                    )
                    or {}
                ).get("pending_batch")
                or {}
            )
        if not bool(updated.get("terminal_confirmed")):
            raise RuntimeError("兽渊终态确认落库回读失败")
        return updated


def _seal_auto_batch_after(
    activity_id: str,
    marker: Mapping[str, Any],
    after: BeastAbyssResourceLedger,
    *,
    challenge_item_automatic: int,
    state_key: str = BEAST_ABYSS_INITIALIZATION_KEY,
) -> dict[str, Any]:
    """Persist the final wallet/rank ledger before the batch is settled.

    Recovery must use this sealed ledger instead of reading mutable game state
    again; otherwise unrelated activity between terminal confirmation and DB
    settlement could be charged to this measurement batch.
    """

    from backend.db import engine

    batch_id = str(marker.get("batch_id") or "")
    if not batch_id:
        raise RuntimeError("兽渊批次终值缺少 batch_id")
    if str(after.activity_instance_id) != str(activity_id):
        raise RuntimeError("兽渊批次终值与本期实例不一致")
    with Session(engine) as session:
        activity = session.get(FanxiuExchangeActivity, activity_id)
        if activity is None or activity.activity_type != "beast-abyss":
            raise RuntimeError("兽渊批次终值失去本期实例")
        instance_data = dict(activity.instance_data or {})
        state = dict(instance_data.get(state_key) or {})
        pending = state.get("pending_batch")
        if not isinstance(pending, Mapping) or str(pending.get("batch_id") or "") != batch_id:
            raise RuntimeError("兽渊批次终值与当前防重标记不一致")
        updated = dict(pending)
        sealed_after = updated.get("after")
        if sealed_after is not None:
            if (
                dict(sealed_after) != _jsonable_dataclass(after)
                or int(updated.get("after_challenge_item_automatic") or 0)
                != int(challenge_item_automatic)
            ):
                raise RuntimeError("兽渊批次终值已经封存，拒绝覆盖")
            return updated
        updated.update({
            "after": _jsonable_dataclass(after),
            "after_challenge_item_automatic": int(challenge_item_automatic),
            "after_sealed_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
        })
        state["pending_batch"] = updated
        instance_data[state_key] = state
        activity.instance_data = instance_data
        activity.updated_at = time.time()
        session.add(activity)
        session.commit()
        session.refresh(activity)
        persisted = dict(
            dict(
                dict(activity.instance_data or {}).get(
                    state_key
                )
                or {}
            ).get("pending_batch")
            or {}
        )
        if dict(persisted.get("after") or {}) != _jsonable_dataclass(after):
            raise RuntimeError("兽渊批次终值落库回读失败")
        return persisted


def _read_settled_activity_rank_snapshot(
    context: Any,
    rank_activity_id: int,
    *,
    require_personal_score: bool,
    label: str,
    self_only: bool = False,
) -> Iterator[Any]:
    """Return two consecutive complete reads after one bounded settle window."""
    from backend.core.fanxiu.instrumentation.activity_rank_runtime import (
        prepare_activity_rank_runtime,
        read_activity_rank_runtime_snapshot,
        read_activity_rank_self_snapshot,
    )
    read_snapshot = read_activity_rank_self_snapshot if self_only else read_activity_rank_runtime_snapshot
    yield from context.wait_action_settle(2.0)
    snapshot: dict[str, Any] = {}
    stable_fingerprint: tuple[Any, ...] | None = None
    stable_reads = 0
    for _attempt in range(12):
        yield from context.wait_action_settle(1.0)
        snapshot = read_snapshot(rank_activity_id)
        if snapshot.get("error_code") in {
            "process_cache_miss",
            "root_cache_miss",
        }:
            loaded = prepare_activity_rank_runtime([rank_activity_id])
            if not loaded.get("ok"):
                snapshot = {"reason": loaded.get("reason")}
                continue
            snapshot = read_snapshot(rank_activity_id)
        raw_score = dict(snapshot.get("self_ranking") or {}).get("score")
        current_score = int(raw_score) if raw_score is not None else -1
        structurally_valid = bool(
            snapshot.get("ok")
            and snapshot.get("complete")
            and int(snapshot.get("rank_activity_id") or 0) == rank_activity_id
            and (not require_personal_score or current_score >= 0)
        )
        if not structurally_valid:
            stable_fingerprint = None
            stable_reads = 0
            continue
        self_ranking = dict(snapshot.get("self_ranking") or {})
        fingerprint = (
            current_score,
            self_ranking.get("rank"),
            self_ranking.get("role_id") or self_ranking.get("role_key"),
            int(snapshot.get("rank_list_size") or 0),
            int(snapshot.get("loaded_rank_count") or 0),
            int(snapshot.get("declared_rank_count") or 0),
        )
        if fingerprint == stable_fingerprint:
            stable_reads += 1
        else:
            stable_fingerprint = fingerprint
            stable_reads = 1
        if stable_reads >= 2:
            break
    else:
        raise RuntimeError(
            str(snapshot.get("reason") or f"{label} Runtime 快照未稳定")
        )
    return snapshot


def _enter_measurement_activity_home(context: Any, activity: FanxiuExchangeActivity) -> Iterator[Any]:
    """Select the exact calendar instance; a generic destination cannot identify its card."""
    from backend.core.fanxiu.data_annotation.schedule_navigation import (
        select_schedule_activity,
    )

    yield from context.go_scene(34)
    yield from context.go_scene(66)
    selected = yield from select_schedule_activity(
        context,
        r"兽渊探秘",
        enter=True,
        require_runtime_alignment=True,
        expected_activity_id=int(activity.game_activity_id or 0),
        expected_runtime_id=str(activity.runtime_id or ""),
        expected_cross_count=int(activity.cross_count or 0),
        now=datetime.now().astimezone(),
    )
    if not str(getattr(selected, "runtime_key", "") or ""):
        raise RuntimeError("兽渊测速：个人榜刷新后未回读精确 Runtime 实例")
    yield from context.wait_scene(
        [535],
        wait=30.0,
        label="兽渊测速：个人榜刷新后重入本期实例",
    )


def _refresh_personal_rank_for_measurement(
    context: Any,
    activity: FanxiuExchangeActivity,
) -> Iterator[Any]:
    """Request a personal-rank refresh, accept its settled value, and restore #657.

    ``ActivityrankMgr`` is a cache populated by ``CM_ActivityRankSync``.  A
    memory read alone proves only the cache contents, not that a just-finished
    challenge has refreshed them.  Every measurement therefore clicks the
    personal tab once, allows the response to settle, and accepts only two
    consecutive complete Runtime snapshots with the same personal result.
    An unchanged score is valid (the batch may legitimately yield zero points).
    """

    identities = dict(dict(activity.evidence or {}).get("rank_scope_identities") or {})
    rank_activity_id = int(
        dict(identities.get("personal") or {}).get("runtime_rank_activity_id")
        or activity.game_rank_activity_id
        or 0
    )
    if rank_activity_id <= 0:
        raise RuntimeError("兽渊测速缺少个人榜 Runtime 身份")

    yield from _enter_measurement_activity_home(context, activity)
    yield from context.wait_click_then_scene(
        535,
        "兽渊榜",
        537,
        timeout=20.0,
        label="兽渊测速：进入个人榜刷新积分",
    )
    context.click_shape_center(537, "个人")
    # ActivityRankMainView sends CM_ActivityRankSync when the tab opens, but
    # ActivityrankData keeps its previous object until the response arrives.
    # There is no response revision exposed in this Runtime model, so do not
    # mistake object presence/identity or a score increase for freshness.
    snapshot = yield from _read_settled_activity_rank_snapshot(
        context,
        rank_activity_id,
        require_personal_score=True,
        label="兽渊测速：个人榜最终积分",
        self_only=True,
    )
    context.click_shape_center(537, "返回")
    yield from context.wait_scene(
        [34],
        wait=30.0,
        label="兽渊测速：个人榜刷新后返回世界",
    )
    yield from _enter_measurement_activity_home(context, activity)
    yield from enter_beast_abyss_explore(
        context, DEFAULT_BEAST_ABYSS_NATIVE_AUTO_ASSETS
    )
    return snapshot


def _settle_auto_batch(
    activity_id: str,
    marker: Mapping[str, Any],
    measurement: BeastAbyssBatchMeasurement,
    state_key: str = BEAST_ABYSS_INITIALIZATION_KEY,
) -> list[BeastAbyssBatchMeasurement]:
    """Append one batch and clear exactly its marker in one DB transaction."""

    from backend.db import engine

    batch_id = str(marker.get("batch_id") or "")
    if not batch_id:
        raise RuntimeError("兽渊未结批次缺少 batch_id")
    if (measurement.requested_explores != int(marker.get("target_explores") or 0)
            or measurement.completed_explores != measurement.requested_explores):
        raise RuntimeError("兽渊结算次数与授权不一致")
    marked_before = _ledger_from_dict(dict(marker.get("before") or {}))
    if (
        str(marked_before.activity_instance_id) != str(activity_id)
        or measurement.activity_instance_id != marked_before.activity_instance_id
        or measurement.shop_snapshot_key != marked_before.shop_snapshot_key
    ):
        raise RuntimeError("兽渊批次结算与授权基线身份不一致")
    with Session(engine) as session:
        activity = session.get(FanxiuExchangeActivity, activity_id)
        if activity is None or activity.activity_type != "beast-abyss":
            raise RuntimeError("兽渊批次结算失去本期实例")
        instance_data = dict(activity.instance_data or {})
        state = dict(instance_data.get(state_key) or {})
        pending = state.get("pending_batch")
        settled_ids = [str(value) for value in state.get("settled_batch_ids") or ()]
        rows = [dict(item) for item in state.get("measurements") or ()]
        if batch_id in settled_ids:
            if isinstance(pending, Mapping) and str(pending.get("batch_id") or "") == batch_id:
                raise RuntimeError("兽渊批次已结算但防重标记未清除")
            stored = next((item for item in rows if item.get("batch_id") == batch_id), None)
            if stored is None or _measurement_from_dict(stored) != measurement:
                raise RuntimeError("兽渊重复结算内容冲突")
            return [_measurement_from_dict(item) for item in rows]
        if not isinstance(pending, Mapping) or str(pending.get("batch_id") or "") != batch_id:
            raise RuntimeError("兽渊批次结算与当前防重标记不一致")
        if not pending.get("terminal_confirmed") or not pending.get("after"):
            raise RuntimeError("兽渊批次缺少终态或封存台账，拒绝结算")
        rows.append({**_jsonable_dataclass(measurement), "batch_id": batch_id})
        settled_measurements = [_measurement_from_dict(item) for item in rows]
        state.update({
            "status": "in_progress",
            "measurements": rows,
            "model": _jsonable_dataclass(
                build_beast_abyss_yield_scatter_model(settled_measurements)
            ),
            "settled_batch_ids": [*settled_ids, batch_id],
            "pending_batch": None,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
        instance_data[state_key] = state
        activity.instance_data = instance_data
        activity.updated_at = time.time()
        session.add(activity)
        session.commit()
    return [_measurement_from_dict(item) for item in rows]


def _ensure_entry_surface(context: Any) -> Iterator[Any]:
    assets = DEFAULT_BEAST_ABYSS_NATIVE_AUTO_ASSETS
    accepted = (
        assets.home_scene_id,
        assets.explore_scene_id,
        assets.cutscene_scene_id,
        assets.skip_confirm_scene_id,
        assets.npc_entry_scene_id,
        assets.region_map_scene_id,
    )
    _wait_scene_match = yield from context.wait_scene(accepted, wait=5.0, required=False)
    (scene_id, _score, _frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if scene_id not in accepted:
        yield from context.go_scene(assets.home_scene_id)


def _close_auto_terminal(context: Any, scene_id: int | None) -> Iterator[Any]:
    assets = DEFAULT_BEAST_ABYSS_NATIVE_AUTO_ASSETS
    if scene_id != 382:
        raise RuntimeError(f"兽渊自动批次没有停在已验证终态：scene={scene_id!r}")
    _wait_scene_match = yield from context.wait_scene((382,), wait=5.0, required=False)
    (observed, _score, frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if observed != 382 or "点击屏幕关闭" not in context.ocr_text(frame).replace(" ", ""):
        raise RuntimeError("兽渊#382完成页缺少完整完成证据，拒绝关闭")
    context.click_shape_center(382, "关闭")
    for _attempt in range(20):
        yield from context.wait_action_settle(0.5)
        _wait_scene_match = yield from context.wait_scene((382,), wait=5.0, required=False)
        (observed, _score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if observed != 382:
            return
    raise RuntimeError("兽渊#382完成页点击关闭后仍未离开")


def _record_beast_abyss_resource_stop(activity_id, marker, *, state_key, terminal_scene):
    """Keep failed-batch evidence without inventing a complete yield sample."""
    from backend.db import engine
    with Session(engine) as session:
        activity = session.get(FanxiuExchangeActivity, activity_id)
        if activity is None or activity.activity_type != "beast-abyss":
            raise RuntimeError("兽渊资源终态失去本期实例")
        data = dict(activity.instance_data or {})
        state = dict(data.get(state_key) or {})
        pending = state.get("pending_batch")
        if not pending or pending.get("batch_id") != marker.get("batch_id"):
            raise RuntimeError("兽渊资源终态与授权批次不一致")
        aborted = {**pending, "terminal_reason": "resource_exhausted", "terminal_scene": terminal_scene}
        state.update(pending_batch=None, status="unavailable", terminal_reason="resource_exhausted",
                     aborted_batches=[*(state.get("aborted_batches") or []), aborted])
        data[state_key] = state
        activity.instance_data = data
        session.add(activity)
        session.commit()


def _execute_or_recover_auto_batch(
    context: Any,
    activity: FanxiuExchangeActivity,
    detail: Any,
    request: BeastAbyssNativeAutoRequest,
    *,
    pending: Mapping[str, Any] | None,
    state_key: str = BEAST_ABYSS_INITIALIZATION_KEY,
) -> Iterator[Any]:
    """Run/recover either phase using its isolated authorization journal."""

    assets = DEFAULT_BEAST_ABYSS_NATIVE_AUTO_ASSETS
    prepared_here = pending is None
    observed_duration_seconds: float | None = None
    observed_duration_reliable = False
    if pending is None:
        baseline_rank = yield from _refresh_personal_rank_for_measurement(
            context,
            activity,
        )
        before, budget = _read_ledger(
            activity,
            detail,
            ranking_snapshot=baseline_rank,
        )
        explore_config = dict(budget["count_configs"][1])
        challenge_config = dict(budget["count_configs"][2])
        if request.measurement:
            if int(budget["capacity"]["explore_attempts_with_items"]) < 100:
                raise BeastAbyssBatchResourceInsufficient("兽渊首次100次探查资源不足，本轮pass")
            plan_beast_abyss_measurement_batch(
                before, hierarchy_consume=int(budget["capacity"]["max_explore_cost"]),
                explore_item_automatic=int(explore_config.get("automatic") or 0),
                challenge_item_automatic=int(challenge_config.get("automatic") or 0))
        elif int(budget["capacity"]["explore_attempts_with_items"]) < request.requested_explores:
            raise BeastAbyssBatchResourceInsufficient("兽渊规划后探查资源减少，整轮停止")
        request = replace(
            request,
            maximum_explores=int(budget["capacity"]["explore_attempts_with_items"]),
        )
        _wait_scene_match = yield from context.wait_scene((assets.help_view_scene_id,), wait=5.0, required=False)
        (current_scene, _score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if current_scene == assets.help_view_scene_id:
            # R&D/resume path: #658 has already been configured and visually
            # confirmed.  Reuse its fully read-back contract instead of leaving
            # the page and replaying the upstream navigation/configuration.
            settings = read_beast_abyss_native_auto_settings(
                context, assets, measurement=request.measurement
            )
        else:
            settings = yield from prepare_beast_abyss_native_auto(
                context, assets, request
            )
        if settings.requested_explores != request.requested_explores:
            raise RuntimeError("兽渊配置次数与本轮规划不一致，未授权启动")
        marker = _arm_auto_batch(str(activity.id), before, settings, state_key=state_key)
    else:
        marker = dict(pending)
        if int(marker.get("target_explores") or 0) != request.requested_explores:
            raise RuntimeError("兽渊未结批次次数与初始化契约不一致")
        before = _ledger_from_dict(dict(marker.get("before") or {}))
        settings = BeastAbyssAutoSettings(**dict(marker.get("settings") or {}))

    terminal_scene = int(marker.get("terminal_scene") or 0) or None
    if not bool(marker.get("terminal_confirmed")):
        _wait_scene_match = yield from context.wait_scene((assets.help_view_scene_id, assets.completed_notice_scene_id, *assets.terminal_scene_ids), wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if (int(marker.get("protocol_version") or 0) >= 2
                and not marker.get("start_click_intent_at")
                and scene_id != assets.help_view_scene_id):
            raise RuntimeError("兽渊未记录启动意图却已离开配置页，不能归因到本批次")
        if scene_id == assets.help_view_scene_id:
            current, _ = _read_ledger(
                activity,
                detail,
                personal_score_override=before.personal_score,
                personal_rank_object_identity_override=(
                    before.personal_rank_object_identity
                ),
            )
            if current != before:
                raise RuntimeError(
                    "兽渊未结批次仍在#658，但 Runtime 事实已变化；禁止重复点击"
                )
            current_settings = read_beast_abyss_native_auto_settings(
                context, assets, measurement=request.measurement
            )
            if current_settings != settings:
                raise RuntimeError("兽渊未结批次#658配置已变化，禁止重复点击")
            if not prepared_here and int(marker.get("protocol_version") or 0) < 2:
                raise RuntimeError("兽渊旧批次启动状态不确定，禁止重复点击")
            marker = _record_auto_batch_start_intent(
                str(activity.id), marker, state_key=state_key
            )
            started_at = time.perf_counter()
            result = yield from run_prepared_beast_abyss_native_auto(
                context, assets, request, settings
            )
            observed_duration_seconds = max(0.001, time.perf_counter() - started_at)
            observed_duration_reliable = True
            if result.terminal is BeastAbyssAutoTerminal.RESOURCE_EXHAUSTED:
                _record_beast_abyss_resource_stop(str(activity.id), marker, state_key=state_key,
                                                 terminal_scene=result.scene_id)
                if result.scene_id == 382:
                    yield from _close_auto_terminal(context, 382)
                raise BeastAbyssBatchResourceInsufficient("兽渊运行中资源耗尽；记录未完整批次，本轮pass")
            if result.terminal is not BeastAbyssAutoTerminal.COMPLETED:
                raise RuntimeError(f"兽渊批次未完整完成，保留授权证据：{result.terminal}")
            terminal_scene = result.scene_id
        elif scene_id == assets.completed_notice_scene_id:
            terminal = classify_beast_abyss_auto_terminal(context.ocr_text(frame))
            if terminal is not BeastAbyssAutoTerminal.COMPLETED:
                raise RuntimeError("兽渊#662完成弹窗缺少完整完成证据；保留标记")
            landed = yield from context.wait_click_then_scene(
                assets.completed_notice_scene_id,
                assets.completed_notice_confirm,
                *assets.terminal_scene_ids,
                timeout=20.0,
                label="兽渊初始化恢复：确认完成弹窗进入结果页",
            )
            terminal_scene = int(getattr(landed, "id", landed))
        elif scene_id in assets.terminal_scene_ids:
            terminal = classify_beast_abyss_auto_terminal(context.ocr_text(frame))
            if terminal is BeastAbyssAutoTerminal.RESOURCE_EXHAUSTED:
                _record_beast_abyss_resource_stop(str(activity.id), marker, state_key=state_key,
                                                 terminal_scene=scene_id)
                yield from _close_auto_terminal(context, int(scene_id))
                raise BeastAbyssBatchResourceInsufficient("兽渊恢复时确认资源耗尽，本轮pass")
            if terminal is not BeastAbyssAutoTerminal.COMPLETED:
                raise RuntimeError(
                    "兽渊未结批次已离开#658，但终态未证明完整完成；保留标记"
                )
            terminal_scene = int(scene_id)
        else:
            raise RuntimeError(
                "兽渊未结批次既不在#658也无已验证终态；保留标记并拒绝重放"
            )
        marker = _confirm_auto_batch_terminal(
            str(activity.id),
            marker,
            terminal_scene=int(terminal_scene or 0),
            duration_seconds=observed_duration_seconds,
            duration_reliable=observed_duration_reliable,
            state_key=state_key,
        )
    sealed_after = marker.get("after")
    if sealed_after is None:
        _wait_scene_match = yield from context.wait_scene(assets.terminal_scene_ids, wait=5.0, required=False)
        (current_scene, _score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if current_scene in assets.terminal_scene_ids:
            yield from _close_auto_terminal(context, int(current_scene))
        rank_snapshot = yield from _refresh_personal_rank_for_measurement(
            context,
            activity,
        )
        after, after_budget = _read_ledger(
            activity,
            detail,
            ranking_snapshot=rank_snapshot,
        )
        challenge_config = dict(after_budget["count_configs"][2])
        marker = _seal_auto_batch_after(
            str(activity.id),
            marker,
            after,
            challenge_item_automatic=int(challenge_config.get("automatic") or 0),
            state_key=state_key,
        )
    else:
        after = _ledger_from_dict(dict(sealed_after))
        challenge_config = {
            "automatic": int(marker.get("after_challenge_item_automatic") or 0)
        }
    measurement = measure_beast_abyss_completed_batch(
        before,
        after,
        requested_explores=request.requested_explores,
        completed_explores=request.requested_explores,
        duration_seconds=max(0.001, float(marker.get("duration_seconds") or 0.001)),
        challenge_item_automatic=int(challenge_config.get("automatic") or 0),
        duration_reliable=bool(marker.get("duration_reliable")),
    )
    measurements = _settle_auto_batch(str(activity.id), marker, measurement, state_key=state_key)
    return measurements


def run_beast_abyss_measurement_batch(
    context: Any,
    activity: FanxiuExchangeActivity,
    detail: Any,
    *,
    pending: Mapping[str, Any] | None = None,
) -> Iterator[Any]:
    """Run or recover one self-contained 100-explore measurement batch.

    The unit owns configuration, Runtime before/after ledgers, native-auto
    terminal handling, durable settlement, and scatter-model refresh.  It does
    not navigate to ranking pages or claim task rewards.
    """

    request = BeastAbyssNativeAutoRequest(
        auto_use_explore_items=True,
        measurement=True,
        requested_explores=BEAST_ABYSS_MEASUREMENT_EXPLORES,
    )
    return (yield from _execute_or_recover_auto_batch(
        context,
        activity,
        detail,
        request,
        pending=pending,
    ))


def _run_auto_batch(
    context: Any,
    request: BeastAbyssNativeAutoRequest,
) -> Iterator[Any]:
    assets = DEFAULT_BEAST_ABYSS_NATIVE_AUTO_ASSETS
    settings = yield from prepare_beast_abyss_native_auto(context, assets, request)
    result = yield from run_prepared_beast_abyss_native_auto(
        context,
        assets,
        request,
        settings,
    )
    if result.terminal not in {
        BeastAbyssAutoTerminal.COMPLETED,
        BeastAbyssAutoTerminal.RESOURCE_EXHAUSTED,
    }:
        raise RuntimeError(f"兽渊自动探查异常终态：{result.terminal}")
    yield from _close_auto_terminal(context, result.scene_id)
    return result


def read_beast_abyss_challenge_state(occurrence: Any) -> dict[str, Any]:
    """Read exact-occurrence recovery state before any navigation is attempted."""
    from backend.db import engine

    with Session(engine) as session:
        activities = list(session.exec(select(FanxiuExchangeActivity).where(
            FanxiuExchangeActivity.activity_type == "beast-abyss",
            FanxiuExchangeActivity.instance_key == occurrence.instance_key,
        )).all())
        if not activities:
            return {}
        activity = validate_beast_abyss_occurrence(occurrence, activities)
        data = dict(activity.instance_data or {})
        return {"initialization": dict(data.get(BEAST_ABYSS_INITIALIZATION_KEY) or {}),
                "formal": dict(data.get(BEAST_ABYSS_FORMAL_KEY) or {})}


def read_beast_abyss_initialization_state(occurrence: Any) -> dict[str, Any]:
    """Compatibility projection of the occurrence's initialization journal."""
    return read_beast_abyss_challenge_state(occurrence).get("initialization", {})


def execute_beast_abyss_initialization_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: Any,
) -> Iterator[Any]:
    activity = _load_activity(occurrence)
    initialization = dict(
        dict(activity.instance_data or {}).get(BEAST_ABYSS_INITIALIZATION_KEY) or {}
    )
    if _initialization_state_complete(
        initialization,
        expected_activity_id=str(activity.id),
    ):
        return {
            "status": "completed",
            "phase": "initialization",
            "performed_actions": False,
            "message": "兽渊本期初始化已完成",
            "initialization": initialization,
        }
    detail = _load_detail(activity.id)
    context = runner._behavior_tree_context(
        ctx,
        ctx.get("asset_tree_path"),
        stop_event=stop_event,
    )
    pending = initialization.get("pending_batch")
    if not isinstance(pending, Mapping) and not initialization.get("measurements"):
        now = datetime.now().astimezone()
        cutoff = min(occurrence.end_at.timestamp(), now.replace(hour=21, minute=0, second=0, microsecond=0).timestamp())
        if time.time() >= cutoff - 90:
            return {"status": "unavailable", "outcome": "pass", "achieved": False,
                    "message": "兽渊首次100次快速运行窗口不足，本轮pass"}
    if not isinstance(pending, Mapping):
        from backend.core.fanxiu.data_annotation.tasks.beast_abyss_native_auto import (
            dismiss_beast_abyss_defeat,
        )
        yield from dismiss_beast_abyss_defeat(context)
        yield from _enter_beast_abyss_occurrence_home(
            context,
            occurrence,
            label="兽渊10:00初始化",
            anchor=datetime.now().astimezone(),
        )
        yield from enter_beast_abyss_explore(
            context, DEFAULT_BEAST_ABYSS_NATIVE_AUTO_ASSETS
        )
    measurements = [
        _measurement_from_dict(item)
        for item in initialization.get("measurements") or ()
        if isinstance(item, Mapping)
    ]
    # One completed sample is sufficient; preserve historical points and
    # recover the existing authorization before considering another run.
    first_rewards = initialization.get("first_reward_check")
    if isinstance(pending, Mapping) or not measurements:
        try:
            measurements = yield from run_beast_abyss_measurement_batch(
                context, activity, detail,
                pending=pending if isinstance(pending, Mapping) else None)
        except BeastAbyssBatchResourceInsufficient as exc:
            yield from context.go_scene(34)
            return {"status": "unavailable", "outcome": "pass", "phase": "initialization",
                    "achieved": False, "message": str(exc)}
    # Save the sample before reward collection; a retry never buys sample #2.
    _persist_initialization_progress(
        activity.id,
        measurements,
        completed=False,
        first_reward_check=(
            first_rewards if isinstance(first_rewards, Mapping) else None
        ),
    )
    final_rewards = yield from claim_beast_abyss_task_rewards(context)
    state = _persist_initialization_progress(
        activity.id,
        measurements,
        completed=True,
        first_reward_check=(
            first_rewards if isinstance(first_rewards, Mapping) else None
        ),
        final_reward_check=final_rewards,
    )
    model = build_beast_abyss_yield_scatter_model(measurements)
    return {
        "status": "completed",
        "message": f"兽渊10:00初始化完成：{len(measurements)} 批双 y 建模",
        "phase": "initialization",
        "performed_actions": True,
        "batch_count": len(measurements),
        "scatter_points": list(model.points),
        "initialization": state,
        "first_batch_rewards": first_rewards,
        "final_rewards": final_rewards,
    }


def execute_beast_abyss_formal_checkpoint(
    runner: Any, ctx: dict[str, Any], payload: dict[str, Any], stop_event: threading.Event,
    *, occurrence: Any,
) -> Iterator[Any]:
    """First 100 → rewards → next commodity, using the durable batch journal."""
    activity = _load_activity(occurrence)
    initialization = dict(dict(activity.instance_data or {}).get(BEAST_ABYSS_INITIALIZATION_KEY) or {})
    formal = dict(dict(activity.instance_data or {}).get(BEAST_ABYSS_FORMAL_KEY) or {})
    if not _initialization_state_complete(initialization, expected_activity_id=str(activity.id)):
        if formal.get("pending_batch"):
            raise RuntimeError("兽渊正式未结批次存在但初始化证据缺失，保留现场")
        initial = yield from execute_beast_abyss_initialization_checkpoint(
            runner, ctx, payload, stop_event, occurrence=occurrence)
        if initial.get("status") != "completed":
            return initial
        activity = _load_activity(occurrence)
    initial_rows = _initialization_measurements(activity)
    formal_rows = _formal_measurements(activity)
    context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
    pending = formal.get("pending_batch")
    if not pending:
        yield from _enter_beast_abyss_occurrence_home(
            context, occurrence, label="兽渊10:05逐档挑战", anchor=datetime.now().astimezone())
        yield from enter_beast_abyss_explore(context, DEFAULT_BEAST_ABYSS_NATIVE_AUTO_ASSETS)
    results = []
    for _ in range(max(1, min(200, int(payload.get("max_formal_batches") or 80)))):
        if stop_event.is_set():
            raise InterruptedError()
        detail = _load_detail(activity.id)
        if pending:
            request = BeastAbyssNativeAutoRequest(auto_use_explore_items=True, measurement=False,
                requested_explores=int(pending["target_explores"]))
            try:
                formal_rows = yield from _execute_or_recover_auto_batch(
                    context, activity, detail, request, pending=pending, state_key=BEAST_ABYSS_FORMAL_KEY)
            except BeastAbyssBatchResourceInsufficient as exc:
                yield from context.go_scene(34)
                return {"status": "unavailable", "outcome": "pass", "achieved": False,
                        "message": str(exc)}
            pending = None
            continue
        before, budget = _read_ledger(activity, detail)
        explore_config, challenge_config = (dict(budget["count_configs"][i]) for i in (1, 2))
        plan = plan_beast_abyss_formal_batch(
            before, (formal_rows or initial_rows)[-1], _rebase_formal_exchange_plan(detail, before),
            now=datetime.now().astimezone(), activity_end_at=occurrence.end_at,
            explore_item_automatic=int(explore_config.get("automatic") or 0),
            challenge_item_automatic=int(challenge_config.get("automatic") or 0),
            hierarchy_consume=int(budget["capacity"]["max_explore_cost"]),
            available_seconds=max(0, min(occurrence.end_at.timestamp(),
                datetime.now().astimezone().replace(hour=21, minute=0, second=0, microsecond=0).timestamp())
                - time.time() - 90))
        plan_payload = _jsonable_dataclass(plan)
        if plan.requested_explores <= 0:
            if plan.status == "deferred":
                state = _persist_formal_progress(activity.id, initial_rows, formal_rows,
                    status="deferred", terminal_reason=plan.reason, last_plan=plan_payload)
                yield from context.go_scene(34)
                # This day's checkpoint is done; the occurrence is not. The
                # next day's formal checkpoint re-reads the wallet and sample.
                return {"status": "retained", "outcome": "deferred", "achieved": False,
                        "phase": "formal", "performed_actions": bool(results),
                        "message": f"兽渊最高档保留至活动最后一天（{plan.unlock_at}），今日逐档补足结束",
                        "plan": plan_payload, "formal": state, "batches": results}
            completed = plan.status == "completed"
            state = _persist_formal_progress(activity.id, initial_rows, formal_rows,
                status="completed" if completed else "unavailable", terminal_reason=plan.reason, last_plan=plan_payload)
            yield from context.go_scene(34)
            return {"status": "completed" if completed else "unavailable", "outcome": plan.status,
                    "achieved": completed, "phase": "formal", "performed_actions": bool(results),
                    "message": ("兽渊全部有限兑换档次预算已满足" if completed else
                                f"兽渊下一档{plan.target_tier}：{plan.reason}；需{plan.remaining_target_explores}次，"
                                f"可用{plan.capacity}次，缺{plan.deficit}次，本轮pass"),
                    "plan": plan_payload, "formal": state, "batches": results}
        _persist_formal_progress(activity.id, initial_rows, formal_rows,
            status="in_progress", last_plan=plan_payload)
        request = BeastAbyssNativeAutoRequest(auto_use_explore_items=True, measurement=False,
            requested_explores=plan.requested_explores, maximum_explores=plan.explore_capacity)
        try:
            formal_rows = yield from _execute_or_recover_auto_batch(
                context, activity, detail, request, pending=None, state_key=BEAST_ABYSS_FORMAL_KEY)
        except BeastAbyssBatchResourceInsufficient as exc:
            state = _persist_formal_progress(activity.id, initial_rows, formal_rows,
                status="unavailable", terminal_reason="resource_insufficient", last_plan=plan_payload)
            yield from context.go_scene(34)
            return {"status": "unavailable", "outcome": "pass", "achieved": False,
                    "message": str(exc), "formal": state, "plan": plan_payload}
        state = _persist_formal_progress(activity.id, initial_rows, formal_rows,
            status="in_progress", last_plan=plan_payload)
        results.append({"plan": plan_payload, "measurement": _jsonable_dataclass(formal_rows[-1])})
    yield from context.go_scene(34)
    return {"status": "pending", "message": "兽渊本次逐档轮数达到安全上限，保留完整批次后重规划"}


def execute_beast_abyss_auto_clear_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: Any,
) -> Iterator[Any]:
    del payload
    activity = _load_activity(occurrence)
    detail = _load_detail(activity.id)
    context = runner._behavior_tree_context(
        ctx,
        ctx.get("asset_tree_path"),
        stop_event=stop_event,
    )
    yield from _ensure_entry_surface(context)
    yield from enter_beast_abyss_explore_and_claim_rewards(
        context,
        DEFAULT_BEAST_ABYSS_NATIVE_AUTO_ASSETS,
    )
    ledger, budget = _read_ledger(activity, detail)
    attempts = int(budget["capacity"]["explore_attempts_without_items"])
    if attempts <= 0:
        return {
            "status": "completed",
            "message": "兽渊20:45无自动恢复的体力需要清理",
            "phase": "auto_clear",
            "performed_actions": False,
        }
    result = yield from _run_auto_batch(
        context,
        BeastAbyssNativeAutoRequest(
            auto_use_explore_items=False,
            measurement=False,
            requested_explores=attempts,
        ),
    )
    rewards = yield from claim_beast_abyss_task_rewards(context)
    return {
        "status": "completed",
        "message": f"兽渊20:45清理自动恢复的体力 {attempts} 次",
        "phase": "auto_clear",
        "performed_actions": True,
        "terminal": str(result.terminal),
        "before": asdict(ledger),
        "task_rewards": rewards,
    }


def execute_beast_abyss_manual_clear_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: Any,
) -> Iterator[Any]:
    del payload
    _load_activity(occurrence)
    context = runner._behavior_tree_context(
        ctx,
        ctx.get("asset_tree_path"),
        stop_event=stop_event,
    )
    yield from _ensure_entry_surface(context)
    yield from enter_beast_abyss_explore_and_claim_rewards(
        context,
        DEFAULT_BEAST_ABYSS_NATIVE_AUTO_ASSETS,
    )
    return {
        "status": "blocked",
        "phase": "manual_clear",
        "message": "兽渊21:30后手动挑战动作尚缺本期可复验的战斗终态契约",
    }


def store_beast_abyss_final_rankings(activity_id: str) -> dict[str, Any]:
    """Persist freshly loaded personal and team ranking facts for one occurrence."""

    from backend.core.fanxiu.activity.beast_abyss import (
        collect_and_store_beast_abyss_activity,
    )
    from backend.db import engine
    from backend.models import FanxiuExchangeActivity, FanxiuExchangeRanking

    with Session(engine) as session:
        detail = collect_and_store_beast_abyss_activity(
            session,
            activity_id=activity_id,
            collect_runtime_shop=False,
            collect_runtime_rank=True,
            collect_related_runtime_ranks=True,
        )
        session.flush()
        rows = session.exec(
            select(FanxiuExchangeRanking).where(
                FanxiuExchangeRanking.activity_id == activity_id
            )
        ).all()
        counts = {
            scope: sum(row.ranking_scope == scope for row in rows)
            for scope in ("personal", "team")
        }
        activity = session.get(FanxiuExchangeActivity, activity_id)
        if activity is None:
            raise RuntimeError("兽渊收尾：最终榜单写入后无法回读活动实例")
        evidence = dict(activity.evidence or {})
        current_related = {
            str(value)
            for value in evidence.get("current_related_ranking_scopes") or []
        }
        refreshed_scopes = {
            str(value)
            for value in dict(evidence.get("refresh_status") or {}).get(
                "ranking_scopes"
            ) or []
        }
        if counts["personal"] <= 0 or "personal" not in refreshed_scopes:
            raise RuntimeError("兽渊收尾：最终个人榜没有成功更新")
        if (
            counts["team"] <= 0
            or "team" not in current_related
            or "team" not in refreshed_scopes
        ):
            raise RuntimeError("兽渊收尾：最终团队榜没有成功更新为本期事实")
        session.commit()
        return {
            "activity_id": activity_id,
            "personal_count": counts["personal"],
            "team_count": counts["team"],
            "captured_at": str(detail.captured_at or ""),
        }


def _refresh_beast_abyss_rank_tabs(
    context: Any,
    *,
    personal_rank_activity_id: int,
    team_rank_activity_id: int,
) -> Iterator[Any]:
    """Load both #537 tabs and read complete snapshots after each tab click."""

    from backend.core.fanxiu.instrumentation.activity_rank_runtime import (
        prepare_activity_rank_runtime,
        read_activity_rank_runtime_snapshot,
    )

    refreshed: dict[str, dict[str, Any]] = {}
    for scope, shape, rank_id in (
        ("personal", "个人", int(personal_rank_activity_id)),
        ("team", "团队", int(team_rank_activity_id)),
    ):
        before = read_activity_rank_runtime_snapshot(rank_id)
        before_object_identity = (
            str(before.get("runtime_object_identity") or "")
            if before.get("ok") and before.get("complete")
            else ""
        )
        context.click_shape_center(537, shape)
        snapshot = yield from _read_settled_activity_rank_snapshot(
            context,
            rank_id,
            require_personal_score=False,
            label=f"兽渊收尾：{shape}榜",
        )
        refreshed[scope] = {
            "rank_activity_id": rank_id,
            "before_runtime_object_identity": before_object_identity,
            "runtime_object_identity": str(
                snapshot.get("runtime_object_identity") or ""
            ),
            "runtime_object_reused": bool(
                before_object_identity
                and before_object_identity
                == str(snapshot.get("runtime_object_identity") or "")
            ),
            "self_ranking": dict(snapshot.get("self_ranking") or {}),
            "rank_list_size": int(snapshot.get("rank_list_size") or 0),
            "captured_at": str(snapshot.get("captured_at") or ""),
        }
    both_rank_ids = [
        int(personal_rank_activity_id),
        int(team_rank_activity_id),
    ]
    loaded = prepare_activity_rank_runtime(both_rank_ids)
    loaded_ids = {
        int(value) for value in loaded.get("loaded_activity_ids") or []
    }
    if not loaded.get("ok") or not set(both_rank_ids).issubset(loaded_ids):
        raise RuntimeError(
            str(loaded.get("reason") or "兽渊收尾：两个榜单无法同时保留到写入阶段")
        )
    for scope, rank_id in (
        ("personal", int(personal_rank_activity_id)),
        ("team", int(team_rank_activity_id)),
    ):
        retained = read_activity_rank_runtime_snapshot(rank_id)
        if (
            not retained.get("ok")
            or not retained.get("complete")
            or int(retained.get("rank_activity_id") or 0) != rank_id
        ):
            raise RuntimeError(f"兽渊收尾：{scope}榜在联合写入前已经失效")
        refreshed[scope]["retained_for_persistence"] = True
    return refreshed


def refresh_beast_abyss_final_rankings(
    context: Any,
    *,
    activity_id: str,
    personal_rank_activity_id: int,
    team_rank_activity_id: int,
) -> Iterator[Any]:
    """Load both #537 tabs before persisting the final occurrence snapshot."""

    yield from _refresh_beast_abyss_rank_tabs(
        context,
        personal_rank_activity_id=personal_rank_activity_id,
        team_rank_activity_id=team_rank_activity_id,
    )
    return store_beast_abyss_final_rankings(activity_id)


def execute_beast_abyss_rank_refresh_probe(
    runner: Any,
    ctx: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: Any,
) -> Iterator[Any]:
    """Refresh this occurrence's two rank tabs without challenge or persistence."""

    activity = _load_activity(occurrence)
    rank_identities = dict(
        dict(activity.evidence or {}).get("rank_scope_identities") or {}
    )
    personal_rank_activity_id = int(
        dict(rank_identities.get("personal") or {}).get("runtime_rank_activity_id")
        or 0
    )
    team_rank_activity_id = int(
        dict(rank_identities.get("team") or {}).get("runtime_rank_activity_id")
        or 0
    )
    if personal_rank_activity_id <= 0 or team_rank_activity_id <= 0:
        raise RuntimeError("兽渊榜单刷新探针：本期实例缺少个人榜或团队榜 Runtime 身份")

    context = runner._behavior_tree_context(
        ctx,
        ctx.get("asset_tree_path"),
        stop_event=stop_event,
    )
    try:
        home_scene_id = yield from _enter_beast_abyss_occurrence_home(
            context,
            occurrence,
            label="兽渊榜单刷新探针",
        )
        yield from context.wait_click_then_scene(
            home_scene_id,
            "兽渊榜",
            537,
            timeout=20.0,
            label="兽渊榜单刷新探针：进入榜单",
        )
        refreshed = yield from _refresh_beast_abyss_rank_tabs(
            context,
            personal_rank_activity_id=personal_rank_activity_id,
            team_rank_activity_id=team_rank_activity_id,
        )
    finally:
        try:
            yield from context.go_scene(34)
        except Exception as cleanup_error:
            runner._log(
                "warning",
                f"兽渊榜单刷新探针：返回世界失败：{cleanup_error}",
            )
    return {
        "status": "completed",
        "phase": "rank_refresh_probe",
        "activity_id": str(activity.id),
        "rankings": refreshed,
    }


def _enter_beast_abyss_occurrence_home(
    context: Any,
    occurrence: Any,
    *,
    label: str,
    anchor: datetime | None = None,
) -> Iterator[Any]:
    """Enter the exact persisted occurrence instead of today's default card."""

    from backend.core.fanxiu.data_annotation.schedule_navigation import (
        select_schedule_activity,
    )

    current_moment = datetime.now().astimezone()

    end_at = getattr(occurrence, "end_at", None)
    close_at = getattr(occurrence, "close_at", None)
    settlement = bool(
        end_at is not None
        and close_at is not None
        and end_at < current_moment < close_at
    )
    expected_home_scene_id = 696 if settlement else 535

    # A stopped purchase leaves the shared detail dialog open. Recognize it
    # explicitly so reentry uses its close/navigation Shapes, not the world
    # shortcut intended for an unrecognized world skin.
    _wait_scene_match = yield from context.wait_scene(scenes=[34, 535, 696, 536, 566, 66, 657, 658], wait=5.0, required=False)
    (scene_id, _score, frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if scene_id == expected_home_scene_id:
        return expected_home_scene_id
    if scene_id != 66:
        if scene_id is None:
            # World skins and transient world overlays can hide #34's OCR
            # identity while its global schedule entry remains fixed.  The
            # click is safe only as a narrowly scoped recovery and #66 is
            # still required immediately afterwards.
            context.click_shape(34, "日程", frame_data_url=frame)
            yield from context.wait_action_settle(1.5)
            yield from context.wait_scene(
                [66],
                wait=30.0,
                label=f"{label}：等待日程页",
            )
        else:
            yield from context.go_scene(34)
            yield from context.go_scene(66)
    target_anchor = anchor or occurrence.end_at.replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    day_offset = (target_anchor.date() - current_moment.date()).days
    selected = yield from select_schedule_activity(
        context,
        r"兽渊探秘",
        day_offset=day_offset,
        enter=True,
        require_runtime_alignment=True,
        expected_activity_id=int(occurrence.activity_id),
        expected_runtime_id=str(occurrence.runtime_id),
        expected_cross_count=int(occurrence.cross_count),
        now=current_moment,
    )
    if not str(getattr(selected, "runtime_key", "") or ""):
        raise RuntimeError(f"{label}：#66 未回读精确 Runtime 实例标识")
    yield from context.wait_scene(
        [expected_home_scene_id],
        wait=30.0,
        label=(
            f"{label}：等待兽渊结算主页"
            if settlement
            else f"{label}：等待兽渊活动主页"
        ),
    )
    return expected_home_scene_id


def execute_beast_abyss_daily_reconcile_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: Any,
    captured_at: datetime,
    required_fact_watermark: datetime,
) -> Iterator[Any]:
    """Maintain the exact occurrence's shop and static reward tiers at 00:30.

    Shop collection is independent of rank-page loading. A previously saved
    complete shop covering this checkpoint is reusable; otherwise collect it
    before recording the shared reconciliation observation. Never challenge,
    exchange, or overwrite initialization state in this maintenance phase.
    """
    from backend.core.fanxiu.activity.beast_abyss import collect_and_store_beast_abyss_shop
    from backend.core.fanxiu.activity.exchange_event import list_exchange_activity_snapshot
    from backend.core.fanxiu.activity.ranking_reconcile import (
        reconcile_ranking_occurrence,
        seed_ranking_occurrence,
    )
    from backend.db import engine

    watermark = required_fact_watermark
    if watermark.tzinfo is None:
        watermark = watermark.astimezone()
    with Session(engine) as session:
        activity = seed_ranking_occurrence(
            session, occurrence, captured_at=captured_at.isoformat(timespec="seconds"),
        )
        activity_id = str(activity.id)
        detail = list_exchange_activity_snapshot(
            session, activity_type="beast-abyss", activity_id=activity_id,
        ).selected_activity
        snapshot_time = None
        try:
            snapshot_time = datetime.fromisoformat(str(detail.shop_snapshot_captured_at or ""))
            if snapshot_time.tzinfo is None:
                snapshot_time = snapshot_time.astimezone()
        except ValueError:
            pass
        shop_ready = bool(detail.shop_items and snapshot_time and snapshot_time >= watermark)
        session.commit()

    context = None
    if not shop_ready:
        context = runner._behavior_tree_context(
            ctx, ctx.get("asset_tree_path"), stop_event=stop_event,
        )
        home_scene_id = yield from _enter_beast_abyss_occurrence_home(
            context, occurrence, label="兽渊00:30实例化",
        )
        match = yield from context.wait_click_then_scene(
            home_scene_id, "兑换宝阁", 536, timeout=20.0,
            label="兽渊00:30：进入兑换宝阁",
        )
        if match.id != 536:
            raise RuntimeError("兽渊00:30：未到达兑换宝阁，保留现场")
        with Session(engine) as session:
            detail = collect_and_store_beast_abyss_shop(session, activity_id=activity_id)
            if str(detail.id) != activity_id or not detail.shop_items:
                raise RuntimeError("兽渊00:30：宝阁未保存完整目标实例数据")
            session.commit()
    with Session(engine) as session:
        result = reconcile_ranking_occurrence(
            session, occurrence,
            captured_at=captured_at.isoformat(timespec="seconds"),
            required_fact_watermark=watermark,
            collect_live_facts=False,
        )
        session.commit()
    if str(result.get("activity_id") or "") != activity_id:
        raise RuntimeError("兽渊00:30：采集结果切换到其他活动实例")
    if context is not None:
        yield from context.go_scene(34)
    return result


def execute_beast_abyss_exchange_tail_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: Any,
) -> Iterator[Any]:
    from backend.core.fanxiu.data_annotation.tasks.beast_abyss_exchange import (
        execute_beast_abyss_exchange,
    )

    activity = _load_activity(occurrence)
    rank_identities = dict(
        dict(activity.evidence or {}).get("rank_scope_identities") or {}
    )
    personal_rank_activity_id = int(
        dict(rank_identities.get("personal") or {}).get("runtime_rank_activity_id")
        or 0
    )
    team_rank_activity_id = int(
        dict(rank_identities.get("team") or {}).get("runtime_rank_activity_id")
        or 0
    )
    if personal_rank_activity_id <= 0 or team_rank_activity_id <= 0:
        raise RuntimeError("兽渊尾日：旧实例缺少个人榜或团队榜 Runtime 身份")
    context = runner._behavior_tree_context(
        ctx,
        ctx.get("asset_tree_path"),
        stop_event=stop_event,
    )
    home_scene_id = yield from _enter_beast_abyss_occurrence_home(
        context,
        occurrence,
        label="兽渊尾日",
    )
    yield from context.wait_click_then_scene(
        home_scene_id,
        "兑换宝阁",
        536,
        timeout=20.0,
        label="兽渊尾日：进入兑换宝阁",
    )
    result = yield from execute_beast_abyss_exchange(
        runner,
        ctx,
        activity_id=str(activity.id),
        stop_event=stop_event,
    )
    yield from context.wait_click_then_scene(
        536,
        "兽渊榜",
        537,
        timeout=20.0,
        label="兽渊尾日：进入最终榜单",
    )
    final_rankings = yield from refresh_beast_abyss_final_rankings(
        context,
        activity_id=str(activity.id),
        personal_rank_activity_id=personal_rank_activity_id,
        team_rank_activity_id=team_rank_activity_id,
    )
    context.click_shape_center(537, "返回")
    yield from context.wait_scene(
        [34],
        wait=20.0,
        label="兽渊尾日：榜单同步后返回世界",
    )
    return {
        **dict(result),
        "status": "completed",
        "phase": "exchange_tail",
        "final_rankings": final_rankings,
    }


__all__ = [
    "BEAST_ABYSS_FORMAL_KEY",
    "BEAST_ABYSS_INITIALIZATION_KEY",
    "execute_beast_abyss_auto_clear_checkpoint",
    "execute_beast_abyss_daily_reconcile_checkpoint",
    "execute_beast_abyss_exchange_tail_checkpoint",
    "execute_beast_abyss_formal_checkpoint",
    "execute_beast_abyss_initialization_checkpoint",
    "read_beast_abyss_initialization_state",
    "read_beast_abyss_challenge_state",
    "execute_beast_abyss_manual_clear_checkpoint",
    "execute_beast_abyss_rank_refresh_probe",
    "refresh_beast_abyss_final_rankings",
    "store_beast_abyss_final_rankings",
    "validate_beast_abyss_occurrence",
]
