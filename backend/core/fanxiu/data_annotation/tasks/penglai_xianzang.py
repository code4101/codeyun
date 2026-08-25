from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
from pyxllib.autogui import ActionPlanner

from backend.core.fanxiu.prayer_cycle import current_prayer_cycle
from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation import (
    XIANZANG_OPTIONAL_REWARD_SCENE_ID,
    XIANZANG_STORE_SCENE_ID,
    XIANZANG_TASK_SCENE_ID,
    XianzangPageResult,
    enter_xianzang,
    is_xianzang_main_page_text,
    open_xianzang_tab,
    open_xianzang_optional_reward,
    leave_xianzang,
    read_xianzang_page,
)
from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_store import (
    complete_xianzang_store,
)
from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_tasks import (
    XianzangTaskCompletionResult,
    XianzangTaskProgress,
    complete_xianzang_tasks,
    parse_xianzang_task_progress,
)


XIANZANG_ROW_COLUMN_COUNTS = {1: 4, 2: 5, 3: 5}
XIANZANG_ROW_LABELS = {1: "珍宝奖励", 2: "稀有奖励", 3: "普通奖励"}
XIANZANG_ROW_SELECTION_LIMITS = {1: 1, 2: 3, 3: 5}
XIANZANG_RARE_RESOURCE_COLUMNS = {
    "仙花": 1,
    "灵兽": 2,
    "炼丹": 3,
    "淬体": 4,
    "洗灵": 5,
}
XIANZANG_RARE_RESOURCE_PRIORITY = ("炼丹", "仙花", "淬体", "灵兽", "洗灵")
_SELECTED_GREEN_LOWER_HSV = np.array((35, 70, 80), dtype=np.uint8)
_SELECTED_GREEN_UPPER_HSV = np.array((95, 255, 255), dtype=np.uint8)


class XianzangChoiceNotApplicableError(RuntimeError):
    """The current optional rewards are not a complete Shenlian choice set."""


@dataclass(frozen=True)
class XianzangRewardCandidate:
    column: int
    reward_item_id: int
    reward_name: str
    target_talisman_id: int | None
    kind: str = ""


@dataclass(frozen=True)
class XianzangShenlianChoice:
    candidate: XianzangRewardCandidate
    talisman_name: str
    rank: int
    wujing_level: int
    distance_to_next_stage: int
    candidate_evidence: tuple["XianzangShenlianCandidateEvidence", ...] = ()
    selection_reason: str = ""


@dataclass(frozen=True)
class XianzangShenlianCandidateEvidence:
    column: int
    reward_item_id: int
    target_talisman_id: int
    body_owned: bool
    rank: int
    wujing_level: int | None
    distance_to_next_stage: int | None
    selected: bool
    elimination_reason: str | None


@dataclass(frozen=True)
class XianzangRowSelectionResult:
    row: int
    desired_columns: tuple[int, ...]
    selected_columns: tuple[int, ...]
    click_points: tuple[tuple[float, float], ...]
    changed: bool


@dataclass(frozen=True)
class XianzangSelectionCompletionResult:
    prayer_category: str
    row_results: tuple[XianzangRowSelectionResult, ...]
    confirmed: bool
    final_scene: int | None
    final_scene_score: float


def build_xianzang_reward_candidates(
    reward_items: Sequence[Mapping[str, Any]],
) -> list[XianzangRewardCandidate]:
    """Normalize the left-to-right reward rows read from BothdrawMgr/config.

    The dynamic reader remains responsible for preserving the UI order.  This
    function deliberately does not infer identities from icons.
    """

    candidates: list[XianzangRewardCandidate] = []
    for column, item in enumerate(reward_items, start=1):
        reward_item_id = int(item.get("item_id") or item.get("reward_item_id") or 0)
        target_talisman_id = item.get("target_talisman_id")
        candidates.append(
            XianzangRewardCandidate(
                column=column,
                reward_item_id=reward_item_id,
                reward_name=str(item.get("name") or item.get("reward_name") or ""),
                target_talisman_id=(
                    int(target_talisman_id)
                    if target_talisman_id not in (None, "")
                    else None
                ),
                kind=str(item.get("kind") or ""),
            )
        )
    return candidates


