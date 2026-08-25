from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time
from typing import Any, Callable

from filelock import FileLock

from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.job_times import next_business_time
from backend.core.fanxiu.data_annotation.state import (
    append_data_annotation_world_fact_event,
    read_data_annotation_world_facts,
    write_data_annotation_world_facts,
)
from backend.core.fanxiu.runtime.mumu_control import shake_mumu_device


BUBBLE_WEEKLY_TASK_ID = "bubble-weekly-pills"
BUBBLE_LIFECYCLE_FACT_KEY = "bubble_lifecycle"


def bubble_sdk_overlay_scene(runtime: Any, *, frame: str) -> int | None:
    """Recognize only SDK-owned modal layers on one already-captured frame.

    The underlying game page is intentionally outside this candidate set.
    This probe is a click-through gate, not a request to navigate or classify
    the game scene hidden below the Android overlay.
    """

    for scene_id in (592, 591, 590):
        matched, _score, _matched_frame = runtime.match_view(
            scene_id,
            frame_data_url=frame,
        )
        if matched:
            return scene_id

    # #591 is a scrolling/dynamic list. Its full-page template can miss after
    # scrolling, so retain the structural item evidence used by the claim
    # transaction. This still identifies the SDK overlay rather than the game
    # page underneath it.
    finder = getattr(runtime, "find_floating_items_by_anchor_text", None)
    fully_inside = getattr(runtime, "floating_item_field_is_fully_inside", None)
    if callable(finder) and callable(fully_inside):
        for anchor_text in ("领取", "已领取"):
            items = finder(
                591,
                "礼包条目",
                "领取",
                anchor_text,
                container_shape="窗口",
                frame_data_url=frame,
                match_mode="exact",
            )
            if any(fully_inside(item, "领取", "窗口") for item in items):
                return 591
    return None


def bubble_week_key(now: datetime) -> str:
    iso_year, iso_week, _weekday = now.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def next_bubble_weekly_time(now: datetime) -> str:
    return next_business_time(("00:10",), now=now, weekdays=(0,))


def _facts_lock_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.lock")


def read_bubble_lifecycle_fact(path: Path) -> dict[str, Any]:
    facts = read_data_annotation_world_facts(path)
    discoveries = facts.get("discoveries")
    if not isinstance(discoveries, dict):
        return {}
    fact = discoveries.get(BUBBLE_LIFECYCLE_FACT_KEY)
    return dict(fact) if isinstance(fact, dict) else {}


def _update_bubble_lifecycle_fact(
    path: Path, *, event_kind: str, updates: dict[str, Any]
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(_facts_lock_path(path)), timeout=30):
        facts = read_data_annotation_world_facts(path)
        discoveries = facts.setdefault("discoveries", {})
        if not isinstance(discoveries, dict):
            discoveries = {}
            facts["discoveries"] = discoveries
        previous = discoveries.get(BUBBLE_LIFECYCLE_FACT_KEY)
        fact = dict(previous) if isinstance(previous, dict) else {}
        fact.update({**updates, "updated_at": time.time()})
        discoveries[BUBBLE_LIFECYCLE_FACT_KEY] = fact
        append_data_annotation_world_fact_event(
            facts, event_kind, {"fact_key": BUBBLE_LIFECYCLE_FACT_KEY, **updates}
        )
        write_data_annotation_world_facts(path, facts, _lock_already_held=True)
    return fact


def record_bubble_claim_success(
    path: Path, *, now: datetime, claim_count: int
) -> dict[str, Any]:
    return _update_bubble_lifecycle_fact(
        path,
        event_kind="bubble_claim_success",
        updates={
            "claimed_week": bubble_week_key(now),
            "claimed_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "claim_count": max(0, int(claim_count)),
        },
    )


def bubble_claimed_item_ids(path: Path, *, now: datetime) -> set[str]:
    fact = read_bubble_lifecycle_fact(path)
    if str(fact.get("claim_item_week") or "") != bubble_week_key(now):
        return set()
    return {str(item).strip() for item in fact.get("claimed_item_ids") or [] if str(item).strip()}


