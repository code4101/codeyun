from __future__ import annotations

"""Claim Beast Abyss task rewards through its verified GUI contract.

The Beast Abyss page is deliberately adapted here instead of being presented
as a universal task-reward layout. Claims are safe idempotent clicks: a list
advance is observed through the unobstructed third-row title, and three
consecutive no-change cycles close one tab.
"""

from collections.abc import Generator, Iterable, Mapping
from dataclasses import dataclass
import re
from typing import Any


BEAST_ABYSS_EXPLORE_SCENE_ID = 657
BEAST_ABYSS_TASK_SCENE_ID = 664
BEAST_ABYSS_HOME_SCENE_ID = 535


@dataclass(frozen=True)
class BeastAbyssTaskRewardAssets:
    """UI contract owned by the Beast Abyss task-reward adapter."""

    home_scene_id: int = BEAST_ABYSS_HOME_SCENE_ID
    explore_scene_id: int = BEAST_ABYSS_EXPLORE_SCENE_ID
    task_scene_id: int = BEAST_ABYSS_TASK_SCENE_ID
    task_entry_shape: str = "任务"
    tab_region_shape: str = "奖励tab"
    first_row_shape: str = "首条任务进度区"
    observer_shape: str = "第三行任务标题"
    home_tab_shape: str = "兽渊探秘页签"


@dataclass(frozen=True)
class BeastAbyssRewardTab:
    title: str
    center_x: float
    center_y: float


DEFAULT_BEAST_ABYSS_TASK_REWARD_ASSETS = BeastAbyssTaskRewardAssets()


def _view_id(value: Any) -> int | None:
    value = getattr(value, "id", value)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _normalized_ocr_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def discover_beast_abyss_reward_tabs(
    lines: Iterable[Mapping[str, Any]],
    *,
    minimum_score: float = 0.80,
) -> tuple[BeastAbyssRewardTab, ...]:
    """Extract current Beast Abyss tabs from one bounded OCR scan."""

    tabs: list[BeastAbyssRewardTab] = []
    seen: set[str] = set()
    for raw in lines:
        title = _normalized_ocr_text(raw.get("text"))
        score = float(raw.get("score") or 0.0)
        if not title or title == "领" or score < float(minimum_score):
            continue
        try:
            x = float(raw.get("x"))
            y = float(raw.get("y"))
            w = float(raw.get("w"))
            h = float(raw.get("h"))
        except (TypeError, ValueError):
            continue
        if w <= 0 or h <= 0 or title in seen:
            continue
        seen.add(title)
        tabs.append(BeastAbyssRewardTab(title, x + w / 2, y + h / 2))
    return tuple(sorted(tabs, key=lambda item: item.center_x))


def _read_third_row_title(
    context: Any,
    assets: BeastAbyssTaskRewardAssets,
) -> str:
    frame = context.cur_frame(update=True)
    text = context.ocr_text_in_shapes(
        assets.task_scene_id,
        (assets.observer_shape,),
        padding=0,
        frame_data_url=frame,
        crop=True,
    )
    return _normalized_ocr_text(text)


def _wait_read_third_row_title(
    context: Any,
    assets: BeastAbyssTaskRewardAssets,
    *,
    attempts: int = 3,
) -> Generator[Any, None, str]:
    """Read the verified observer ROI, retrying only transport-level misses."""

    for attempt in range(max(1, int(attempts))):
        title = _read_third_row_title(context, assets)
        if title:
            return title
        if attempt + 1 < attempts:
            yield from context.wait_action_settle(0.4)
    raise RuntimeError("兽渊任务奖励：第3行任务标题连续 OCR 为空")


