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
from contextlib import contextmanager
import logging
import time
import re
import json
import hashlib
import base64
from pathlib import Path
from datetime import datetime
from typing import Any, Iterator


@contextmanager
def _batch_phase_timing(context, phase, *, occurrence, count):
    """Expose wall-time bottlenecks without promoting them to native speed.

    These Runtime log intervals include navigation and observation overhead.
    Only the existing continuously observed batch ledger estimates challenge
    speed. Diagnostic publication must not alter a game's result or replace
    the original exception after an irreversible action.
    """
    started = time.monotonic()
    outcome = "error"
    def publish(message):
        try:
            context.report_progress(message, phase="magic_batch_timing")
        except Exception:
            logging.getLogger(__name__).exception("魔道分段耗时诊断发布失败")
    label = f"魔道批次 {occurrence.occurrence_id} {count}次 {phase}"
    publish(f"{label} 开始")
    try:
        yield
        outcome = "success"
    finally:
        publish(f"{label} {outcome} 耗时{time.monotonic() - started:.3f}秒")

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


MAGIC_INVASION_OPEN_AUTO_SHAPE = "自动除魔图标"
MAGIC_INVASION_AUTO_BATCH_SIZE = 100
MAGIC_INVASION_AUTO_COMPLETED_SCENE_ID = 873


