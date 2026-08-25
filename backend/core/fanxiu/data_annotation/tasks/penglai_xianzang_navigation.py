from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Literal


XIANZANG_MAIN_SCENE_ID = 447
XIANZANG_OPTIONAL_REWARD_SCENE_ID = 448
XIANZANG_STORE_SCENE_ID = 449
XIANZANG_TASK_SCENE_ID = 450
XIANZANG_KNOWN_SCENE_IDS = (
    XIANZANG_MAIN_SCENE_ID,
    XIANZANG_OPTIONAL_REWARD_SCENE_ID,
    XIANZANG_STORE_SCENE_ID,
    XIANZANG_TASK_SCENE_ID,
)
XianzangTab = Literal["蓬莱仙藏", "任务", "商店"]


class XianzangActivityUnavailable(RuntimeError):
    """The stable world menu did not expose this week's activity in time."""


@dataclass(frozen=True)
class XianzangPageResult:
    page: str
    scene_id: int | None
    score: float
    ocr_text: str


def is_xianzang_main_page_text(text: str) -> bool:
    """Recognize the current unnumbered main page after a popup closes."""

    normalized = re.sub(r"\s+", "", str(text or ""))
    return (
        "蓬莱仙藏" in normalized
        and "鉴宝" in normalized
        and "自选奖励" not in normalized
        and "礼包商店" not in normalized
        and "炼宝试炼" not in normalized
    )


def _page_from_observation(
    scene_id: int | None,
    score: float,
    text: str,
) -> XianzangPageResult | None:
    reliable_scene = int(scene_id or 0) if float(score or 0) >= 80.0 else 0
    normalized = re.sub(r"\s+", "", str(text or ""))
    # #447 and #450 share the same title-only scene identity and can both score
    # 100 on the task page.  Resolve specific business content before the
    # shared shell/main scene id, and return the logical page scene so callers
    # never execute a #447 action against an already-open #450 page.
    if "自选奖励" in normalized:
        return XianzangPageResult(
            "自选", XIANZANG_OPTIONAL_REWARD_SCENE_ID, float(score), text
        )
    if "礼包商店" in normalized:
        return XianzangPageResult(
            "商店", XIANZANG_STORE_SCENE_ID, float(score), text
        )
    if "炼宝试炼" in normalized or (
        "登录游戏" in normalized and "已完成" in normalized
    ):
        return XianzangPageResult(
            "任务", XIANZANG_TASK_SCENE_ID, float(score), text
        )
    if reliable_scene == XIANZANG_OPTIONAL_REWARD_SCENE_ID:
        return XianzangPageResult("自选", scene_id, float(score), text)
    if reliable_scene == XIANZANG_STORE_SCENE_ID:
        return XianzangPageResult("商店", scene_id, float(score), text)
    if reliable_scene == XIANZANG_TASK_SCENE_ID:
        return XianzangPageResult("任务", scene_id, float(score), text)
    if reliable_scene == XIANZANG_MAIN_SCENE_ID or is_xianzang_main_page_text(text):
        return XianzangPageResult("蓬莱仙藏", scene_id, float(score), text)
    return None


def read_xianzang_page(runtime: Any, *, update: bool = True) -> XianzangPageResult | None:
    scene_id, score, frame = runtime.current_scene(
        list(XIANZANG_KNOWN_SCENE_IDS),
        update=bool(update),
    )
    return _page_from_observation(scene_id, float(score or 0), runtime.ocr_text(frame))


