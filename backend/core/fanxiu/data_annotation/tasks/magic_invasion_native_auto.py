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
import re
from typing import Any, Iterator

from backend.core.fanxiu.activity.magic_invasion import (
    resolve_magic_invasion_shop_identity,
)
from backend.core.fanxiu.runtime_gui.integer_count_control import (
    IntegerSliderAssets,
    set_verified_integer_slider_count,
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
MAGIC_INVASION_AUTO_COMPLETED_SCENE_ID = 873

MAGIC_INVASION_AUTO_COUNT_ASSETS = IntegerSliderAssets(
    settings_scene_id=MAGIC_INVASION_AUTO_SETTINGS_SCENE_ID,
    count_slider_thumb="自动除魔次数_滑块",
    count_slider_left_anchor="自动除魔次数_减少",
    count_slider_right_anchor="自动除魔次数_增加",
    count_slider_left_center_offset=35.0,
    count_slider_right_center_offset=-9.0,
    count_slider_track="自动除魔次数_滑轨",
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
        allow_discovery=True,
        missing_as_zero=False,
    )


def ensure_magic_invasion_map(context: Any) -> Iterator[Any]:
    """Reach #512 through the same #66 entry the exploration flow uses."""

    match = yield from context.wait_scene(
        [MAGIC_INVASION_MAP_SCENE_ID, MAGIC_INVASION_AUTO_SETTINGS_SCENE_ID],
        wait=5.0, required=False,
    )
    scene = match.scene_id if match is not None else None
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

    scene = yield from ensure_magic_invasion_map(context)
    if int(scene or 0) != MAGIC_INVASION_AUTO_SETTINGS_SCENE_ID:
        yield from context.wait_click(MAGIC_INVASION_MAP_SCENE_ID, MAGIC_INVASION_OPEN_AUTO_SHAPE)
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
    from backend.core.fanxiu.instrumentation.magic_invasion_auto_settings import (
        read_magic_invasion_auto_settings_snapshot,
    )
    # Runtime facts remain readable under an activity popup. Re-enter the
    # visible-scene guard after that read, and retry only the reversible
    # count reconciliation from fresh facts when an interruption races it.
    for attempt in range(3):
        match = yield from context.wait_scene([698], wait=10.0)
        if match.scene_id != 698:
            raise RuntimeError("魔道次数设置未回到配置页")
        snapshot = read_magic_invasion_auto_settings_snapshot()
        if not snapshot.get("complete"):
            raise RuntimeError("魔道自动除魔次数设置缺少完整面板快照")
        match = yield from context.wait_scene([698], wait=10.0)
        if match.scene_id != 698:
            raise RuntimeError("魔道次数设置被其他页面遮挡")
        try:
            count_result = yield from set_verified_integer_slider_count(
                context, MAGIC_INVASION_AUTO_COUNT_ASSETS, int(count),
                count_label="魔道自动除魔次数", max_adjustments=10,
                maximum=int(snapshot["times"]["maximum"]),
                initial_count=int(snapshot["times"]["selected"]),
            )
            break
        except RuntimeError:
            if attempt == 2:
                raise
            yield from context.wait_action_settle(1.0)
    if int(count_result.get("after") or 0) != int(count):
        raise RuntimeError(
            f"魔道自动除魔次数回读异常：expected={int(count)}, "
            f"actual={count_result.get('after')!r}"
        )
    return {"configured": configured, "count": count_result}


def wait_magic_invasion_auto_completion(
    context: Any, *, count: int, terminal_polls: int = 600,
    poll_seconds: float = 3.0,
) -> Iterator[Any]:
    """Observe completion before dismissing it; the game resets hadAutoTimes.

    Runtime zero is not a completion proof. The dedicated result Shape must
    report the exact requested count, including on pending-batch recovery.
    Every poll re-enters the scene pipeline so known interruptions are closed.
    """
    last = {}
    for _ in range(max(1, terminal_polls)):
        last = read_magic_invasion_counters()
        match = yield from context.wait_scene(
            [MAGIC_INVASION_AUTO_COMPLETED_SCENE_ID, 699, 512],
            wait=3.0, required=False,
        )
        if match is not None and match.scene_id == MAGIC_INVASION_AUTO_COMPLETED_SCENE_ID:
            for attempt in range(5):
                text = context.ocr_text_in_shapes(
                    MAGIC_INVASION_AUTO_COMPLETED_SCENE_ID, ["完成次数"],
                    crop=True, padding=10,
                )
                parsed = re.search(r"完成除魔次数[：:]?\s*(\d+)\s*次", text)
                if parsed:
                    completed = int(parsed.group(1))
                    last = read_magic_invasion_counters()
                    if completed != count or last.get("is_in_auto"):
                        raise RuntimeError(f"魔道终态与请求不一致：{completed}/{count}, {last}")
                    return {"terminal": "completed", "completed_exorcisms": completed,
                            "observed_at_epoch": time.time(), "counters": last,
                            "result_text": text}
                yield from context.wait_action_settle(1.0)
            raise RuntimeError("魔道完成次数连续5帧未能读出，保留弹窗")
        yield from context.wait_action_settle(poll_seconds)
    raise RuntimeError(f"魔道自动除魔未到达可证明终态：{last}")


def start_magic_invasion_auto(
    context: Any, *, count: int, terminal_polls: int = 600,
    poll_seconds: float = 3.0,
) -> Iterator[Any]:
    yield from context.wait_click(698, MAGIC_INVASION_START_AUTO_SHAPE)
    return (yield from wait_magic_invasion_auto_completion(
        context, count=count, terminal_polls=terminal_polls,
        poll_seconds=poll_seconds,
    ))


def run_magic_invasion_auto_batch(
    context: Any, occurrence: MagicInvasionOccurrence, *,
    count: int = MAGIC_INVASION_AUTO_BATCH_SIZE, terminal_polls: int = 600,
    poll_seconds: float = 3.0, max_quality_scrolls: int = 12,
    phase: str = "initialization",
) -> Iterator[Any]:
    """Run/recover one durable initialization batch, settle, then close result."""
    if phase == "formal":
        from .magic_invasion_reward_ledger import (
            load_magic_invasion_reward_state as load_state,
            arm_magic_invasion_reward_batch as arm_batch,
            settle_magic_invasion_reward_batch as settle_batch,
        )
        arm_options = {"count": count}
    elif phase == "initialization":
        if count != MAGIC_INVASION_AUTO_BATCH_SIZE:
            raise ValueError("魔道首次挑战必须为100次")
        load_state = load_magic_invasion_auto_timing_state
        arm_batch = arm_magic_invasion_auto_timing_batch
        settle_batch = settle_magic_invasion_auto_timing_batch
        arm_options = {}
    else:
        raise ValueError(f"未知魔道批次阶段：{phase}")
    state = load_state(occurrence)
    pending = state.get("pending_batch")
    if pending is None and state.get("measurements"):
        # A crash after durable settlement but before confirmation must only
        # finish that confirmation, never repeat the recorded batch.
        match = yield from context.wait_scene([873, 512, 698, 34], wait=3.0, required=False)
        if match is not None and match.scene_id == 873:
            previous_count = int(state["measurements"][-1]["completed_exorcisms"])
            yield from wait_magic_invasion_auto_completion(context, count=previous_count, terminal_polls=1)
            yield from context.wait_click(873, "确定")
            yield from context.wait_scene([512], wait=15.0)
    if phase == "initialization" and pending is None and (state.get("measurements") or state["status"] == "skipped_this_occurrence"):
        return {"status": "already_terminal", "state": state}
    batch = None
    if pending is None:
        if read_magic_invasion_counters().get("is_in_auto"):
            raise RuntimeError("检测到非本批次启动的自动除魔，保留游戏运行，不接管")
        batch = yield from configure_magic_invasion_auto_for_batch(
            context, count=count, max_quality_scrolls=max_quality_scrolls,
        )
        wallet = _wallet_snapshot(occurrence)
        counters = read_magic_invasion_counters()
        if counters.get("ranking_score") is None or counters.get("is_in_auto"):
            raise RuntimeError("魔道批次启动前积分不可用或自动除魔仍在运行")
        baseline = {"magic_crystal": int(wallet["exchange_currency"]),
                    "ranking_score": int(counters["ranking_score"]),
                    "pid": counters["pid"],
                    "process_start_ticks": counters["process_start_ticks"]}
        armed = arm_batch(occurrence, baseline=baseline, **arm_options)
        if armed.get("recovered_pending"):
            raise RuntimeError("魔道批次授权冲突，拒绝重复启动")
        pending = armed["pending_batch"]
        terminal = yield from start_magic_invasion_auto(
            context, count=count, terminal_polls=terminal_polls, poll_seconds=poll_seconds,
        )
    else:
        if int(pending["requested_exorcisms"]) != count or not pending.get("baseline"):
            raise RuntimeError("魔道待结算批次缺少一致的请求/基线，保留现场")
        terminal = yield from wait_magic_invasion_auto_completion(
            context, count=count, terminal_polls=terminal_polls, poll_seconds=poll_seconds,
        )
    baseline = pending["baseline"]
    after = _wallet_snapshot(occurrence)
    counters = terminal["counters"]
    if any(counters[key] != baseline[key] for key in ("pid", "process_start_ticks")):
        raise RuntimeError("魔道批次跨越游戏进程，拒绝混用基线")
    crystal_delta = int(after["exchange_currency"]) - baseline["magic_crystal"]
    score_delta = int(counters["ranking_score"]) - baseline["ranking_score"]
    state = settle_batch(
        occurrence, batch_id=pending["batch_id"], completed_exorcisms=count,
        magic_crystal_delta=crystal_delta, ranking_score_delta=score_delta,
        duration_seconds=terminal["observed_at_epoch"] - pending["armed_at_epoch"],
    )
    yield from context.wait_click(MAGIC_INVASION_AUTO_COMPLETED_SCENE_ID, "确定")
    match = yield from context.wait_scene([512], wait=15.0)
    if match is None or match.scene_id != 512:
        raise RuntimeError("魔道批次已结算，但结果弹窗尚未退出到地图")
    return {"status": "settled", "batch": batch, "terminal": terminal,
            "crystal_after": int(after["exchange_currency"]),
            "crystal_delta": crystal_delta, "ranking_score_delta": score_delta,
            "state": state}


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
