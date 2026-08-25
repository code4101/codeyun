from __future__ import annotations

"""Production Magic Invasion exploration shared by server and cross-server.

The workflow is intentionally occurrence-scoped.  A server-internal preview
and the following cross-server round reuse the same actions, but each exact
Runtime occurrence independently owes three confirmed 500-base-explore
batches.  Mail is outside the critical path: rank mail is handled by the
idempotent mail job and its absence never blocks exploration.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import re
import threading
import time
from typing import Any, Iterator, Mapping

from backend.core.fanxiu.activity.magic_invasion_explore import (
    MAGIC_INVASION_EXPLORE_BATCH_SIZE,
    MAGIC_INVASION_TARGET_BATCHES,
    WHITE_DRAGON_EFFECT_ALIASES,
    actual_magic_invasion_topup,
)
from backend.core.fanxiu.data_annotation.tasks.magic_invasion_supply import (
    TIANYAN_ITEM_ID,
)
from backend.core.fanxiu.data_annotation.tasks.yunmeng_native_auto import (
    YunmengNativeAutoAssets,
    _set_count as _set_verified_slider_count,
)
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.core.fanxiu.instrumentation.magic_invasion_task_rewards import (
    read_magic_invasion_task_reward_snapshot,
)


MAGIC_INVASION_TASK_TYPE = "magic_invasion_explore"
MAGIC_INVASION_TASK_ID = "magic-invasion-explore"
MAGIC_INVASION_PROGRESS_KEY = "magic_invasion_progress"
MAGIC_INVASION_ACTIVITY_TYPE_ID = 7
MAGIC_INVASION_MAIN_SCENE_ID = 509
MAGIC_INVASION_TASK_DEMON_SCENE_ID = 510
MAGIC_INVASION_TASK_CULTIVATION_SCENE_ID = 511
MAGIC_INVASION_MAP_SCENE_ID = 512
MAGIC_INVASION_ITEM_SCENE_ID = 513
MAGIC_INVASION_USE_SCENE_ID = 514
MAGIC_INVASION_RESULT_SCENE_ID = 515
MAGIC_INVASION_EVENT_SCENE_ID = 516
MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID = 517
MAGIC_INVASION_OVERFLOW_SCENE_ID = 518
MAGIC_INVASION_ENTRY_TRANSITION_SCENE_ID = 641
MAGIC_INVASION_MAP_ENTRY_SETTLE_TIMEOUT_SECONDS = 90.0


@dataclass(frozen=True)
class MagicInvasionOccurrence:
    occurrence_id: str
    activity_id: int
    runtime_id: int
    start_time_ms: int
    end_time_ms: int
    server_count: int
    mode: str


def _ms(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def magic_invasion_occurrences(
    schedule: Mapping[str, Any],
) -> tuple[MagicInvasionOccurrence, ...]:
    rows: list[MagicInvasionOccurrence] = []
    for raw in schedule.get("items") or ():
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        if int(item.get("activityType") or 0) != MAGIC_INVASION_ACTIVITY_TYPE_ID:
            continue
        activity_id = int(item.get("activityId") or 0)
        runtime_id = int(item.get("id") or 0)
        start_ms = _ms(item.get("startTime"))
        end_ms = _ms(item.get("endTime"))
        if activity_id <= 0 or runtime_id <= 0 or start_ms <= 0 or end_ms < start_ms:
            continue
        server_count = max(1, int(item.get("serverCount") or 1))
        rows.append(
            MagicInvasionOccurrence(
                occurrence_id=str(runtime_id),
                activity_id=activity_id,
                runtime_id=runtime_id,
                start_time_ms=start_ms,
                end_time_ms=end_ms,
                server_count=server_count,
                mode="server" if server_count <= 1 else "cross",
            )
        )
    return tuple(sorted(rows, key=lambda row: (row.start_time_ms, row.runtime_id)))


def current_magic_invasion_occurrence(
    schedule: Mapping[str, Any],
    *,
    now: datetime,
) -> MagicInvasionOccurrence | None:
    now_ms = int(now.timestamp() * 1000)
    current = [
        item
        for item in magic_invasion_occurrences(schedule)
        if item.start_time_ms <= now_ms <= item.end_time_ms
    ]
    if len(current) > 1:
        raise RuntimeError("同时命中多个魔道入侵 Runtime 实例，拒绝猜测")
    return current[0] if current else None


def next_magic_invasion_probe_time(now: datetime) -> datetime:
    """Daily fail-safe probe; active-day Runtime identity gates every click."""

    candidate = now.replace(hour=10, minute=1, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def parse_available_explore_count(text: str) -> int:
    """Parse the Tianyan-backed available-explore counter, never event progress."""

    value = str(text or "")
    if "挑战事件" in value:
        raise RuntimeError(f"魔道入侵误把挑战事件进度当成可用探查次数：{text!r}")
    match = re.search(r"(\d+)\s*/\s*120(?!\d)", value)
    if match is None:
        raise RuntimeError(f"魔道入侵可用探查次数不可读：{text!r}")
    return int(match.group(1))


def parse_owned_item_count(text: str) -> int:
    match = re.search(r"持有数量\s*[:：]?\s*(\d+)", str(text or ""))
    if match is None:
        raise RuntimeError(f"天眼符持有数量不可读：{text!r}")
    return int(match.group(1))


def parse_selected_item_count(text: str) -> int:
    values = [int(value) for value in re.findall(r"(?<!\d)(\d+)(?!\d)", str(text or ""))]
    if len(values) != 1:
        raise RuntimeError(f"天眼符使用数量不唯一：{text!r}")
    return values[0]


def slider_fraction(*, quantity: int, owned_count: int) -> float:
    if owned_count <= 0 or not 1 <= quantity <= owned_count:
        raise ValueError("天眼符滑块目标超出持有数量")
    if owned_count == 1:
        return 0.0
    return (int(quantity) - 1) / (int(owned_count) - 1)


def _wait_scene(
    runtime: Any,
    targets: tuple[int, ...],
    *,
    timeout_seconds: float = 20.0,
) -> tuple[int, float, str]:
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    last_scene: int | None = None
    last_score = 0.0
    last_frame = ""
    while time.monotonic() < deadline:
        last_scene, last_score, last_frame = runtime.current_scene(list(targets), update=True)
        if int(last_scene or 0) in targets and float(last_score) >= 80.0:
            return int(last_scene), float(last_score), last_frame
        time.sleep(0.25)
    raise RuntimeError(
        f"等待魔道入侵场景超时：targets={targets}, scene={last_scene}, score={last_score:.1f}"
    )


def _shape_text(runtime: Any, scene_id: int, shape_title: str) -> str:
    frame = runtime.cur_frame(update=True)
    fragments = runtime.ocr_fragments_in_shapes(
        scene_id,
        (shape_title,),
        frame_data_url=frame,
        padding=8,
        crop=True,
    )
    return " ".join(str(item.get("text") or "") for item in fragments).strip()


def _wait_magic_invasion_map_entry_settle(runtime: Any) -> tuple[int, float, str]:
    """Wait through the passive entry animation until the map is stable."""

    observed_targets = (
        MAGIC_INVASION_TASK_DEMON_SCENE_ID,
        MAGIC_INVASION_TASK_CULTIVATION_SCENE_ID,
        MAGIC_INVASION_MAP_SCENE_ID,
        MAGIC_INVASION_ENTRY_TRANSITION_SCENE_ID,
    )
    started_at = time.monotonic()
    deadline = started_at + MAGIC_INVASION_MAP_ENTRY_SETTLE_TIMEOUT_SECONDS
    last_scene: int | None = None
    last_score = 0.0
    last_frame = ""
    last_state = "unknown"
    while time.monotonic() < deadline:
        scene, score, frame = runtime.current_scene(list(observed_targets), update=True)
        last_scene = int(scene) if scene is not None else None
        last_score = float(score or 0.0)
        last_frame = frame
        if last_scene == MAGIC_INVASION_MAP_SCENE_ID and last_score >= 80.0:
            return last_scene, last_score, last_frame
        elapsed = max(0.0, time.monotonic() - started_at)
        if (
            last_scene
            in {
                MAGIC_INVASION_TASK_DEMON_SCENE_ID,
                MAGIC_INVASION_TASK_CULTIVATION_SCENE_ID,
            }
            and last_score >= 80.0
        ):
            raise RuntimeError(
                "魔道入侵入口误落任务页，拒绝当作入口过渡："
                f"scene=#{last_scene}, score={last_score:.1f}, elapsed={elapsed:.1f}s"
            )
        last_state = (
            f"transition#{MAGIC_INVASION_ENTRY_TRANSITION_SCENE_ID}"
            if last_scene == MAGIC_INVASION_ENTRY_TRANSITION_SCENE_ID and last_score >= 80.0
            else "unknown"
        )
        # #641 is read-only and auto-dismissing.  Unknown is tolerated only
        # inside this bounded flight-animation window; neither is success.
        time.sleep(0.25)
    elapsed = max(0.0, time.monotonic() - started_at)
    raise RuntimeError(
        "等待魔道入侵入口稳定落到 #512 超时："
        f"elapsed={elapsed:.1f}s, last_state={last_state}, "
        f"scene={last_scene}, score={last_score:.1f}"
    )


def _enter_magic_invasion_map(runtime: Any) -> None:
    """Enter the map while explicitly consuming the Magic-specific confirm layer."""

    runtime.click_shape(MAGIC_INVASION_MAIN_SCENE_ID, "前往大地图")
    scene, _score, _frame = _wait_scene(
        runtime,
        (
            MAGIC_INVASION_MAP_SCENE_ID,
            MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID,
            MAGIC_INVASION_TASK_DEMON_SCENE_ID,
            MAGIC_INVASION_TASK_CULTIVATION_SCENE_ID,
            MAGIC_INVASION_ENTRY_TRANSITION_SCENE_ID,
        ),
        timeout_seconds=30.0,
    )
    if scene == MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID:
        runtime.click_shape(MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID, "确认")
        scene, _score, _frame = _wait_magic_invasion_map_entry_settle(runtime)
    elif scene in {
        MAGIC_INVASION_TASK_DEMON_SCENE_ID,
        MAGIC_INVASION_TASK_CULTIVATION_SCENE_ID,
    }:
        raise RuntimeError(f"魔道入侵入口误落任务页 #{scene}，拒绝继续进入大地图")
    elif scene != MAGIC_INVASION_MAP_SCENE_ID:
        _wait_magic_invasion_map_entry_settle(runtime)


def _leave_magic_invasion_map(runtime: Any) -> None:
    """Leave the sandbox without confusing the map-entry confirmation for an exit."""

    runtime.click_shape_center(MAGIC_INVASION_MAP_SCENE_ID, "地图返回")
    scene, _score, _frame = _wait_scene(
        runtime,
        (34, MAGIC_INVASION_MAIN_SCENE_ID),
        timeout_seconds=15.0,
    )
    if scene == MAGIC_INVASION_MAIN_SCENE_ID:
        runtime.click_shape_center(MAGIC_INVASION_MAIN_SCENE_ID, "返回")
        _wait_scene(runtime, (34,), timeout_seconds=15.0)


def _set_progress(
    runner: Any,
    task_id: str,
    payload: dict[str, Any],
    progress: Mapping[str, Any],
) -> None:
    value = dict(progress)
    if not runner._set_scheduler_task_payload_flag(task_id, MAGIC_INVASION_PROGRESS_KEY, value):
        raise RuntimeError("魔道入侵防重复进度未持久化，拒绝继续不可逆操作")
    payload[MAGIC_INVASION_PROGRESS_KEY] = value


def _progress_for_occurrence(
    payload: Mapping[str, Any], occurrence: MagicInvasionOccurrence
) -> dict[str, Any]:
    existing = payload.get(MAGIC_INVASION_PROGRESS_KEY)
    if not isinstance(existing, Mapping):
        existing = {}
    old_id = str(existing.get("occurrence_id") or "")
    old_state = str(existing.get("state") or "")
    if old_id and old_id != occurrence.occurrence_id and old_state not in {"", "complete"}:
        raise RuntimeError(
            "上一魔道入侵实例存在未闭合不可逆批次，拒绝用新实例覆盖防重复证据"
        )
    if old_id == occurrence.occurrence_id:
        return dict(existing)
    return {
        "occurrence_id": occurrence.occurrence_id,
        "activity_id": occurrence.activity_id,
        "mode": occurrence.mode,
        "server_count": occurrence.server_count,
        "state": "ready",
        "confirmed_batches": [],
        "base_explore_count": 0,
        "mail_policy": "optional_not_prerequisite",
    }


def _configure_use_quantity(runtime: Any, *, quantity: int) -> Iterator[Any]:
    owned = parse_owned_item_count(_shape_text(runtime, MAGIC_INVASION_USE_SCENE_ID, "持有数量"))
    if quantity > owned:
        raise RuntimeError(f"天眼符不足：需要 {quantity}，持有 {owned}")
    assets = YunmengNativeAutoAssets(
        home_scene_id=MAGIC_INVASION_MAP_SCENE_ID,
        settings_scene_id=MAGIC_INVASION_USE_SCENE_ID,
        terminal_scene_ids=(MAGIC_INVASION_USE_SCENE_ID,),
        count_region="使用数量",
        count_decrease="数量减",
        count_increase="数量加",
        count_slider_thumb="数量滑块游标",
        count_minimum_marker="使用数量为1",
        count_slider_left_anchor="数量滑轨左端",
        count_slider_right_anchor="数量滑轨右端",
    )
    calibration = yield from _set_verified_slider_count(
        runtime,
        assets,
        int(quantity),
        max_adjustments=10,
        force_bound_probe=True,
        count_label="天眼符使用数量",
    )
    calibration_evidence = dict(calibration or {})
    try:
        verified = int(calibration_evidence["after"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("天眼符使用数量缺少稳定读回证据") from exc
    if verified != int(quantity):
        raise RuntimeError(f"天眼符使用数量验证失败：目标 {quantity}，实际 {verified}")
    return {
        "owned_count": owned,
        "single_use_maximum": int(calibration_evidence.get("maximum") or 0),
        "selected_count": verified,
        "slider_calibration": calibration_evidence,
    }


def _prepare_top_up_to_batch(runtime: Any, *, available_count: int) -> Iterator[Any]:
    """Prepare the Tianyan use dialog without committing the use action."""

    topup = MAGIC_INVASION_EXPLORE_BATCH_SIZE - int(available_count)
    if topup < 0:
        raise RuntimeError(f"魔道入侵可用探查次数越界：{available_count}>500")
    if topup == 0:
        return {"requested_topup": 0, "selected_count": 0}
    runtime.click_shape_center(MAGIC_INVASION_MAP_SCENE_ID, "补充探查次数")
    _wait_scene(runtime, (MAGIC_INVASION_ITEM_SCENE_ID,))
    runtime.click_shape_center(MAGIC_INVASION_ITEM_SCENE_ID, "天眼符条目")
    _wait_scene(runtime, (MAGIC_INVASION_USE_SCENE_ID,))
    calibration = yield from _configure_use_quantity(runtime, quantity=topup)
    return {"requested_topup": topup, **dict(calibration or {})}


def _commit_prepared_top_up(runtime: Any) -> int:
    """Commit an already prepared use dialog and return the authoritative map count."""

    runtime.click_shape_center(MAGIC_INVASION_USE_SCENE_ID, "使用")
    _wait_scene(runtime, (MAGIC_INVASION_ITEM_SCENE_ID,))
    runtime.click_shape_center(MAGIC_INVASION_ITEM_SCENE_ID, "关闭道具列表")
    _wait_scene(runtime, (MAGIC_INVASION_MAP_SCENE_ID,))
    verified = parse_available_explore_count(
        _shape_text(runtime, MAGIC_INVASION_MAP_SCENE_ID, "可用探查次数")
    )
    if verified != MAGIC_INVASION_EXPLORE_BATCH_SIZE:
        raise RuntimeError(f"魔道入侵补充后不是精确 500 次：{verified}")
    return verified


def _top_up_to_batch(runtime: Any, *, available_count: int) -> Iterator[Any]:
    """Compatibility wrapper for callers that do not need transaction arming."""

    evidence = yield from _prepare_top_up_to_batch(
        runtime, available_count=available_count
    )
    requested = int(evidence["requested_topup"])
    verified = (
        _commit_prepared_top_up(runtime)
        if requested
        else int(available_count)
    )
    return {
        **evidence,
        "available_explore_count_after_topup": verified,
    }


def _read_tianyan_inventory() -> dict[str, Any]:
    counts, evidence = read_backpack_item_counts(
        (TIANYAN_ITEM_ID,),
        manager_key="magic-invasion-explore",
    )
    return {
        "count": int(counts.get(TIANYAN_ITEM_ID) or 0),
        "evidence": dict(evidence),
    }


def _compact_task_snapshot(activity_id: int) -> dict[str, Any]:
    snapshot = read_magic_invasion_task_reward_snapshot(int(activity_id))
    if not bool(snapshot.get("ok") and snapshot.get("available") and snapshot.get("complete")):
        raise RuntimeError(
            "魔道入侵任务权威快照不可用或不完整："
            f"{snapshot.get('reason') or snapshot.get('state') or 'unknown'}"
        )
    tasks = []
    for raw in snapshot.get("tasks") or ():
        if not isinstance(raw, Mapping):
            continue
        tasks.append(
            {
                "task_id": int(raw.get("task_id") or 0),
                "status": int(raw.get("status") or 0),
                "turn": int(raw.get("turn") or 0),
                "reward_time": int(raw.get("reward_time") or 0),
                "progress_complete": bool(raw.get("progress_complete")),
                "claimed": bool(raw.get("claimed")),
                "claimable": bool(raw.get("claimable")),
            }
        )
    return {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "state": str(snapshot.get("state") or ""),
        "claimable_task_ids": [int(value) for value in snapshot.get("claimable_task_ids") or ()],
        "claimed_task_ids": [int(value) for value in snapshot.get("claimed_task_ids") or ()],
        "pending_task_ids": [int(value) for value in snapshot.get("pending_task_ids") or ()],
        "tasks": tasks,
        "source": str(snapshot.get("source") or ""),
        "protocol": str(snapshot.get("protocol") or ""),
    }


def _task_snapshot_did_not_regress(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> bool:
    """Conservatively reject a post-explore task snapshot that went backwards."""

    before_rows = {
        int(row.get("task_id") or 0): dict(row)
        for row in before.get("tasks") or ()
        if isinstance(row, Mapping) and int(row.get("task_id") or 0) > 0
    }
    after_rows = {
        int(row.get("task_id") or 0): dict(row)
        for row in after.get("tasks") or ()
        if isinstance(row, Mapping) and int(row.get("task_id") or 0) > 0
    }
    if set(before_rows) - set(after_rows):
        return False
    monotonic_fields = ("status", "turn", "reward_time")
    for task_id, old in before_rows.items():
        new = after_rows[task_id]
        if any(int(new.get(key) or 0) < int(old.get(key) or 0) for key in monotonic_fields):
            return False
        if bool(old.get("progress_complete")) and not bool(new.get("progress_complete")):
            return False
        if bool(old.get("claimed")) and not bool(new.get("claimed")):
            return False
    return True


def _legacy_phase(state: Any) -> str:
    phase = str(state or "ready")
    return "explore_armed" if phase == "armed" else phase


def _white_dragon_result(result_text: str) -> dict[str, Any]:
    matched = next(
        (alias for alias in WHITE_DRAGON_EFFECT_ALIASES if alias in str(result_text or "")),
        None,
    )
    return {
        "observed": matched is not None,
        "matched_alias": matched,
        "evidence_text": str(result_text or "")[:500],
    }


def execute_magic_invasion_explore_job(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    manage_schedule: bool = True,
    prepared_runtime: Any | None = None,
    prepared_schedule: Mapping[str, Any] | None = None,
    already_on_main_scene: bool = False,
    already_on_map_scene: bool = False,
) -> Iterator[Any]:
    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )
    from backend.core.fanxiu.data_annotation.schedule_navigation import (
        select_schedule_activity,
    )

    target_batches = int(payload.get("target_batches") or MAGIC_INVASION_TARGET_BATCHES)
    batch_size = int(payload.get("batch_size") or MAGIC_INVASION_EXPLORE_BATCH_SIZE)
    if target_batches != 3 or batch_size != 500:
        raise ValueError("魔道入侵生产策略固定为每实例 3×500 基础探查")
    now = datetime.now().astimezone()
    schedule = dict(prepared_schedule) if prepared_schedule is not None else (
        read_fanxiu_activity_runtime_schedule(
            allow_discovery=True,
            force_refresh=True,
        )
    )
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError("魔道入侵 Runtime 日程不可用，拒绝只按页面名称点击")
    occurrence = current_magic_invasion_occurrence(schedule, now=now)
    task_id = str(ctx.get("scheduler_task_id") or MAGIC_INVASION_TASK_ID)
    expected_occurrence_id = str(payload.get("expected_occurrence_id") or "").strip()
    if expected_occurrence_id:
        if occurrence is None:
            raise RuntimeError(
                "统一榜单 checkpoint 指定的魔道 occurrence 当前已不在活动期，"
                "拒绝把零动作记为完成"
            )
        if occurrence.occurrence_id != expected_occurrence_id:
            raise RuntimeError(
                "魔道入侵当前 Runtime occurrence 与统一榜单 checkpoint 不一致"
            )
    if occurrence is None:
        next_time = next_magic_invasion_probe_time(now)
        if manage_schedule:
            runner._persist_scheduler_task_next_time(task_id, next_time)
        return {
            "result": "success",
            "message": f"魔道入侵：当前无活动实例，下次探针 {next_time:%Y-%m-%d %H:%M:%S}",
            "performed_actions": False,
            **({"next_time": next_time.isoformat()} if manage_schedule else {}),
        }

    progress = _progress_for_occurrence(payload, occurrence)
    progress["state"] = _legacy_phase(progress.get("state"))
    confirmed = list(progress.get("confirmed_batches") or [])
    if len(confirmed) >= target_batches:
        progress["state"] = "complete"
        _set_progress(runner, task_id, payload, progress)
        next_time = next_magic_invasion_probe_time(now)
        if manage_schedule:
            runner._persist_scheduler_task_next_time(task_id, next_time)
        return {
            "result": "success",
            "message": f"魔道入侵 {occurrence.mode} 实例已幂等完成 1500 次",
            "performed_actions": False,
            "progress": progress,
        }

    runtime = prepared_runtime or runner._fanxiu_runtime(ctx, stop_event=stop_event)
    phase = str(progress["state"])
    if phase in {"ready", "confirmed"}:
        if already_on_map_scene:
            _wait_scene(runtime, (MAGIC_INVASION_MAP_SCENE_ID,), timeout_seconds=15.0)
        else:
            if already_on_main_scene:
                _wait_scene(runtime, (MAGIC_INVASION_MAIN_SCENE_ID,), timeout_seconds=15.0)
            else:
                yield from runtime.goto_view(66)
                yield from select_schedule_activity(
                    runtime,
                    r"魔道入侵",
                    enter=True,
                    runtime_schedule=schedule,
                    require_runtime_alignment=True,
                    now=now,
                )
                _wait_scene(runtime, (MAGIC_INVASION_MAIN_SCENE_ID,), timeout_seconds=30.0)
            _enter_magic_invasion_map(runtime)

    def ensure_fast_explore_enabled() -> Iterator[Any]:
        if runtime.shape_matches(MAGIC_INVASION_MAP_SCENE_ID, "快速探索开启态") is None:
            runtime.click_shape_center(MAGIC_INVASION_MAP_SCENE_ID, "快速探索开关")
            yield from runtime.wait_action_settle(0.5)
            if runtime.shape_matches(MAGIC_INVASION_MAP_SCENE_ID, "快速探索开启态") is None:
                raise RuntimeError("魔道入侵快速探索开关未进入开启态")

    while len(confirmed) < target_batches:
        if stop_event.is_set():
            raise InterruptedError()
        batch_index = len(confirmed) + 1
        base_explore_before = len(confirmed) * MAGIC_INVASION_EXPLORE_BATCH_SIZE
        phase = _legacy_phase(progress.get("state"))
        evidence = dict(progress.get("transaction_evidence") or progress.get("armed_evidence") or {})

        if phase in {"ready", "confirmed"}:
            yield from ensure_fast_explore_enabled()
            available_count = parse_available_explore_count(
                _shape_text(runtime, MAGIC_INVASION_MAP_SCENE_ID, "可用探查次数")
            )
            tianyan_before = _read_tianyan_inventory()
            task_before = _compact_task_snapshot(occurrence.activity_id)
            prepared = yield from _prepare_top_up_to_batch(
                runtime, available_count=available_count
            )
            evidence = {
                "base_explore_before": base_explore_before,
                "available_explore_count_before": available_count,
                **prepared,
                "tianyan_before": tianyan_before,
                "task_progress_before": task_before,
            }
            requested_topup = int(prepared["requested_topup"])
            if requested_topup:
                progress.update({
                    "state": "use_armed",
                    "batch_index": batch_index,
                    "transaction_evidence": evidence,
                })
                _set_progress(runner, task_id, payload, progress)
                verified_topup = _commit_prepared_top_up(runtime)
            else:
                verified_topup = available_count
        elif phase == "use_armed":
            requested_topup = int(evidence.get("requested_topup") or 0)
            if requested_topup <= 0:
                raise RuntimeError("魔道入侵 use_armed 缺少有效补充意图，拒绝恢复")
            tianyan_before = dict(evidence.get("tianyan_before") or {})
            tianyan_after = _read_tianyan_inventory()
            actual_topup = actual_magic_invasion_topup(
                inventory_before=int(tianyan_before.get("count") or 0),
                inventory_after=tianyan_after["count"],
            )
            if actual_topup != requested_topup:
                raise RuntimeError(
                    "魔道入侵 use_armed 后置事实不确定，禁止重放使用："
                    f"预期扣减 {requested_topup}，实际扣减 {actual_topup}"
                )
            scene, _score, _frame = _wait_scene(
                runtime,
                (MAGIC_INVASION_ITEM_SCENE_ID, MAGIC_INVASION_MAP_SCENE_ID),
                timeout_seconds=15.0,
            )
            if scene == MAGIC_INVASION_ITEM_SCENE_ID:
                runtime.click_shape_center(MAGIC_INVASION_ITEM_SCENE_ID, "关闭道具列表")
                _wait_scene(runtime, (MAGIC_INVASION_MAP_SCENE_ID,))
            verified_topup = parse_available_explore_count(
                _shape_text(runtime, MAGIC_INVASION_MAP_SCENE_ID, "可用探查次数")
            )
        elif phase == "topup_confirmed":
            verified_topup = parse_available_explore_count(
                _shape_text(runtime, MAGIC_INVASION_MAP_SCENE_ID, "可用探查次数")
            )
        else:
            verified_topup = MAGIC_INVASION_EXPLORE_BATCH_SIZE

        if phase in {"ready", "confirmed", "use_armed"}:
            requested_topup = int(evidence.get("requested_topup") or 0)
            tianyan_before = dict(evidence.get("tianyan_before") or {})
            tianyan_after = _read_tianyan_inventory()
            actual_topup = actual_magic_invasion_topup(
                inventory_before=int(tianyan_before.get("count") or 0),
                inventory_after=tianyan_after["count"],
            )
            before_process = (
                dict(tianyan_before.get("evidence") or {}).get("pid"),
                dict(tianyan_before.get("evidence") or {}).get("process_start_ticks"),
            )
            after_process = (
                tianyan_after["evidence"].get("pid"),
                tianyan_after["evidence"].get("process_start_ticks"),
            )
            if before_process != after_process:
                raise RuntimeError("天眼符批次对账期间游戏进程代际变化，拒绝拼接快照")
            if actual_topup != requested_topup or verified_topup != MAGIC_INVASION_EXPLORE_BATCH_SIZE:
                raise RuntimeError(
                    "天眼符权威库存精确扣减与地图 500 未成对成立，拒绝继续探查"
                )
            evidence.update({
                "available_explore_count_after_topup": verified_topup,
                "tianyan_after_topup": tianyan_after,
                "tianyan_consumed": actual_topup,
            })
            progress.update({
                "state": "topup_confirmed",
                "batch_index": batch_index,
                "transaction_evidence": evidence,
            })
            _set_progress(runner, task_id, payload, progress)
            phase = "topup_confirmed"

        if phase == "topup_confirmed":
            if verified_topup != MAGIC_INVASION_EXPLORE_BATCH_SIZE:
                raise RuntimeError("魔道入侵 topup_confirmed 地图已不再是 500，拒绝探查")
            progress["state"] = "explore_armed"
            _set_progress(runner, task_id, payload, progress)
            runtime.click_shape_center(MAGIC_INVASION_MAP_SCENE_ID, "探查")
            phase = "explore_armed"

        result_full_text = ""
        if phase == "explore_armed":
            scene, _score, _frame = _wait_scene(
                runtime,
                (
                    MAGIC_INVASION_RESULT_SCENE_ID,
                    MAGIC_INVASION_OVERFLOW_SCENE_ID,
                    MAGIC_INVASION_MAP_SCENE_ID,
                    MAGIC_INVASION_EVENT_SCENE_ID,
                ),
                timeout_seconds=30.0,
            )
            if scene == MAGIC_INVASION_OVERFLOW_SCENE_ID:
                runtime.click_shape_center(MAGIC_INVASION_OVERFLOW_SCENE_ID, "确认覆盖")
                _wait_scene(runtime, (MAGIC_INVASION_RESULT_SCENE_ID,), timeout_seconds=30.0)
                scene = MAGIC_INVASION_RESULT_SCENE_ID
            if scene == MAGIC_INVASION_RESULT_SCENE_ID:
                result_frame = runtime.cur_frame(update=True)
                result_text = _shape_text(runtime, MAGIC_INVASION_RESULT_SCENE_ID, "探索次数结果")
                match = re.search(r"快速探索\s*(\d+)\s*次", result_text)
                if match is None:
                    raise RuntimeError(f"魔道入侵结果没有探索次数证据：{result_text!r}")
                result_explore_count = int(match.group(1))
                if result_explore_count != MAGIC_INVASION_EXPLORE_BATCH_SIZE:
                    raise RuntimeError(
                        f"魔道入侵第 {batch_index} 批结果不是精确 500 次：{result_explore_count}"
                    )
                result_full_text = runtime.ocr_text(result_frame)
                result_source = "result_page"
                task_after: dict[str, Any] = {}
            else:
                if scene == MAGIC_INVASION_EVENT_SCENE_ID:
                    runtime.click_shape(MAGIC_INVASION_EVENT_SCENE_ID, "稍后处理")
                    _wait_scene(runtime, (MAGIC_INVASION_MAP_SCENE_ID,))
                available_after = parse_available_explore_count(
                    _shape_text(runtime, MAGIC_INVASION_MAP_SCENE_ID, "可用探查次数")
                )
                if available_after != 0:
                    raise RuntimeError(
                        "魔道入侵 explore_armed 后置事实不确定，禁止重放探查"
                    )
                task_after = _compact_task_snapshot(occurrence.activity_id)
                task_before = dict(evidence.get("task_progress_before") or {})
                if not _task_snapshot_did_not_regress(task_before, task_after):
                    raise RuntimeError("魔道入侵探查后任务权威快照发生回退，拒绝提交")
                result_explore_count = MAGIC_INVASION_EXPLORE_BATCH_SIZE
                result_source = "map_zero_and_task_non_regression"
            evidence.update({
                "result_explore_count": result_explore_count,
                "result_source": result_source,
                "task_progress_after": task_after,
                "result_full_text": result_full_text[:1000],
            })
            progress.update({"state": "result_observed", "transaction_evidence": evidence})
            _set_progress(runner, task_id, payload, progress)
            phase = "result_observed"

        if phase == "result_observed":
            scene, _score, _frame = _wait_scene(
                runtime,
                (
                    MAGIC_INVASION_RESULT_SCENE_ID,
                    MAGIC_INVASION_MAP_SCENE_ID,
                    MAGIC_INVASION_EVENT_SCENE_ID,
                ),
                timeout_seconds=30.0,
            )
            if scene == MAGIC_INVASION_RESULT_SCENE_ID:
                runtime.click_shape(MAGIC_INVASION_RESULT_SCENE_ID, "确定")
                scene, _score, _frame = _wait_scene(
                    runtime,
                    (MAGIC_INVASION_MAP_SCENE_ID, MAGIC_INVASION_EVENT_SCENE_ID),
                    timeout_seconds=30.0,
                )
            if scene == MAGIC_INVASION_EVENT_SCENE_ID:
                runtime.click_shape(MAGIC_INVASION_EVENT_SCENE_ID, "稍后处理")
                _wait_scene(runtime, (MAGIC_INVASION_MAP_SCENE_ID,))
            available_count_after = parse_available_explore_count(
                _shape_text(runtime, MAGIC_INVASION_MAP_SCENE_ID, "可用探查次数")
            )
            if available_count_after != 0:
                raise RuntimeError(
                    f"魔道入侵第 {batch_index} 批结算后可用探查次数未归零：{available_count_after}"
                )
        result_explore_count = int(evidence.get("result_explore_count") or 0)
        task_after = dict(evidence.get("task_progress_after") or {})
        confirmed.append(
            {
                "batch_index": batch_index,
                "base_explore_before": base_explore_before,
                "base_explore_count": MAGIC_INVASION_EXPLORE_BATCH_SIZE,
                "base_explore_after": base_explore_before + MAGIC_INVASION_EXPLORE_BATCH_SIZE,
                "result_explore_count": result_explore_count,
                **evidence,
                "task_progress_after": task_after,
                "white_dragon": _white_dragon_result(str(evidence.get("result_full_text") or "")),
                "available_explore_count_after_result": available_count_after,
                "confirmed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        )
        progress.update(
            {
                "state": "confirmed",
                "confirmed_batches": confirmed,
                "base_explore_count": len(confirmed) * MAGIC_INVASION_EXPLORE_BATCH_SIZE,
            }
        )
        for key in (
            "armed_batch_index", "armed_at", "armed_base_count", "armed_evidence",
            "batch_index", "transaction_evidence",
        ):
            progress.pop(key, None)
        _set_progress(runner, task_id, payload, progress)
        inventory_before_count = int(
            dict(evidence.get("tianyan_before") or {}).get("count") or 0
        )
        inventory_after_count = int(
            dict(evidence.get("tianyan_after_topup") or {}).get("count")
            or inventory_before_count
        )
        runner._log(
            "success",
            f"魔道第 {batch_index}/3 批对账：基础探查 "
            f"{base_explore_before}->{base_explore_before + 500}，"
            f"可用探查次数 {evidence['available_explore_count_before']}->500->0，"
            f"天眼符 {inventory_before_count}->{inventory_after_count}，"
            f"白龙马={'触发' if confirmed[-1]['white_dragon']['observed'] else '未触发'}",
        )

    progress["state"] = "complete"
    progress["completed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    _set_progress(runner, task_id, payload, progress)
    next_time = next_magic_invasion_probe_time(datetime.now().astimezone())
    if manage_schedule:
        runner._persist_scheduler_task_next_time(task_id, next_time)

    # Departure is best effort after the business terminal is durably stored.
    try:
        _leave_magic_invasion_map(runtime)
    except Exception as exc:
        runner._log("info", f"魔道入侵 1500 次已提交；返回世界留待通用恢复：{exc}")

    white_dragon_batches = [
        int(item.get("batch_index") or 0)
        for item in confirmed
        if bool(dict(item.get("white_dragon") or {}).get("observed"))
    ]
    white_dragon_message = (
        f"白龙马于第 {','.join(str(value) for value in white_dragon_batches)} 批触发"
        if white_dragon_batches
        else "本次 1500 次未观察到白龙马触发"
    )
    message = (
        f"魔道入侵 {occurrence.mode} 实例 {occurrence.occurrence_id}："
        f"完成 3×500=1500 次基础探查；{white_dragon_message}；"
        "邮件为可选独立流程"
    )
    runner._log("success", message)
    return {
        "result": "success",
        "message": message,
        "occurrence": asdict(occurrence),
        "progress": progress,
        "white_dragon": {
            "observed": bool(white_dragon_batches),
            "batch_indexes": white_dragon_batches,
            "message": white_dragon_message,
        },
        **({"next_time": next_time.isoformat()} if manage_schedule else {}),
    }


__all__ = [
    "MAGIC_INVASION_PROGRESS_KEY",
    "MAGIC_INVASION_TASK_ID",
    "MAGIC_INVASION_TASK_TYPE",
    "MagicInvasionOccurrence",
    "current_magic_invasion_occurrence",
    "execute_magic_invasion_explore_job",
    "magic_invasion_occurrences",
    "next_magic_invasion_probe_time",
    "parse_available_explore_count",
    "parse_owned_item_count",
    "parse_selected_item_count",
    "slider_fraction",
]