def shenlian_distance_to_next_stage(wujing_level: int) -> int:
    """Return materials needed for the next meaningful Shenlian stage.

    Level 0 first targets 1/9.  Once activated, meaningful stages are the
    integer boundaries 1 0/9, 2 0/9, ... at levels 9, 18, ... .
    """

    level = int(wujing_level)
    if level < 0:
        raise ValueError(f"神炼等级不能为负数：{level}")
    if level == 0:
        return 1
    remainder = level % 9
    return 9 if remainder == 0 else 9 - remainder


def choose_xianzang_shenlian_candidate(
    candidates: Sequence[XianzangRewardCandidate],
    talisman_items: Iterable[Mapping[str, Any]],
) -> XianzangShenlianChoice:
    """Apply the permanent body-first Shenlian selection policy.

    Owned, unrefined bodies (level 0) always take precedence.  Only when none
    exists do refined bodies compete by distance to the next 9-level boundary;
    every tie is resolved from left to right.
    """

    if len(candidates) != XIANZANG_ROW_COLUMN_COUNTS[1]:
        raise XianzangChoiceNotApplicableError(
            f"珍宝神炼四选一必须正好有 4 个候选，实际 {len(candidates)} 个"
        )
    if any(
        candidate.target_talisman_id is None
        or candidate.kind not in {"", "talisman_refine_material"}
        for candidate in candidates
    ):
        raise XianzangChoiceNotApplicableError("当前珍宝候选不是完整的法宝神炼材料四选一")

    by_id = {
        int(item.get("talisman_id") or 0): item
        for item in talisman_items
        if int(item.get("talisman_id") or 0) > 0
    }
    choices: list[XianzangShenlianChoice] = []
    progress_by_column: dict[int, tuple[bool, int, int | None, int | None]] = {}
    for candidate in candidates:
        item = by_id.get(int(candidate.target_talisman_id or 0))
        if not item:
            progress_by_column[candidate.column] = (False, 0, None, None)
            continue
        rank = int(item.get("rank") or 0)
        owned = bool(item.get("owned")) and rank > 0
        if not owned:
            progress_by_column[candidate.column] = (
                False,
                rank,
                int(item.get("wujing_level") or 0),
                None,
            )
            continue
        wujing_level = int(item.get("wujing_level") or 0)
        distance = shenlian_distance_to_next_stage(wujing_level)
        progress_by_column[candidate.column] = (True, rank, wujing_level, distance)
        choices.append(
            XianzangShenlianChoice(
                candidate=candidate,
                talisman_name=str(item.get("name") or ""),
                rank=rank,
                wujing_level=wujing_level,
                distance_to_next_stage=distance,
            )
        )
    if not choices:
        raise RuntimeError("珍宝神炼四选一没有任何已拥有本体的可选法宝")

    unrefined = [choice for choice in choices if choice.wujing_level == 0]
    selected = min(
        unrefined or choices,
        key=lambda choice: (
            choice.distance_to_next_stage,
            choice.candidate.column,
        ),
    )
    selection_reason = (
        "优先未神炼且已拥有本体；并列按从左到右"
        if unrefined
        else "按距下一9级神炼梯度所需材料最少；并列按从左到右"
    )

    evidence: list[XianzangShenlianCandidateEvidence] = []
    for candidate in candidates:
        owned, rank, wujing_level, distance = progress_by_column[candidate.column]
        is_selected = candidate.column == selected.candidate.column
        if is_selected:
            elimination_reason = None
        elif not owned:
            elimination_reason = "未拥有法宝本体"
        elif unrefined and wujing_level != 0:
            elimination_reason = "存在未神炼且已拥有本体的候选，后者绝对优先"
        elif unrefined:
            elimination_reason = "同为未神炼且已拥有本体，按从左到右优先"
        elif distance != selected.distance_to_next_stage:
            elimination_reason = (
                f"距下一9级梯度需 {distance} 份，"
                f"多于最优 {selected.distance_to_next_stage} 份"
            )
        else:
            elimination_reason = "距下一9级梯度所需材料并列，按从左到右优先"
        evidence.append(
            XianzangShenlianCandidateEvidence(
                column=candidate.column,
                reward_item_id=candidate.reward_item_id,
                target_talisman_id=int(candidate.target_talisman_id or 0),
                body_owned=owned,
                rank=rank,
                wujing_level=wujing_level,
                distance_to_next_stage=distance,
                selected=is_selected,
                elimination_reason=elimination_reason,
            )
        )

    return XianzangShenlianChoice(
        candidate=selected.candidate,
        talisman_name=selected.talisman_name,
        rank=selected.rank,
        wujing_level=selected.wujing_level,
        distance_to_next_stage=selected.distance_to_next_stage,
        candidate_evidence=tuple(evidence),
        selection_reason=selection_reason,
    )