def _wait_xianzang_page(
    runtime: Any,
    page: str,
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> XianzangPageResult:
    deadline = time.monotonic() + max(0.5, float(timeout_seconds))
    while True:
        result = read_xianzang_page(runtime, update=True)
        if result is not None and result.page == page:
            return result
        if time.monotonic() >= deadline:
            observed = result.page if result is not None else "unknown"
            raise RuntimeError(
                f"等待蓬莱仙藏页面「{page}」超时，当前={observed}"
            )
        time.sleep(max(0.05, float(poll_seconds)))


def enter_xianzang(
    runtime: Any,
    *,
    source_scene_id: int = 34,
    timeout_seconds: float = 8.0,
    poll_seconds: float = 0.25,
    availability_timeout_seconds: float = 60.0,
    availability_poll_seconds: float = 1.0,
) -> XianzangPageResult:
    """Enter the Xianzang main page from #34 and verify fresh business text."""

    current = read_xianzang_page(runtime, update=True)
    if current is not None:
        if current.page == "蓬莱仙藏":
            return current
        return open_xianzang_tab(
            runtime,
            "蓬莱仙藏",
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
        )

    availability_deadline = time.monotonic() + max(
        0.5, float(availability_timeout_seconds)
    )
    while True:
        scene_id, score, frame = runtime.current_scene([int(source_scene_id)], update=True)
        if int(scene_id or 0) != int(source_scene_id) or float(score or 0) < 90.0:
            raise RuntimeError(
                f"进入蓬莱仙藏要求从可靠 #{source_scene_id} 开始："
                f"scene={scene_id}, score={float(score or 0):.1f}"
            )
        if "蓬莱仙藏" in re.sub(r"\s+", "", runtime.ocr_text(frame)):
            break
        if time.monotonic() >= availability_deadline:
            raise XianzangActivityUnavailable(
                f"可靠 #{source_scene_id} 菜单连续 "
                f"{float(availability_timeout_seconds):.0f} 秒未识别到蓬莱仙藏"
            )
        time.sleep(max(0.05, float(availability_poll_seconds)))
    runtime.click_ocr_text(
        int(source_scene_id),
        "蓬莱仙藏",
        frame_data_url=frame,
        match_mode="fuzzy",
        min_similarity=70.0,
        ambiguity_margin=5.0,
    )
    return _wait_xianzang_page(
        runtime,
        "蓬莱仙藏",
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )


def open_xianzang_tab(
    runtime: Any,
    tab: XianzangTab,
    *,
    timeout_seconds: float = 8.0,
    poll_seconds: float = 0.25,
    retry_click_seconds: float = 1.0,
    max_clicks: int = 3,
) -> XianzangPageResult:
    """Open one implemented tab with bounded, freshly verified retries.

    A freshly opened activity can already satisfy the visual #447 contract
    while its bottom navigation is still swallowing the first click.  A tab
    click is reversible, so retry it only while a fresh observation still
    proves that we remain on another known Xianzang page.  Unknown frames are
    waited through and never clicked.
    """

    target = str(tab or "").strip()
    if target not in {"蓬莱仙藏", "任务", "商店"}:
        raise ValueError(f"尚未实现的蓬莱仙藏页签：{tab!r}")
    current = read_xianzang_page(runtime, update=True)
    if current is None:
        raise RuntimeError("当前不在可靠的蓬莱仙藏系列页面，拒绝切换页签")
    if current.page == target:
        return current

    settle_window = max(0.5, float(timeout_seconds))
    deadline = time.monotonic() + settle_window
    next_click_at = 0.0
    click_count = 0
    observed: XianzangPageResult | None = current
    while True:
        now = time.monotonic()
        if observed is not None and observed.page == target:
            return observed
        if (
            observed is not None
            and now >= next_click_at
            and click_count < max(1, int(max_clicks))
        ):
            frame = runtime.cur_frame(update=True)
            click_scene_id = XIANZANG_MAIN_SCENE_ID
            click_shape_title = target
            if target == "蓬莱仙藏" and observed.scene_id == XIANZANG_TASK_SCENE_ID:
                # #450 also uses "蓬莱仙藏" as its scene-identity title.  Its
                # bottom navigation item is disambiguated in the asset tree.
                click_scene_id = XIANZANG_TASK_SCENE_ID
                click_shape_title = "pengla蓬莱仙藏"
            elif target == "蓬莱仙藏" and observed.scene_id == XIANZANG_STORE_SCENE_ID:
                click_scene_id = XIANZANG_STORE_SCENE_ID
            runtime.click_shape(
                click_scene_id,
                click_shape_title,
                frame_data_url=frame,
            )
            click_count += 1
            next_click_at = now + max(0.25, float(retry_click_seconds))
            # Runtime scene recognition can itself take several seconds.  A
            # retry must receive a complete post-click observation window;
            # never click and then fail against the previous attempt's wall
            # clock deadline.
            deadline = now + settle_window
        if now >= deadline:
            current_page = observed.page if observed is not None else "unknown"
            raise RuntimeError(
                f"等待蓬莱仙藏页面「{target}」超时，当前={current_page}"
            )
        time.sleep(max(0.05, float(poll_seconds)))
        observed = read_xianzang_page(runtime, update=True)


def open_xianzang_optional_reward(
    runtime: Any,
    *,
    timeout_seconds: float = 8.0,
    poll_seconds: float = 0.25,
) -> XianzangPageResult:
    """Open #448 only after first aligning the Xianzang main page."""

    current = open_xianzang_tab(
        runtime,
        "蓬莱仙藏",
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    frame = runtime.cur_frame(update=True)
    runtime.click_shape(
        XIANZANG_MAIN_SCENE_ID,
        "自选未配置入口",
        frame_data_url=frame,
    )
    return _wait_xianzang_page(
        runtime,
        "自选",
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )


def leave_xianzang(
    runtime: Any,
    *,
    timeout_seconds: float = 8.0,
    poll_seconds: float = 0.25,
) -> tuple[int, float]:
    """Close #447 through its explicit return shape and verify world #34."""

    open_xianzang_tab(
        runtime,
        "蓬莱仙藏",
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    frame = runtime.cur_frame(update=True)
    runtime.click_shape(XIANZANG_MAIN_SCENE_ID, "返回", frame_data_url=frame)
    deadline = time.monotonic() + max(0.5, float(timeout_seconds))
    last_scene: int | None = None
    last_score = 0.0
    while True:
        last_scene, last_score, _frame = runtime.current_scene(
            [34, XIANZANG_MAIN_SCENE_ID],
            update=True,
        )
        if int(last_scene or 0) == 34 and float(last_score or 0) >= 90.0:
            return 34, float(last_score)
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"#447[返回] 后未可靠回到 #34：scene={last_scene}, "
                f"score={float(last_score or 0):.1f}"
            )
        time.sleep(max(0.05, float(poll_seconds)))
