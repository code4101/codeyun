from __future__ import annotations

"""Fail-closed GUI driver for Magic Invasion native auto-exorcism.

The Runtime readers, the option configurator and the durable timing ledger
already existed but nothing orchestrated them.  This module owns only the
missing orchestration:

    #512 自动除魔 -> #698 configure -> set count -> 开启自动
    -> await the MagicinvadeMgr terminal -> settle the timing ledger.

It never guesses a scene id or coordinate: the map entry, the settings scene
and the start control all come from formally annotated assets.
"""

from collections.abc import Mapping
import time
from typing import Any, Iterator

from backend.core.fanxiu.activity.magic_invasion import (
    resolve_magic_invasion_shop_identity,
)
from backend.core.fanxiu.runtime_gui.integer_count_control import (
    IntegerButtonAssets,
    set_verified_integer_button_count,
)
from backend.core.fanxiu.data_annotation.tasks.magic_invasion import (
    MAGIC_INVASION_MAP_SCENE_ID,
    MagicInvasionOccurrence,
    _wait_scene,
)
from backend.core.fanxiu.data_annotation.tasks.magic_invasion_auto_configurator import (
    MAGIC_INVASION_AUTO_SETTINGS_SCENE_ID,
    MAGIC_INVASION_START_AUTO_SHAPE,
    configure_magic_invasion_auto_options,
)
from backend.core.fanxiu.data_annotation.tasks.magic_invasion_native_auto_timing import (
    arm_magic_invasion_auto_timing_batch,
    load_magic_invasion_auto_timing_state,
    settle_magic_invasion_auto_timing_batch,
)
from backend.core.fanxiu.instrumentation.magic_invasion_auto_running import (
    read_magic_invasion_counters,
)
from backend.core.fanxiu.instrumentation.wallet import read_wallet_currency_snapshot


MAGIC_INVASION_OPEN_AUTO_SHAPE = "自动除魔"
MAGIC_INVASION_AUTO_BATCH_SIZE = 100

MAGIC_INVASION_AUTO_COUNT_ASSETS = IntegerButtonAssets(
    settings_scene_id=MAGIC_INVASION_AUTO_SETTINGS_SCENE_ID,
    count_region="自动除魔次数",
    count_decrease="自动除魔次数_减少",
    count_increase="自动除魔次数_增加",
)


def _wallet_snapshot(occurrence: MagicInvasionOccurrence) -> dict[str, Any]:
    _shop_id, currency_type, _cross_count = resolve_magic_invasion_shop_identity(
        cross_count=occurrence.server_count
    )
    return read_wallet_currency_snapshot(
        int(currency_type),
        allow_discovery=False,
        missing_as_zero=True,
    )


def ensure_magic_invasion_map(context: Any) -> Iterator[Any]:
    """Reach #512 through the same #66 entry the exploration flow uses."""

    scene, _score, _frame = yield from context.current_scene()
    if int(scene or 0) in (
        MAGIC_INVASION_MAP_SCENE_ID,
        MAGIC_INVASION_AUTO_SETTINGS_SCENE_ID,
    ):
        return int(scene)

    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )
    from backend.core.fanxiu.data_annotation.effective_time import job_now
    from backend.core.fanxiu.data_annotation.schedule_navigation import (
        select_schedule_activity,
    )
    from backend.core.fanxiu.data_annotation.tasks.magic_invasion import (
        enter_magic_invasion_challenge_page,
        wait_magic_invasion_cover_after_schedule_entry,
    )

    schedule = read_fanxiu_activity_runtime_schedule(
        allow_discovery=True,
        force_refresh=True,
    )
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError("魔道自动除魔：Runtime 日程不可用或不完整")
    yield from context.go_scene(66)
    yield from select_schedule_activity(
        context,
        r"魔道入侵",
        enter=True,
        runtime_schedule=schedule,
        require_runtime_alignment=True,
        now=job_now(),
    )
    yield from wait_magic_invasion_cover_after_schedule_entry(
        context,
        [509],
        wait_seconds=30.0,
        label="魔道自动除魔：等待活动主页",
    )
    yield from enter_magic_invasion_challenge_page(context)
    landed, _score, _frame = yield from _wait_scene(
        context,
        (MAGIC_INVASION_MAP_SCENE_ID,),
        timeout_seconds=20.0,
    )
    return int(landed)