def choose_xianzang_rare_resource_columns(prayer_category: str) -> tuple[int, int, int]:
    """Reserve one slot for this week's prayer, then fill by fixed priority."""

    category = str(prayer_category or "").strip()
    if category not in XIANZANG_RARE_RESOURCE_COLUMNS:
        raise ValueError(f"未知祈愿类别：{prayer_category!r}")
    ordered_categories = [category]
    ordered_categories.extend(
        candidate
        for candidate in XIANZANG_RARE_RESOURCE_PRIORITY
        if candidate != category
    )
    return tuple(
        XIANZANG_RARE_RESOURCE_COLUMNS[candidate]
        for candidate in ordered_categories[:3]
    )


def xianzang_optional_reward_plan(
    treasure_column: int,
    *,
    prayer_category: str | None = None,
) -> dict[int, tuple[int, ...]]:
    category = str(prayer_category or current_prayer_cycle()).strip()
    treasure = int(treasure_column)
    if treasure < 1 or treasure > XIANZANG_ROW_COLUMN_COUNTS[1]:
        raise ValueError(f"珍宝候选必须为 1..4：{treasure!r}")
    return {
        1: (treasure,),
        2: choose_xianzang_rare_resource_columns(category),
        3: tuple(range(1, XIANZANG_ROW_COLUMN_COUNTS[3] + 1)),
    }


def _raw_view(view: Any) -> Mapping[str, Any]:
    raw = getattr(view, "raw", view)
    if not isinstance(raw, Mapping):
        raise RuntimeError("#448 缺少可用的场景数据")
    return raw


def _iter_shapes(nodes: Iterable[Any]) -> Iterable[Mapping[str, Any]]:
    for node in nodes:
        raw = getattr(node, "raw", node)
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("kind") or "") == "shape" or "x" in raw:
            yield raw
        children = raw.get("children")
        if isinstance(children, list):
            yield from _iter_shapes(children)


def _shape_by_title(view: Mapping[str, Any], title: str) -> Mapping[str, Any]:
    roots = view.get("shapes") or view.get("children") or []
    matches = [shape for shape in _iter_shapes(roots) if str(shape.get("title") or "") == title]
    if len(matches) != 1:
        raise RuntimeError(f"#448 必须唯一包含标注 {title!r}，实际 {len(matches)} 个")
    return matches[0]


def derive_xianzang_row_button_points(
    view: Any,
    row: int,
    *,
    column_count: int | None = None,
) -> tuple[tuple[float, float], ...]:
    """Derive a whole optional-reward row from two horizontal anchors.

    #448 intentionally labels only ``按钮1-1`` and ``按钮1-2`` to establish the
    horizontal pitch.  Each later row labels its first button; all remaining
    positions use the same pitch.
    """

    row_number = int(row)
    count = int(column_count or XIANZANG_ROW_COLUMN_COUNTS.get(row_number) or 0)
    if row_number not in XIANZANG_ROW_COLUMN_COUNTS or count <= 0:
        raise ValueError(f"自选奖励行无效：row={row!r}, column_count={column_count!r}")
    raw_view = _raw_view(view)
    planner = ActionPlanner()
    first_row_first = _shape_by_title(raw_view, "按钮1-1")
    first_row_second = _shape_by_title(raw_view, "按钮1-2")
    x1, y1 = planner.shape_center(raw_view, first_row_first)
    x2, y2 = planner.shape_center(raw_view, first_row_second)
    pitch_x = float(x2) - float(x1)
    pitch_y = float(y2) - float(y1)
    if pitch_x <= 0 or abs(pitch_y) > max(8.0, abs(pitch_x) * 0.15):
        raise RuntimeError(
            f"#448 第一排按钮锚点不满足稳定横排：delta=({pitch_x:.1f},{pitch_y:.1f})"
        )
    row_first = _shape_by_title(raw_view, f"按钮{row_number}-1")
    row_x, row_y = planner.shape_center(raw_view, row_first)
    return tuple(
        (
            float(row_x) + pitch_x * column,
            float(row_y) + pitch_y * column,
        )
        for column in range(count)
    )