def _open_beast_abyss_task_page(
    context: Any,
    assets: BeastAbyssTaskRewardAssets,
) -> Generator[Any, None, None]:
    _wait_scene_match = yield from context.wait_scene((assets.home_scene_id, assets.explore_scene_id, assets.task_scene_id), wait=5.0, required=False)
    (scene_id, _score, _frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if int(scene_id or 0) == assets.explore_scene_id:
        landed = yield from context.wait_click_then_scene(
            assets.explore_scene_id,
            "返回",
            (assets.home_scene_id,),
            timeout=20.0,
            label="兽渊任务奖励：探查页返回封面",
        )
        scene_id = _view_id(landed)
    if int(scene_id or 0) == assets.home_scene_id:
        landed = yield from context.wait_click_then_scene(
            assets.home_scene_id,
            assets.task_entry_shape,
            (assets.task_scene_id,),
            timeout=20.0,
            label="兽渊任务奖励：进入任务页",
        )
        scene_id = _view_id(landed)
    if int(scene_id or 0) != assets.task_scene_id:
        raise RuntimeError(
            "兽渊任务奖励要求从封面、探查页或任务页开始："
            f"scene={scene_id}"
        )


def _return_to_beast_abyss_home(
    context: Any,
    assets: BeastAbyssTaskRewardAssets,
) -> Generator[Any, None, None]:
    landed = yield from context.wait_click_then_scene(
        assets.task_scene_id,
        assets.home_tab_shape,
        (assets.home_scene_id,),
        timeout=20.0,
        label="兽渊任务奖励：返回活动封面",
    )
    if _view_id(landed) != assets.home_scene_id:
        raise RuntimeError("兽渊任务奖励：未返回活动封面")


def claim_beast_abyss_task_rewards(
    context: Any,
    *,
    assets: BeastAbyssTaskRewardAssets = DEFAULT_BEAST_ABYSS_TASK_REWARD_ASSETS,
    click_settle_seconds: float = 3.0,
    no_change_confirmations: int = 3,
    max_clicks_per_tab: int = 30,
) -> Generator[Any, None, dict[str, Any]]:
    """Claim every visible Beast Abyss reward tab without Runtime task IDs.

    One first-row click is harmless when no reward is available. A changed
    third-row title proves that the list advanced; three consecutive unchanged
    observations close the current tab. This intentionally favors reward
    recall over avoiding redundant clicks.
    """

    confirmations = max(1, int(no_change_confirmations))
    click_limit = max(confirmations, int(max_clicks_per_tab))
    yield from _open_beast_abyss_task_page(context, assets)

    _wait_scene_match = yield from context.wait_scene((assets.task_scene_id,), wait=5.0, required=False)
    (scene_id, score, frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if int(scene_id or 0) != assets.task_scene_id or float(score or 0.0) < 80.0:
        raise RuntimeError("兽渊任务奖励：任务页场景身份无效")
    lines = context.ocr_lines_in_shapes(
        assets.task_scene_id,
        (assets.tab_region_shape,),
        padding=0,
        frame_data_url=frame,
    )
    tabs = discover_beast_abyss_reward_tabs(lines)
    if not tabs:
        raise RuntimeError("兽渊任务奖励：奖励tab区域没有识别到任何 Tab")

    results: list[dict[str, Any]] = []
    for tab in tabs:
        context.click_frame_point(
            assets.task_scene_id,
            tab.center_x,
            tab.center_y,
        )
        yield from context.wait_action_settle(0.8)
        title = yield from _wait_read_third_row_title(context, assets)
        unchanged = 0
        advances = 0
        clicks = 0
        while unchanged < confirmations:
            if clicks >= click_limit:
                raise RuntimeError(
                    f"兽渊任务奖励：Tab「{tab.title}」在 {click_limit} 次点击内未收敛"
                )
            context.click_shape_center(
                assets.task_scene_id,
                assets.first_row_shape,
            )
            clicks += 1
            yield from context.wait_action_settle(click_settle_seconds)
            next_title = yield from _wait_read_third_row_title(context, assets)
            if next_title != title:
                title = next_title
                unchanged = 0
                advances += 1
            else:
                unchanged += 1
        results.append(
            {
                "tab": tab.title,
                "clicks": clicks,
                "detected_advances": advances,
                "unchanged_confirmations": unchanged,
                "final_observer": title,
            }
        )

    yield from _return_to_beast_abyss_home(context, assets)
    return {
        "checked": True,
        "gui_opened": True,
        "tabs": [item.title for item in tabs],
        "tab_results": results,
        "detected_advances": sum(item["detected_advances"] for item in results),
        "remaining_claimable": [],
    }


__all__ = [
    "BeastAbyssTaskRewardAssets",
    "DEFAULT_BEAST_ABYSS_TASK_REWARD_ASSETS",
    "claim_beast_abyss_task_rewards",
    "discover_beast_abyss_reward_tabs",
]
