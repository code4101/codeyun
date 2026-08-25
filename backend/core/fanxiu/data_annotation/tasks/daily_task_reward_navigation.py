from __future__ import annotations

"""Navigation-only routes for the daily task-reward aggregate job.

The helpers in this module stop at an activity cover scene.  They deliberately
do not call the corresponding seat/challenge Job because those Jobs own
irreversible business actions that are unrelated to claiming task rewards.
"""

from dataclasses import dataclass
import threading
from typing import Any, Mapping


_LINGMAI_ENTRY_PATTERN = (
    r"参\s*与?.*灵\s*脉.*(?:争|夺).*?(?:1|一)?.*?(?:小\s*时|时)?|"
    r"灵\s*脉.*(?:争|夺)|灵\s*脉"
)


@dataclass(frozen=True)
class DailyTaskRewardNavigationSpec:
    domain: str
    title_pattern: str
    target_scene_id: int
    landing_scene_ids: tuple[int, ...]
    initial_checks: int = 1


DAILY_TASK_REWARD_NAVIGATION_SPECS: Mapping[str, DailyTaskRewardNavigationSpec] = {
    "lundao": DailyTaskRewardNavigationSpec(
        domain="lundao",
        title_pattern=r"论道",
        target_scene_id=296,
        # #304 means the player is already seated.  Its labelled Return only
        # exposes the dojo cover and does not leave/change the seat.  #391 is
        # the independently identified kicked notice.
        # #549 is the 22:00-10:00 closed cover.  It exposes the same task
        # entry as #296 and is therefore a valid reward-only landing; do not
        # force it through the seat/challenge flow merely to normalize a
        # cosmetic day/night cover difference.
        landing_scene_ids=(296, 549, 304, 391),
    ),
    "qixi_mojie": DailyTaskRewardNavigationSpec(
        domain="qixi_mojie",
        title_pattern=r"参与.{0,4}奇|奇.{0,4}魔|魔界",
        target_scene_id=319,
        # #330 is the known pre-cover reward confirmation.
        landing_scene_ids=(319, 330),
    ),
    "lingmai": DailyTaskRewardNavigationSpec(
        domain="lingmai",
        title_pattern=_LINGMAI_ENTRY_PATTERN,
        target_scene_id=285,
        # #312 is the known entry confirmation before the Zaohua cover.
        landing_scene_ids=(285, 312),
        initial_checks=10,
    ),
}


def _scene_id(value: Any) -> int | None:
    if hasattr(value, "id"):
        value = getattr(value, "id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _finish_lundao_cover(runtime: Any, scene_id: int) -> Any:
    """Normalize only known entry blockers to a day/night Lundao cover."""

    if scene_id == 391:
        waited = yield from runtime.wait_click_then_view(
            391,
            "确认",
            [296, 549, 304],
            settle_seconds=1.5,
            timeout=20.0,
        )
        scene_id = _scene_id(waited)
    if scene_id == 304:
        waited = yield from runtime.wait_click_then_view(
            304,
            "返回",
            [296, 549, 34, 69],
            settle_seconds=1.5,
            timeout=15.0,
        )
        scene_id = _scene_id(waited)
    if scene_id not in {296, 549}:
        raise RuntimeError(
            "日常_任务奖励/论道：安全导航未到达 #296/#549；"
            f"当前 #{scene_id if scene_id is not None else 'unknown'}，未执行座位业务"
        )
    return scene_id


def _finish_qixi_cover(runtime: Any, scene_id: int) -> Any:
    if scene_id == 330:
        waited = yield from runtime.wait_click_then_view(
            330,
            "确定",
            [319],
            settle_seconds=1.5,
            timeout=20.0,
        )
        scene_id = _scene_id(waited)
    if scene_id != 319:
        raise RuntimeError(
            "日常_任务奖励/奇袭魔界：安全导航未到达 #319；"
            f"当前 #{scene_id if scene_id is not None else 'unknown'}，未执行挑战业务"
        )
    return 319


def _finish_lingmai_cover(runtime: Any, scene_id: int) -> Any:
    if scene_id == 312:
        # Do not include the source popup in the post-click candidate set: a
        # still-rendered animation frame must not be mistaken for completion.
        yield from runtime.wait_click(312, "确认")
        waited = yield from runtime.wait_scene(
            285,
            timeout=8.0,
            label="日常_任务奖励/灵脉：确认入口弹窗后等待 #285",
        )
        scene_id = _scene_id(waited)
    if scene_id != 285:
        raise RuntimeError(
            "日常_任务奖励/灵脉：安全导航未到达 #285；"
            f"当前 #{scene_id if scene_id is not None else 'unknown'}，未执行聚灵/抢位业务"
        )
    return 285


def navigate_to_daily_task_reward_cover(
    owner: Any,
    ctx: dict[str, Any],
    stop_event: threading.Event,
    payload: dict[str, Any],
    runtime: Any,
    domain: str,
) -> Any:
    """Navigate from #34/#69 to one reward-bearing activity cover.

    ``owner`` supplies the already-tested world-to-daily and dynamic daily-row
    primitives.  The function contains no Scheduler writes and never invokes a
    complete activity Job.
    """

    try:
        spec = DAILY_TASK_REWARD_NAVIGATION_SPECS[domain]
    except KeyError as exc:
        raise ValueError(f"未知日常任务奖励域：{domain}") from exc

    candidates = [spec.target_scene_id, *spec.landing_scene_ids, 69, 34]
    scene_id, _score, frame = runtime.current_scene(candidates, update=True)
    scene_id = _scene_id(scene_id)
    text = runtime.ocr_text(frame)

    # A retry may already be on the target or one of its known entry blockers.
    # Handle those before touching the shared daily list.
    if scene_id not in spec.landing_scene_ids:
        if scene_id != 69:
            scene_id = yield from owner._enter_daily_from_world_like(
                ctx,
                runtime,
                stop_event,
                frame,
                scene_id,
                text,
                label=f"日常_任务奖励/{domain}",
            )
        if scene_id != 69:
            raise RuntimeError(
                f"日常_任务奖励/{domain}：未确认进入 #69，禁止查找动态任务条目"
            )
        status = yield from owner._open_daily_entry_from_daily(
            ctx,
            stop_event,
            payload,
            task_label=f"日常_任务奖励/{domain}",
            title_pattern=spec.title_pattern,
            progress_can_mark_done=False,
            initial_checks=spec.initial_checks,
        )
        if status != "open":
            raise RuntimeError(
                f"日常_任务奖励/{domain}：#69 动态入口未打开，status={status!r}"
            )
        waited = yield from runtime.wait_scene(
            *spec.landing_scene_ids,
            timeout=25.0,
            label=f"日常_任务奖励/{domain}：等待活动封面",
        )
        scene_id = _scene_id(waited)

    if domain == "lundao":
        target = yield from _finish_lundao_cover(runtime, scene_id)
    elif domain == "qixi_mojie":
        target = yield from _finish_qixi_cover(runtime, scene_id)
    else:
        target = yield from _finish_lingmai_cover(runtime, scene_id)
    return {
        "ok": True,
        "domain": domain,
        "scene_id": target,
        "status": "cover_ready",
    }


__all__ = [
    "DAILY_TASK_REWARD_NAVIGATION_SPECS",
    "DailyTaskRewardNavigationSpec",
    "navigate_to_daily_task_reward_cover",
]
