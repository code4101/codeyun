from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Literal


KUNLUN_MAIN_SCENE_ID = 540
KUNLUN_OPTIONAL_REWARD_SCENE_ID = 541
KUNLUN_STORE_SCENE_ID = 542
KUNLUN_TASK_SCENE_ID = 543
KUNLUN_PAGE_WAIT_TIMEOUT_SECONDS = 30.0
KUNLUN_KNOWN_SCENE_IDS = (
    KUNLUN_MAIN_SCENE_ID,
    KUNLUN_OPTIONAL_REWARD_SCENE_ID,
    KUNLUN_STORE_SCENE_ID,
    KUNLUN_TASK_SCENE_ID,
)
KunlunTab = Literal["昆仑秘藏", "任务", "商店"]


class KunlunActivityUnavailable(RuntimeError):
    """The stable world menu did not expose Kunlun Secret in time."""


@dataclass(frozen=True)
class KunlunPageResult:
    page: str
    scene_id: int | None
    score: float
    ocr_text: str


def _page_from_observation(
    scene_id: int | None, score: float, text: str
) -> KunlunPageResult | None:
    reliable = int(scene_id or 0) if float(score or 0) >= 80.0 else 0
    pages = {
        KUNLUN_OPTIONAL_REWARD_SCENE_ID: "自选",
        KUNLUN_STORE_SCENE_ID: "商店",
        KUNLUN_TASK_SCENE_ID: "任务",
    }
    if reliable in pages:
        return KunlunPageResult(pages[reliable], scene_id, float(score), text)
    # The world page also contains the activity-menu text “昆仑秘藏”.  OCR is
    # therefore only an availability/click target in enter_kunlun(), never a
    # business-page identity.  Page identity must come from a reliable scene.
    if reliable == KUNLUN_MAIN_SCENE_ID:
        return KunlunPageResult("昆仑秘藏", scene_id, float(score), text)
    return None


def read_kunlun_page(runtime: Any, *, update: bool = True) -> KunlunPageResult | None:
    scene_id, score, frame = runtime.current_scene(
        list(KUNLUN_KNOWN_SCENE_IDS), update=bool(update)
    )
    return _page_from_observation(scene_id, float(score or 0), runtime.ocr_text(frame))


def _wait_kunlun_page(
    runtime: Any, page: str, *, timeout_seconds: float, poll_seconds: float
) -> KunlunPageResult:
    deadline = time.monotonic() + max(0.5, float(timeout_seconds))
    result = None
    while True:
        result = read_kunlun_page(runtime, update=True)
        if result is not None and result.page == page:
            return result
        if time.monotonic() >= deadline:
            observed = result.page if result is not None else "unknown"
            raise RuntimeError(f"等待昆仑秘藏页面「{page}」超时，当前={observed}")
        time.sleep(max(0.05, float(poll_seconds)))


def enter_kunlun(
    runtime: Any,
    *,
    source_scene_id: int = 34,
    timeout_seconds: float = KUNLUN_PAGE_WAIT_TIMEOUT_SECONDS,
    poll_seconds: float = 0.25,
    availability_timeout_seconds: float = 60.0,
    availability_poll_seconds: float = 1.0,
) -> KunlunPageResult:
    current = read_kunlun_page(runtime, update=True)
    if current is not None:
        # A failed/interrupted configuration may legitimately leave #541 open.
        # Let the idempotent workflow continue from the currently visible form
        # instead of trying to
        # click a background page tab through the modal.
        return current

    deadline = time.monotonic() + max(0.5, float(availability_timeout_seconds))
    while True:
        scene_id, score, frame = runtime.current_scene([int(source_scene_id)], update=True)
        if int(scene_id or 0) != int(source_scene_id) or float(score or 0) < 90.0:
            raise RuntimeError(
                f"进入昆仑秘藏要求从可靠 #{source_scene_id} 开始："
                f"scene={scene_id}, score={float(score or 0):.1f}"
            )
        if "昆仑秘藏" in re.sub(r"\s+", "", runtime.ocr_text(frame)):
            break
        if time.monotonic() >= deadline:
            raise KunlunActivityUnavailable(
                f"可靠 #{source_scene_id} 菜单连续 {availability_timeout_seconds:.0f} 秒"
                "未识别到昆仑秘藏"
            )
        time.sleep(max(0.05, float(availability_poll_seconds)))
    runtime.click_ocr_text(
        int(source_scene_id),
        "昆仑秘藏",
        frame_data_url=frame,
        match_mode="fuzzy",
        min_similarity=70.0,
        ambiguity_margin=5.0,
    )
    return _wait_kunlun_page(
        runtime, "昆仑秘藏", timeout_seconds=timeout_seconds, poll_seconds=poll_seconds
    )


def open_kunlun_tab(
    runtime: Any,
    tab: KunlunTab,
    *,
    timeout_seconds: float = KUNLUN_PAGE_WAIT_TIMEOUT_SECONDS,
    poll_seconds: float = 0.25,
) -> KunlunPageResult:
    target = str(tab or "").strip()
    if target not in {"昆仑秘藏", "任务", "商店"}:
        raise ValueError(f"尚未实现的昆仑秘藏页签：{tab!r}")
    current = read_kunlun_page(runtime, update=True)
    if current is None:
        raise RuntimeError("当前不在可靠的昆仑秘藏系列页面，拒绝切换页签")
    if current.page == target:
        return current
    frame = runtime.cur_frame(update=True)
    click_scene_id = current.scene_id or KUNLUN_MAIN_SCENE_ID
    click_shape_title = target
    if target == "昆仑秘藏" and current.scene_id == KUNLUN_TASK_SCENE_ID:
        click_shape_title = "kunlun昆仑秘藏"
    runtime.click_shape(int(click_scene_id), click_shape_title, frame_data_url=frame)
    return _wait_kunlun_page(
        runtime, target, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds
    )


def open_kunlun_optional_reward(
    runtime: Any,
    *,
    timeout_seconds: float = KUNLUN_PAGE_WAIT_TIMEOUT_SECONDS,
    poll_seconds: float = 0.25,
) -> KunlunPageResult:
    open_kunlun_tab(runtime, "昆仑秘藏", timeout_seconds=timeout_seconds)
    frame = runtime.cur_frame(update=True)
    runtime.click_shape(
        KUNLUN_MAIN_SCENE_ID,
        "自选未配置入口",
        frame_data_url=frame,
    )
    return _wait_kunlun_page(
        runtime, "自选", timeout_seconds=timeout_seconds, poll_seconds=poll_seconds
    )


def leave_kunlun(
    runtime: Any,
    *,
    timeout_seconds: float = KUNLUN_PAGE_WAIT_TIMEOUT_SECONDS,
    poll_seconds: float = 0.25,
) -> tuple[int, float]:
    open_kunlun_tab(runtime, "昆仑秘藏", timeout_seconds=timeout_seconds)
    frame = runtime.cur_frame(update=True)
    runtime.click_shape(KUNLUN_MAIN_SCENE_ID, "返回", frame_data_url=frame)
    deadline = time.monotonic() + max(0.5, float(timeout_seconds))
    while True:
        scene_id, score, _ = runtime.current_scene([34, KUNLUN_MAIN_SCENE_ID], update=True)
        if int(scene_id or 0) == 34 and float(score or 0) >= 90.0:
            return 34, float(score)
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"#540[返回] 后未可靠回到 #34：scene={scene_id}, score={score:.1f}"
            )
        time.sleep(max(0.05, float(poll_seconds)))