def record_magic_invasion_completion_trace(
    occurrence: MagicInvasionOccurrence, *, batch_id: str,
    frame_path: str, completed_exorcisms: int, ranking_score_delta: int,
    context: Any | None = None,
) -> dict[str, Any]:
    """Bind a visually verified original pre-close reward frame to its batch.

    Recovery records completion, never a new start. The formal driver still
    checks the live process and exact score delta before settling. Historical
    frame time provides an upper bound, not a native timing measurement.
    """
    from backend.core.temp_paths import codeyun_temp_root
    from .magic_invasion import load_magic_invasion_occurrence_evidence, store_magic_invasion_occurrence_evidence
    state = load_magic_invasion_auto_timing_state(occurrence)
    pending = state.get("pending_batch")
    if not isinstance(pending, Mapping) or pending.get("batch_id") != batch_id:
        raise RuntimeError("完成截图未绑定当前待结算批次")
    if completed_exorcisms != pending["requested_exorcisms"] or ranking_score_delta < 0:
        raise ValueError("完成截图次数或积分增量不合法")
    root = codeyun_temp_root("fanxiu_action_trace").resolve()
    path = Path(frame_path).resolve()
    if path.parent != root or not path.is_file():
        raise ValueError("完成截图必须来自原始动作留痕目录")
    matches = []
    for line in (root / "index.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("before") and Path(row["before"]).resolve() == path:
            matches.append(row)
    if len(matches) != 1:
        raise RuntimeError("完成截图缺少唯一动作索引")
    trace = matches[0]
    observed_epoch = datetime.fromisoformat(trace["time"]).timestamp()
    scene_id = int(trace.get("image_number") or 0)
    completion_action = {873: "确定", 874: "继续"}.get(scene_id)
    if (completion_action is None
        or trace.get("action", {}).get("shape_title") != completion_action
        # Empty label is an ordinary repair Cell, still bound to the unique
        # pre-close action receipt and the original armed batch below.
        or (trace.get("execution_task") != "玩法榜"
            and not (scene_id == 873 and trace.get("execution_task") == ""))
        or observed_epoch <= float(pending["armed_at_epoch"])):
        raise RuntimeError("完成截图场景、动作或批次时间不匹配")
    if scene_id == 873:
        if context is None:
            raise ValueError("完成提示截图必须通过正式场景与次数 Shape 核验")
        frame = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()
        matched, _, _ = context.recognize_scene_in_frame([873], frame_data_url=frame)
        text = context.ocr_text_in_shapes(873, ["完成次数"], frame_data_url=frame, crop=True, padding=10)
        if matched != 873 or parse_magic_invasion_completed_count(text) != completed_exorcisms:
            raise RuntimeError("原完成提示未证明本批实际次数")
    proof = {"batch_id": batch_id, "occurrence_id": occurrence.occurrence_id,
             "completed_exorcisms": completed_exorcisms,
             "ranking_score_delta": ranking_score_delta,
             "frame_path": str(path), "frame_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
             "observed_at_epoch": observed_epoch, "duration_reliable": False,
             "baseline": dict(pending["baseline"]), "terminal_scene_id": scene_id}
    existing = load_magic_invasion_occurrence_evidence(occurrence).get("magic_auto_completion_trace")
    if existing is not None and existing != proof:
        raise RuntimeError("完成截图凭证与已有凭证冲突")
    store_magic_invasion_occurrence_evidence(occurrence, {"magic_auto_completion_trace": proof},
                                           message="原始奖励动作截图证明本批实际完成，待正式结账")
    return proof

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

    for attempt in range(2):
        scene = yield from ensure_magic_invasion_map(context)
        if int(scene or 0) == MAGIC_INVASION_AUTO_SETTINGS_SCENE_ID:
            return int(scene)
        try:
            yield from context.wait_click(MAGIC_INVASION_MAP_SCENE_ID, MAGIC_INVASION_OPEN_AUTO_SHAPE)
        except RuntimeError as exc:
            if attempt or not str(exc).startswith("wait_click #512 [自动除魔图标] 超时"):
                raise
            # A timed activity promotion can cover the icon after the map
            # guard. Before any batch is armed, use the existing world
            # navigation to exit it once, then freshly re-enter the activity.
            yield from context.go_scene(34)
            continue
        landed, _score, _frame = yield from _wait_scene(
            context, (MAGIC_INVASION_AUTO_SETTINGS_SCENE_ID,), timeout_seconds=20.0,
        )
        return int(landed)


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
    from backend.core.fanxiu.runtime_gui.magic_auto_count_reader import (
        build_magic_auto_runtime_count_reader,
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
        # Bind the exact active process/panel after the visible guard. Each
        # feedback callback acquires a new, strictly newer Runtime snapshot.
        # Identity/source/ambiguity rejection is not RuntimeError and bypasses
        # both OCR downgrade and this reversible reconciliation retry.
        runtime_count_reader = build_magic_auto_runtime_count_reader(
            snapshot, snapshot_reader=read_magic_invasion_auto_settings_snapshot,
        )
        try:
            count_result = yield from set_verified_integer_slider_count(
                context, MAGIC_INVASION_AUTO_COUNT_ASSETS, int(count),
                count_label="魔道自动除魔次数", max_adjustments=10,
                maximum=int(snapshot["times"]["maximum"]),
                initial_count=int(snapshot["times"]["selected"]),
                runtime_count_reader=runtime_count_reader,
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


def parse_magic_invasion_completed_count(text: str) -> int | None:
    """Parse actual completion, never the configured target or a bare 100.

    Native fast mode #873 says 本次自动除魔完成除魔次数：100次;
    #874 says 已完成除魔次数. Both describe executed work.
    """
    parsed = re.search(r"(?:已?完成除魔次数|除魔完成除魔次数)[：:]?\s*(\d+)\s*次?", text)
    return int(parsed.group(1)) if parsed else None


def validate_magic_invasion_completion_count(completed: int, requested: int, *, is_in_auto: bool) -> None:
    """Reject partial completion and any still-running native batch."""
    if completed != requested or is_in_auto is not False:
        raise RuntimeError(f"魔道终态与请求不一致：{completed}/{requested}, is_in_auto={is_in_auto}")


def magic_invasion_completion_exit(scene_id: int) -> tuple[int, str]:
    """Only the proven completion page owns its corresponding close action."""
    actions = {873: "确定", 874: "继续"}
    if scene_id not in actions:
        raise ValueError(f"魔道完成页不支持：#{scene_id}")
    return scene_id, actions[scene_id]


def magic_invasion_auto_start_window_open(occurrence: MagicInvasionOccurrence, *, now_epoch: float) -> bool:
    """Recheck the fast-mode window after configuration, before batch arming.

    The planner reserves 90 seconds before the final gameplay hour. Slow
    reversible configuration can consume that reservation, so its earlier
    admission never licenses a late irreversible start. Pending recovery is
    independent of this gate and must still consume its existing proof.
    """
    return now_epoch < occurrence.end_time_ms / 1000 - 3600 - 90


def wait_magic_invasion_auto_completion(
    context: Any, *, count: int, terminal_polls: int = 600,
    poll_seconds: float = 3.0,
) -> Iterator[Any]:
    """Observe completion before dismissing it; the game resets hadAutoTimes.

    Runtime zero is not a completion proof. The dedicated result Shape must
    report the exact requested count, including on pending-batch recovery.
    Both the native completion notice (#873) and the reward page (#874) can
    report the actual count. Fast mode can end at #873 without ever showing
    #874. Consume the exact count before closing either page; configured
    targets and reset native counters are never completion evidence.
    """
    last = {}
    stopped_at = None
    for _ in range(max(1, terminal_polls)):
        # The running overlay already tells us to wait. Re-reading manager
        # counters on every poll adds no decision fact and can fail while the
        # client replaces combat objects. Read fresh counters only at a
        # terminal boundary; never infer completion from the running count.
        match = yield from context.wait_scene(
            [MAGIC_INVASION_AUTO_COMPLETED_SCENE_ID, 874, 699, 512],
            wait=3.0, required=False,
        )
        if match is not None and match.scene_id in (873, 874):
            scene_id = match.scene_id
            count_shape = "完成次数" if scene_id == 873 else "已完成除魔次数"
            for attempt in range(5):
                text = context.ocr_text_in_shapes(
                    scene_id, [count_shape],
                    crop=True, padding=10,
                )
                completed = parse_magic_invasion_completed_count(text)
                if completed is not None:
                    last = read_magic_invasion_counters()
                    validate_magic_invasion_completion_count(completed, count, is_in_auto=last.get("is_in_auto"))
                    return {"terminal": "completed", "completed_exorcisms": completed,
                            "observed_at_epoch": time.time(), "counters": last,
                            "result_text": text, "terminal_scene_id": scene_id}
                yield from context.wait_action_settle(1.0)
            raise RuntimeError("魔道完成次数连续5帧未能读出，保留弹窗")
        if match is not None and match.scene_id == 512:
            last = read_magic_invasion_counters()
            running = last.get("is_in_auto")
            if type(running) is not bool:
                raise RuntimeError("魔道自动状态未知，保留原批次待核验，禁止重复启动")
            if running:
                stopped_at = None
            elif stopped_at is None:
                # The map frame can precede the completion notice even when
                # the subsequent Runtime read already observes the stop.
                # Allow that transition, but never replace actual-count proof
                # with the stopped flag or wait indefinitely for a missing page.
                stopped_at = time.monotonic()
        if stopped_at is not None and time.monotonic() - stopped_at >= 15.0:
            raise RuntimeError("魔道自动已停止但完成奖励页缺失，保留原批次待核验，禁止重复启动")
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


def close_magic_invasion_completed_batch(context: Any, terminal: Mapping[str, Any]) -> Iterator[Any]:
    """Finish a settled batch's direct result pages without another start.

    #873 can close to the map or to #874. The latter repeats the actual count;
    verify it against the settled batch before dismissing its reward page.
    """
    scene_id = int(terminal["terminal_scene_id"])
    yield from context.wait_click(*magic_invasion_completion_exit(scene_id))
    match = yield from context.wait_scene([512, 874] if scene_id == 873 else [512], wait=15.0)
    if match is not None and match.scene_id == 874:
        yield from wait_magic_invasion_auto_completion(context,
            count=int(terminal["completed_exorcisms"]), terminal_polls=2)
        yield from context.wait_click(*magic_invasion_completion_exit(874))
        match = yield from context.wait_scene([512], wait=15.0)
    if match is None or match.scene_id != 512:
        raise RuntimeError("魔道批次已结算，但结果弹窗尚未退出到地图")


def finish_settled_magic_invasion_batch(context: Any, state: Mapping[str, Any]) -> Iterator[Any]:
    """Consume a still-visible completion before navigation on Job reentry."""
    if state.get("pending_batch") or not state.get("measurements"):
        return
    previous_count = int(state["measurements"][-1]["completed_exorcisms"])
    validate_magic_invasion_completion_count(previous_count, previous_count,
        is_in_auto=read_magic_invasion_counters().get("is_in_auto"))
    match = yield from context.wait_scene([873, 874, 512, 698, 34], wait=3.0, required=False)
    if match is not None and match.scene_id in (873, 874):
        terminal = yield from wait_magic_invasion_auto_completion(context,
            count=previous_count, terminal_polls=2)
        yield from close_magic_invasion_completed_batch(context, terminal)


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
    recovered_pending = pending is not None
    if pending is None and state.get("measurements"):
        # A crash after durable settlement but before confirmation must only
        # finish that confirmation, never repeat the recorded batch.
        yield from finish_settled_magic_invasion_batch(context, state)
    if phase == "initialization" and pending is None and (state.get("measurements") or state["status"] == "skipped_this_occurrence"):
        return {"status": "already_terminal", "state": state}
    batch = None
    if pending is None:
        closed = {"status": "unavailable", "reason": "fast_mode_window_closed", "state": state}
        if not magic_invasion_auto_start_window_open(occurrence, now_epoch=time.time()):
            return closed
        validate_magic_invasion_completion_count(count, count,
            is_in_auto=read_magic_invasion_counters().get("is_in_auto"))
        with _batch_phase_timing(context, "设置与次数回读", occurrence=occurrence, count=count):
            batch = yield from configure_magic_invasion_auto_for_batch(
                context, count=count, max_quality_scrolls=max_quality_scrolls,
            )
        with _batch_phase_timing(context, "启动前原生基线", occurrence=occurrence, count=count):
            wallet = _wallet_snapshot(occurrence)
            counters = read_magic_invasion_counters()
        validate_magic_invasion_completion_count(count, count, is_in_auto=counters.get("is_in_auto"))
        if counters.get("ranking_score") is None:
            raise RuntimeError("魔道批次启动前积分不可用或自动除魔仍在运行")
        if not magic_invasion_auto_start_window_open(occurrence, now_epoch=time.time()):
            return closed
        baseline = {"magic_crystal": int(wallet["exchange_currency"]),
                    "ranking_score": int(counters["ranking_score"]),
                    "pid": counters["pid"],
                    "process_start_ticks": counters["process_start_ticks"]}
        armed = arm_batch(occurrence, baseline=baseline, **arm_options)
        if armed.get("recovered_pending"):
            raise RuntimeError("魔道批次授权冲突，拒绝重复启动")
        pending = armed["pending_batch"]
        with _batch_phase_timing(context, "原生挑战及终态观察", occurrence=occurrence, count=count):
            terminal = yield from start_magic_invasion_auto(
                context, count=count, terminal_polls=terminal_polls, poll_seconds=poll_seconds,
            )
    else:
        if int(pending["requested_exorcisms"]) != count or not pending.get("baseline"):
            raise RuntimeError("魔道待结算批次缺少一致的请求/基线，保留现场")
        from .magic_invasion import load_magic_invasion_occurrence_evidence
        proof = load_magic_invasion_occurrence_evidence(occurrence).get("magic_auto_completion_trace")
        if phase == "initialization" and isinstance(proof, Mapping) and proof.get("batch_id") == pending["batch_id"]:
            counters = read_magic_invasion_counters()
            validate_magic_invasion_completion_count(count, count, is_in_auto=counters.get("is_in_auto"))
            path = Path(proof["frame_path"])
            if (proof.get("occurrence_id") != occurrence.occurrence_id
                or proof.get("baseline") != pending["baseline"]
                or proof.get("completed_exorcisms") != count
                or hashlib.sha256(path.read_bytes()).hexdigest() != proof["frame_sha256"]
                or counters.get("ranking_score") is None
                or int(counters["ranking_score"]) - int(pending["baseline"]["ranking_score"]) != proof["ranking_score_delta"]):
                raise RuntimeError("原完成截图与当前批次、进程或积分不一致")
            match = yield from context.wait_scene([512], wait=15.0)
            if match is None or match.scene_id != 512:
                raise RuntimeError("恢复结账要求已关闭奖励的真实魔道地图")
            terminal = {"counters": counters, "observed_at_epoch": proof["observed_at_epoch"],
                        "completed_exorcisms": count, "reward_already_closed": True,
                        "completion_trace": dict(proof)}
        else:
            with _batch_phase_timing(context, "原批次终态恢复", occurrence=occurrence, count=count):
                terminal = yield from wait_magic_invasion_auto_completion(
                    context, count=count, terminal_polls=terminal_polls, poll_seconds=poll_seconds,
                )
    baseline = pending["baseline"]
    with _batch_phase_timing(context, "结算钱包回读", occurrence=occurrence, count=count):
        after = _wallet_snapshot(occurrence)
    counters = terminal["counters"]
    if any(counters[key] != baseline[key] for key in ("pid", "process_start_ticks")):
        raise RuntimeError("魔道批次跨越游戏进程，拒绝混用基线")
    crystal_delta = int(after["exchange_currency"]) - baseline["magic_crystal"]
    score_delta = int(counters["ranking_score"]) - baseline["ranking_score"]
    # Recovery preserves count/yield evidence, but its armed-to-terminal wall
    # interval can contain a failed Cell and an unobserved wait. Both journals
    # retain that interval without promoting it to a new speed estimate.
    timing_options = {"duration_reliable": not (
        recovered_pending or bool(terminal.get("reward_already_closed")))}
    state = settle_batch(
        occurrence, batch_id=pending["batch_id"], completed_exorcisms=count,
        magic_crystal_delta=crystal_delta, ranking_score_delta=score_delta,
        duration_seconds=terminal["observed_at_epoch"] - pending["armed_at_epoch"],
        **timing_options,
    )
    with _batch_phase_timing(context, "领奖页退出到地图", occurrence=occurrence, count=count):
        if not terminal.get("reward_already_closed"):
            yield from close_magic_invasion_completed_batch(context, terminal)
        else:
            yield from context.wait_scene([512], wait=15.0)
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