def record_bubble_claim_item(
    path: Path, *, now: datetime, item_id: str
) -> dict[str, Any]:
    """Checkpoint one confirmed SDK gift so a failed Cell cannot consume it again."""

    normalized_id = str(item_id or "").strip()
    if not normalized_id:
        raise ValueError("气泡领取检查点缺少礼包身份")
    week = bubble_week_key(now)
    with FileLock(str(_facts_lock_path(path)), timeout=30):
        facts = read_data_annotation_world_facts(path)
        discoveries = facts.setdefault("discoveries", {})
        fact = discoveries.get(BUBBLE_LIFECYCLE_FACT_KEY)
        if not isinstance(fact, dict):
            fact = {}
            discoveries[BUBBLE_LIFECYCLE_FACT_KEY] = fact
        claimed = (
            [str(item).strip() for item in fact.get("claimed_item_ids") or [] if str(item).strip()]
            if str(fact.get("claim_item_week") or "") == week
            else []
        )
        if normalized_id not in claimed:
            claimed.append(normalized_id)
        fact.update({
            "claim_item_week": week,
            "claimed_item_ids": claimed,
            "partial_claim_count": len(claimed),
            "last_claimed_item_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": time.time(),
        })
        append_data_annotation_world_fact_event(
            facts,
            "bubble_claim_item",
            {"fact_key": BUBBLE_LIFECYCLE_FACT_KEY, "week": week, "item_id": normalized_id,
             "partial_claim_count": len(claimed)},
        )
        write_data_annotation_world_facts(path, facts, _lock_already_held=True)
        return dict(fact)