def _decode_frame(frame_data_url: str) -> np.ndarray:
    payload = str(frame_data_url or "")
    if "," not in payload:
        raise RuntimeError("#448 当前帧不是有效 data URL")
    try:
        encoded = payload.split(",", 1)[1]
        image = cv2.imdecode(
            np.frombuffer(base64.b64decode(encoded), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
    except Exception as exc:
        raise RuntimeError(f"#448 当前帧解码失败：{exc}") from exc
    if image is None:
        raise RuntimeError("#448 当前帧解码为空")
    return image


def detect_xianzang_selected_columns(
    frame_data_url: str,
    points: Sequence[tuple[float, float]],
    *,
    crop_radius: int = 14,
    green_ratio_threshold: float = 0.12,
) -> tuple[int, ...]:
    """Detect the game's green checks inside the derived checkbox centers."""

    image = _decode_frame(frame_data_url)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    height, width = hsv.shape[:2]
    selected: list[int] = []
    radius = max(6, int(crop_radius))
    for column, (raw_x, raw_y) in enumerate(points, start=1):
        x = int(round(raw_x))
        y = int(round(raw_y))
        x1, x2 = max(0, x - radius), min(width, x + radius)
        y1, y2 = max(0, y - radius), min(height, y + radius)
        if x1 >= x2 or y1 >= y2:
            raise RuntimeError(f"#448 推导按钮{column}超出当前帧：({raw_x:.1f},{raw_y:.1f})")
        crop = hsv[y1:y2, x1:x2]
        mask = cv2.inRange(crop, _SELECTED_GREEN_LOWER_HSV, _SELECTED_GREEN_UPPER_HSV)
        if float(np.count_nonzero(mask)) / float(mask.size) >= float(green_ratio_threshold):
            selected.append(column)
    return tuple(selected)


def parse_xianzang_row_selected_fraction(
    text: str,
    row: int,
    *,
    row_labels: Mapping[int, str] | None = None,
) -> tuple[int, int] | None:
    row_number = int(row)
    label = (row_labels or XIANZANG_ROW_LABELS).get(row_number)
    denominator = XIANZANG_ROW_SELECTION_LIMITS.get(row_number)
    if not label or denominator is None:
        raise ValueError(f"自选奖励行无效：{row!r}")
    normalized = re.sub(r"\s+", "", str(text or ""))
    # Full-frame OCR commonly joins the following reward amount to the
    # fraction (for example ``0/3`` + ``50`` + ``200`` -> ``0/350200``).
    # The denominator is a #448 business contract, not an arbitrary OCR
    # integer, so bind it before the adjacent item quantities.
    match = re.search(
        rf"{re.escape(label)}(?:\([^)]*\))?(\d+)/{denominator}",
        normalized,
    )
    if match is None:
        return None
    return int(match.group(1)), denominator


def ensure_xianzang_row_choices_selected(
    runtime: Any,
    row: int,
    desired_columns: Sequence[int],
    *,
    scene_id: int = XIANZANG_OPTIONAL_REWARD_SCENE_ID,
    timeout_seconds: float = 4.0,
    row_labels: Mapping[int, str] | None = None,
    require_fraction_ocr: bool = True,
    allow_missing_fraction_ocr: bool = False,
) -> XianzangRowSelectionResult:
    """Idempotently select exactly the requested columns and verify the result."""

    row_number = int(row)
    count = XIANZANG_ROW_COLUMN_COUNTS.get(row_number)
    if count is None:
        raise ValueError(f"自选奖励行无效：{row!r}")
    desired = tuple(sorted({int(column) for column in desired_columns}))
    if not desired or desired[0] < 1 or desired[-1] > count:
        raise ValueError(f"第 {row_number} 排候选范围必须为 1..{count}：{desired!r}")
    target_view = runtime.view(int(scene_id))
    current_scene, score, frame = runtime.current_scene([int(scene_id)], update=True)
    if int(current_scene or 0) != int(scene_id) or float(score or 0) < 90.0:
        raise RuntimeError(
            f"当前不是可靠的 #{scene_id}，拒绝勾选：scene={current_scene}, score={float(score or 0):.1f}"
        )
    points = derive_xianzang_row_button_points(target_view, row_number, column_count=count)
    before = detect_xianzang_selected_columns(frame, points)
    click_columns = tuple(column for column in before if column not in desired) + tuple(
        column for column in desired if column not in before
    )
    click_points: list[tuple[float, float]] = []
    for column in click_columns:
        point = points[column - 1]
        runtime.click_frame_point(target_view, *point)
        click_points.append(point)
        time.sleep(0.25)

    deadline = time.monotonic() + max(0.5, float(timeout_seconds))
    last_selected = before
    last_fraction: tuple[int, int] | None = None
    while True:
        frame = runtime.cur_frame(update=True)
        last_selected = detect_xianzang_selected_columns(frame, points)
        last_fraction = parse_xianzang_row_selected_fraction(
            runtime.ocr_text(frame), row_number, row_labels=row_labels
        )
        expected_fraction = (len(desired), XIANZANG_ROW_SELECTION_LIMITS[row_number])
        fraction_closed = not require_fraction_ocr or (
            last_fraction == expected_fraction
            or (allow_missing_fraction_ocr and last_fraction is None)
        )
        if last_selected == desired and fraction_closed:
            return XianzangRowSelectionResult(
                row=row_number,
                desired_columns=desired,
                selected_columns=last_selected,
                click_points=tuple(click_points),
                changed=bool(click_points),
            )
        if time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    raise RuntimeError(
        f"#448 第 {row_number} 排勾选未闭环：期望={desired}，绿色勾={last_selected}，OCR={last_fraction}"
    )


def complete_xianzang_optional_reward_selection(
    runtime: Any,
    treasure_column: int,
    *,
    prayer_category: str | None = None,
    confirm: bool = True,
    scene_id: int = XIANZANG_OPTIONAL_REWARD_SCENE_ID,
    expected_after_scene_ids: Sequence[int] = (447,),
    timeout_seconds: float = 6.0,
    row_labels: Mapping[int, str] | None = None,
    require_fraction_ocr: bool = True,
    allow_missing_fraction_ocr: bool = False,
) -> XianzangSelectionCompletionResult:
    """Select all three #448 rows, verify each row, then optionally confirm."""

    category = str(prayer_category or current_prayer_cycle()).strip()
    plan = xianzang_optional_reward_plan(
        treasure_column,
        prayer_category=category,
    )
    row_results = tuple(
        ensure_xianzang_row_choices_selected(
            runtime,
            row,
            columns,
            scene_id=scene_id,
            timeout_seconds=timeout_seconds,
            row_labels=row_labels,
            require_fraction_ocr=require_fraction_ocr,
            allow_missing_fraction_ocr=allow_missing_fraction_ocr,
        )
        for row, columns in sorted(plan.items())
    )
    if not confirm:
        return XianzangSelectionCompletionResult(
            prayer_category=category,
            row_results=row_results,
            confirmed=False,
            final_scene=scene_id,
            final_scene_score=100.0,
        )

    current_scene, score, frame = runtime.current_scene([int(scene_id)], update=True)
    if int(current_scene or 0) != int(scene_id) or float(score or 0) < 90.0:
        raise RuntimeError(
            f"三排勾选后当前不是可靠的 #{scene_id}，拒绝确认："
            f"scene={current_scene}, score={float(score or 0):.1f}"
        )
    runtime.click_shape(int(scene_id), "确认", frame_data_url=frame)

    expected_after = tuple(int(value) for value in expected_after_scene_ids)
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    final_scene: int | None = int(scene_id)
    final_score = 100.0
    while True:
        candidates = [int(scene_id), *expected_after]
        final_scene, final_score, _frame = runtime.current_scene(candidates, update=True)
        final_text = runtime.ocr_text(_frame)
        landed_on_numbered_scene = (
            final_scene in expected_after
            and float(final_score or 0) >= 70.0
        )
        landed_on_current_unnumbered_main_page = (
            final_scene != int(scene_id)
            and is_xianzang_main_page_text(final_text)
        )
        if landed_on_numbered_scene or landed_on_current_unnumbered_main_page:
            return XianzangSelectionCompletionResult(
                prayer_category=category,
                row_results=row_results,
                confirmed=True,
                final_scene=final_scene,
                final_scene_score=float(final_score or 0),
            )
        if time.monotonic() >= deadline:
            break
        time.sleep(0.3)
    raise RuntimeError(
        f"#448 确认后未进入预期页面 {expected_after}，也未识别到蓬莱仙藏主页面："
        f"scene={final_scene}, score={float(final_score or 0):.1f}"
    )