def open_magic_invasion_auto_settings(context: Any) -> Iterator[Any]:
    """Reach #698 from the map through the annotated 自动除魔 entry."""

    yield from ensure_magic_invasion_map(context)
    scene, _score, _frame = yield from context.current_scene()
    if int(scene or 0) != MAGIC_INVASION_AUTO_SETTINGS_SCENE_ID:
        context.click_shape_center(MAGIC_INVASION_MAP_SCENE_ID, MAGIC_INVASION_OPEN_AUTO_SHAPE)
        landed, _score, _frame = yield from _wait_scene(
            context,
            (MAGIC_INVASION_AUTO_SETTINGS_SCENE_ID,),
            timeout_seconds=20.0,
        )
        scene = landed
    return int(scene)


def configure_magic_invasion_auto_for_batch(
    context: Any,
    *,
    count: int,
    max_quality_scrolls: int = 12,
) -> Iterator[Any]:
    """Configure #698 and set the exact count without starting the loop."""

    yield from open_magic_invasion_auto_settings(context)
    configured = yield from configure_magic_invasion_auto_options(
        context,
        scene_id=MAGIC_INVASION_AUTO_SETTINGS_SCENE_ID,
        max_quality_scrolls=int(max_quality_scrolls),
    )
    count_result = yield from set_verified_integer_button_count(
        context,
        MAGIC_INVASION_AUTO_COUNT_ASSETS,
        int(count),
        count_label="魔道自动除魔次数",
    )
    if int(count_result.get("after") or 0) != int(count):
        raise RuntimeError(
            f"魔道自动除魔次数回读异常：expected={int(count)}, "
            f"actual={count_result.get('after')!r}"
        )
    return {"configured": configured, "count": count_result}


def start_magic_invasion_auto(
    context: Any,
    *,
    count: int,
    terminal_polls: int = 600,
    poll_seconds: float = 3.0,
) -> Iterator[Any]:
    """Click 开启自动 and await the MagicinvadeMgr terminal for one batch."""

    before = read_magic_invasion_counters()
    had_before = int(before.get("had_auto_times") or 0)
    target_had = had_before + int(count)
    context.click_shape_center(
        MAGIC_INVASION_AUTO_SETTINGS_SCENE_ID,
        MAGIC_INVASION_START_AUTO_SHAPE,
    )
    yield from context.wait_action_settle(2.0)
    last: Mapping[str, Any] = {}
    for _poll in range(max(1, int(terminal_polls))):
        yield from context.wait_action_settle(poll_seconds)
        current = read_magic_invasion_counters()
        last = current
        had_now = int(current.get("had_auto_times") or 0)
        if had_now >= target_had and not bool(current.get("is_in_auto")):
            return {
                "terminal": "completed",
                "target_had": target_had,
                "had_before": had_before,
                "counters": dict(current),
            }
    raise RuntimeError(
        f"魔道自动除魔未在轮询上限内到达终态：target_had={target_had}, last={dict(last)}"
    )


