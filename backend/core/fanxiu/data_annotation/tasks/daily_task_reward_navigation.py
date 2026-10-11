from __future__ import annotations

"""Navigation-only routes for the daily task-reward aggregate job.

The helpers in this module stop at an activity cover scene.  They deliberately
do not call the corresponding seat/challenge Job because those Jobs own
irreversible business actions that are unrelated to claiming task rewards.
"""

from dataclasses import dataclass
import threading
from typing import Any, Mapping
from backend.core.fanxiu.behavior_tree.errors import SceneClickMismatch


def daily_task_reward_cover_rebind(domain: str, *, source_scene_id: int,
                                    expected_scene_id: int, actual_scene_id: int,
                                    rebind_count: int) -> int | None:
    """One no-input re-observation of the declared Lundao day/night pair.

    This is not an action retry: SceneClickMismatch proves the first mandatory
    pre-click guard rejected input. Unknown/popup/other-domain scenes and a
    second flip remain failures. The next attempt keeps all fresh click gates.
    """
    if (domain == 'lundao' and rebind_count == 0
            and source_scene_id == expected_scene_id
            and {expected_scene_id, actual_scene_id} == {296, 549}):
        return actual_scene_id
    return None


def open_daily_task_reward_task_panel(context: Any, domain: str, *, cover_scene_id: int,
                                      entry_shape: str, task_scene_id: int, on_rebind=None):
    """Open one task panel without bypassing the scene/Shape click transaction."""
    if domain != 'lundao':
        return (yield from context.wait_click_then_scene(cover_scene_id, entry_shape,
            [task_scene_id], settle_seconds=1.2, timeout=25.0))
    source = cover_scene_id
    for rebind_count in range(2):
        try:
            # With one submitted click per call, a caught mismatch cannot be a
            # failed second click after an earlier input. A post-click timeout
            # propagates directly and never enters this rebinding path.
            return (yield from context.wait_click_then_scene(source, entry_shape,
                [task_scene_id], settle_seconds=1.2, timeout=25.0,
                max_clicks=1, retry_if_source_remains=False))
        except SceneClickMismatch as exc:
            next_source = daily_task_reward_cover_rebind(domain, source_scene_id=source,
                expected_scene_id=exc.expected_scene_id, actual_scene_id=exc.actual_scene_id,
                rebind_count=rebind_count)
            if next_source is None:
                raise
            if on_rebind is not None:
                on_rebind({'source_scene_id': source, 'actual_scene_id': next_source,
                           'input_submitted': False, 'rebind_count': rebind_count + 1})
            source = next_source


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


def _finish_lundao_cover(context: Any, scene_id: int) -> Any:
    """Normalize only known entry blockers to a day/night Lundao cover."""

    if scene_id == 391:
        waited = yield from context.wait_click_then_scene(
            391,
            "确认",
            [296, 549, 304],
            settle_seconds=1.5,
            timeout=20.0,
        )
        scene_id = _scene_id(waited)
    if scene_id == 304:
        waited = yield from context.wait_click_then_scene(
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


def _finish_qixi_cover(context: Any, scene_id: int) -> Any:
    if scene_id == 330:
        waited = yield from context.wait_click_then_scene(
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


def _finish_lingmai_cover(context: Any, scene_id: int) -> Any:
    if scene_id == 312:
        # Do not include the source popup in the post-click candidate set: a
        # still-rendered animation frame must not be mistaken for completion.
        yield from context.wait_click(312, "确认")
        waited = yield from context.wait_scene(
            [285],
            wait=8.0,
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
    context: Any,
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
    _wait_scene_match = yield from context.wait_scene(candidates, wait=5.0, required=False)
    (scene_id, _score, frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    scene_id = _scene_id(scene_id)
    text = context.ocr_text(frame)

    # A retry may already be on the target or one of its known entry blockers.
    # Handle those before touching the shared daily list.
    if scene_id not in spec.landing_scene_ids:
        if scene_id != 69:
            scene_id = yield from owner._enter_daily_from_world_like(
                ctx,
                context,
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
        waited = yield from context.wait_scene(
            spec.landing_scene_ids,
            wait=25.0,
            label=f"日常_任务奖励/{domain}：等待活动封面",
        )
        scene_id = _scene_id(waited)

    if domain == "lundao":
        target = yield from _finish_lundao_cover(context, scene_id)
    elif domain == "qixi_mojie":
        target = yield from _finish_qixi_cover(context, scene_id)
    else:
        target = yield from _finish_lingmai_cover(context, scene_id)
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