def record_bubble_hidden(path: Path, *, now: datetime) -> dict[str, Any]:
    """Record observed UI state only; visual absence remains the source of truth."""

    return _update_bubble_lifecycle_fact(
        path,
        event_kind="bubble_hidden",
        updates={
            "hidden_week": bubble_week_key(now),
            "hidden_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


def schedule_bubble_reconcile_after_login(
    *, now: datetime, set_next_time: Callable[[str, str | None], Any]
) -> str:
    """Every successful login reconciles the single bubble business invariant."""

    set_next_time(BUBBLE_WEEKLY_TASK_ID, now.strftime("%Y-%m-%d %H:%M:%S"))
    return BUBBLE_WEEKLY_TASK_ID


class BubbleLifecycleTaskMixin:
    """One idempotent Job: claim this week's pills when needed, then hide."""

    @staticmethod
    def _bubble_lifecycle_world_facts_path() -> Path:
        from backend.core.fanxiu.data_annotation.behavior_tree_runtime import (
            _data_annotation_world_facts_path,
        )

        return _data_annotation_world_facts_path()

    def _schedule_bubble_reconcile_after_login(self, *, now: datetime) -> str:
        return schedule_bubble_reconcile_after_login(
            now=now, set_next_time=self._persist_scheduler_task_next_time
        )

    def _reconcile_bubble_after_login(
        self,
        ctx: dict[str, Any],
        stop_event: Any,
        payload: dict[str, Any] | None = None,
    ):
        """Close a recreated bubble inline when this week's gift is settled.

        A VM/app restart can recreate the Android top-level bubble even though
        the weekly claim fact is already complete.  In that common case login
        owns the visible postcondition and hides it before reporting success.
        An unclaimed week remains the single weekly Job's transaction because
        it may need to open SDK pages and make irreversible claims.
        """

        now = job_now()
        fact = read_bubble_lifecycle_fact(self._bubble_lifecycle_world_facts_path())
        if str(fact.get("claimed_week") or "") == bubble_week_key(now):
            hide_payload = dict(payload or {})
            # Android can recreate the SDK overlay a few seconds after the
            # first stable in-game frame.  Login owns this bounded grace
            # window so a late bubble cannot appear immediately after a
            # successful Login Cell and remain forgotten for the whole week.
            hide_payload.setdefault("bubble_appearance_grace_samples", 12)
            hide_payload.setdefault("bubble_appearance_poll_seconds", 1.0)
            hide_result = yield from self._ensure_bubble_hidden(
                ctx, stop_event, hide_payload
            )
            return {"mode": "hidden_inline", "hide": hide_result}

        scheduled_task_id = self._schedule_bubble_reconcile_after_login(now=now)
        return {"mode": "scheduled_weekly", "task_id": scheduled_task_id}

    def _ensure_bubble_visible_for_claim(
        self, runtime: Any, *, payload: dict[str, Any]
    ):
        frame = runtime.cur_frame(update=True)
        overlay_scene = bubble_sdk_overlay_scene(runtime, frame=frame)
        if overlay_scene in {590, 591}:
            return {"result": "menu_open"}
        if overlay_scene == 592:
            raise RuntimeError(
                "气泡_每周丹药：启动时停在未完成的角色选择事务 #592，拒绝摇一摇或穿透点击"
            )
        # The floating image matcher is intentionally global, so it can find a
        # look-alike on an unrelated game page.  Establish the world page
        # before any shake, drag, or click; an already-open SDK transaction is
        # handled above and must not be disturbed by game navigation.
        yield from runtime.go_scene(34)
        frame = runtime.cur_frame(update=True)
        match = runtime.shape_matches(421, "气泡", frame_data_url=frame)
        resolved = (match or {}).get("resolved_box") or (match or {}).get("fixed_box")
        if match is not None:
            if not isinstance(resolved, dict) or not bool(match.get("unique_match", True)):
                raise RuntimeError("气泡_每周丹药：悬浮球未唯一定位，拒绝点击穿透")
            return {"result": "already_visible"}

        repeats = max(1, min(20, int(payload.get("shake_repeats") or 7)))
        interval = max(0.1, min(1.0, float(payload.get("shake_interval_seconds") or 0.22)))
        self._log("action", f"气泡_每周丹药：领取未完成且气泡缺失，连续摇一摇 {repeats} 次恢复")
        shake_mumu_device(
            vmindex=str(payload.get("vmindex") or "1"),
            repeats=repeats,
            interval_seconds=interval,
        )
        settle = max(0.2, min(2.0, float(payload.get("poll_seconds") or 0.75)))
        samples = max(2, min(12, int(payload.get("bubble_restore_samples") or 6)))
        for _sample in range(samples):
            frame = runtime.cur_frame(update=True)
            overlay_scene = bubble_sdk_overlay_scene(runtime, frame=frame)
            if overlay_scene is not None:
                raise RuntimeError(
                    f"气泡_每周丹药：摇一摇验证时出现 SDK 事务 #{overlay_scene}，拒绝继续"
                )
            match = runtime.shape_matches(421, "气泡", frame_data_url=frame)
            resolved = (match or {}).get("resolved_box") or (match or {}).get("fixed_box")
            if match is not None and isinstance(resolved, dict) and bool(match.get("unique_match", True)):
                return {"result": "restored"}
            yield from runtime.wait_action_settle(settle)
        raise RuntimeError("气泡_每周丹药：摇一摇后仍未唯一识别气泡，拒绝猜测点击")

    def _execute_bubble_weekly_task(
        self, ctx: dict[str, Any], stop_event: Any, payload: dict[str, Any] | None = None
    ):
        payload = dict(payload or {})
        facts_path = self._bubble_lifecycle_world_facts_path()
        current_week = bubble_week_key(job_now())
        fact = read_bubble_lifecycle_fact(facts_path)
        claim_result: dict[str, Any] | None = None

        if str(fact.get("claimed_week") or "") != current_week:
            asset_tree_path = ctx.get("asset_tree_path")
            if not isinstance(asset_tree_path, Path):
                raise RuntimeError("气泡_每周丹药：缺少资产树路径")
            runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
            yield from self._ensure_bubble_visible_for_claim(runtime, payload=payload)
            claim_result = yield from self._execute_bubble_claim_pills_task(ctx, stop_event, payload)
            completed_fact = read_bubble_lifecycle_fact(facts_path)
            if str(completed_fact.get("claimed_week") or "") != bubble_week_key(job_now()):
                raise RuntimeError("气泡_每周丹药：领取阶段返回但本周完成事实未落盘")

        hide_result = yield from self._ensure_bubble_hidden(ctx, stop_event, payload)
        completed_at = job_now()
        next_time = next_bubble_weekly_time(completed_at)
        task_id = str(payload.get("__scheduler_task_id") or BUBBLE_WEEKLY_TASK_ID)
        self._persist_scheduler_task_next_time(task_id, next_time)
        message = f"气泡_每周丹药：本周丹药已确认领取，气泡已隐藏；下次 {next_time}"
        self._log("success", message)
        return {
            "result": "success", "message": message,
            "week": bubble_week_key(completed_at),
            "claimed_this_run": claim_result is not None,
            "claim": claim_result, "hide": hide_result, "next_time": next_time,
        }