def run_magic_invasion_auto_batch(
    context: Any,
    occurrence: MagicInvasionOccurrence,
    *,
    count: int = MAGIC_INVASION_AUTO_BATCH_SIZE,
    terminal_polls: int = 600,
    poll_seconds: float = 3.0,
    max_quality_scrolls: int = 12,
) -> Iterator[Any]:
    """Run exactly one ledger-armed auto-exorcism batch and settle it.

    Navigation/configuration happen BEFORE the ledger is armed so a failed
    entry never leaves a pending marker; only the irreversible 开启自动 click
    is bracketed by ``arm`` ... ``settle``.
    """

    state = load_magic_invasion_auto_timing_state(occurrence)
    if str(state.get("status") or "") in {
        "stable",
        "max_batches_reached",
        "skipped_this_occurrence",
    }:
        return {"status": "already_terminal", "state": state}

    crystal_before = _wallet_snapshot(occurrence)
    started_at = time.time()
    batch = yield from configure_magic_invasion_auto_for_batch(
        context,
        count=int(count),
        max_quality_scrolls=int(max_quality_scrolls),
    )
    armed = arm_magic_invasion_auto_timing_batch(occurrence)
    if bool(armed.get("recovered_pending")):
        raise RuntimeError(
            "魔道自动除魔存在未闭合 pending 批次，需先恢复，拒绝重放"
        )
    pending = armed.get("pending_batch")
    if not isinstance(pending, Mapping) or not str(pending.get("batch_id") or ""):
        raise RuntimeError("魔道自动除魔账本未形成 pending 批次")
    if int(pending.get("requested_exorcisms") or 0) != int(count):
        raise RuntimeError("魔道自动除魔账本批次大小与本批请求不一致")

    terminal = yield from start_magic_invasion_auto(
        context,
        count=int(count),
        terminal_polls=int(terminal_polls),
        poll_seconds=float(poll_seconds),
    )
    duration = max(0.001, time.time() - started_at)
    crystal_after = _wallet_snapshot(occurrence)
    crystal_delta = int(crystal_after.get("exchange_currency") or 0) - int(
        crystal_before.get("exchange_currency") or 0
    )
    settled = settle_magic_invasion_auto_timing_batch(
        occurrence,
        batch_id=str(pending["batch_id"]),
        completed_exorcisms=int(count),
        magic_crystal_delta=max(0, crystal_delta),
        ranking_score_delta=0,
        duration_seconds=duration,
    )
    return {
        "status": "settled",
        "batch": batch,
        "terminal": terminal,
        "crystal_before": int(crystal_before.get("exchange_currency") or 0),
        "crystal_after": int(crystal_after.get("exchange_currency") or 0),
        "crystal_delta": crystal_delta,
        "duration_seconds": duration,
        "state": settled,
    }


def clear_unstarted_magic_invasion_auto_pending(
    occurrence: MagicInvasionOccurrence,
    *,
    reason: str,
) -> dict[str, Any]:
    """Clear an armed-but-never-started batch (navigation-time failure).

    The durable ledger cannot prove whether 开启自动 was clicked, so this is an
    explicit R&D tool: call it only when the start click is provably absent.
    """

    from backend.core.fanxiu.activity.magic_invasion_auto_timing import (
        measurement_from_mapping,
        timing_state_projection,
    )
    from backend.core.fanxiu.data_annotation.tasks.magic_invasion_native_auto_timing import (
        MAGIC_INVASION_AUTO_TIMING_KEY,
        _default_writer,
        load_magic_invasion_auto_timing_state,
    )

    state = load_magic_invasion_auto_timing_state(occurrence)
    if state.get("pending_batch") is None:
        return {"cleared": False, "state": state}
    updated = timing_state_projection(
        occurrence_id=occurrence.occurrence_id,
        measurements=[measurement_from_mapping(row) for row in state["measurements"]],
        pending_batch=None,
        max_batches=int(state["max_batches"]),
        settled_batch_ids=state["settled_batch_ids"],
        skip=None,
    )
    _default_writer(
        occurrence,
        {MAGIC_INVASION_AUTO_TIMING_KEY: updated},
        message=f"魔道自动除魔清除未启动批次：{reason}",
    )
    return {"cleared": True, "state": updated}


__all__ = [
    "MAGIC_INVASION_AUTO_BATCH_SIZE",
    "MAGIC_INVASION_AUTO_COUNT_ASSETS",
    "MAGIC_INVASION_OPEN_AUTO_SHAPE",
    "clear_unstarted_magic_invasion_auto_pending",
    "configure_magic_invasion_auto_for_batch",
    "ensure_magic_invasion_map",
    "open_magic_invasion_auto_settings",
    "run_magic_invasion_auto_batch",
    "start_magic_invasion_auto",
]
