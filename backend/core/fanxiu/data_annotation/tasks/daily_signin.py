from __future__ import annotations

import base64
import hashlib
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.job_times import next_business_time
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.data_annotation.ocr_spatial import (
    group_ocr_tokens,
    locate_text_box,
)
from backend.core.fanxiu.instrumentation.activity_menu import (
    ActivityMenuSnapshot,
    read_activity_menu_snapshot,
)
from backend.core.fanxiu.instrumentation.activity_signin import (
    ActivitySigninSnapshot,
    read_activity_signin_snapshot,
)
from backend.core.fanxiu.runtime_gui.activity_menu import (
    GROUP_POPUP_ACTIVITY_GRID,
    WORLD_LEFT_ACTIVITY_GRID,
    ActivityMenuGrid,
    plan_activity_menu_click,
)


_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_DAY_LABEL_PATTERN = re.compile(r"第\s*(\d+)\s*天")
_DAILY_SIGNIN_TOTAL_DAYS = 28
_DAILY_SIGNIN_WORLD_TARGET = "group:110001"
_DAILY_SIGNIN_ENTRY_TARGET = "每日签到"
_DAILY_SIGNIN_ACTIVITY_ID = 1310001
_DAILY_SIGNIN_MILESTONE_DAYS = (3, 7, 14, 21, 28)
_DAILY_SIGNIN_CHECK_LOWER_HSV = np.array((35, 100, 100), dtype=np.uint8)
_DAILY_SIGNIN_CHECK_UPPER_HSV = np.array((75, 255, 255), dtype=np.uint8)
_DAILY_SIGNIN_UNCLAIMED_GREEN_MAX = 0.01
_DAILY_SIGNIN_CLAIMED_GREEN_MIN = 0.03


def _normalize_claimed_fraction(numerator_text: str, denominator_text: str) -> tuple[int, int]:
    denominator = int(denominator_text)
    numerator = int(numerator_text)
    if denominator > 0 and numerator > denominator:
        # Paddle may join a neighbouring day label with the fraction, e.g.
        # ``第2天`` + ``23/28`` becomes ``223/28``.  The fraction's numerator
        # cannot have more digits than the denominator, so retain its longest
        # valid suffix.
        for width in range(min(len(numerator_text), len(denominator_text)), 0, -1):
            candidate = int(numerator_text[-width:])
            if candidate <= denominator:
                numerator = candidate
                break
    return numerator, denominator


def parse_daily_signin_claimed(tokens: list[dict[str, Any]]) -> tuple[int, int] | None:
    """Read claimed/total with the shared separator-agnostic parser."""

    fragments = group_ocr_tokens(tokens)
    candidates = [str(fragment.get("text") or "") for fragment in fragments]
    if not candidates:
        candidates = [str(token.get("text") or "") for token in tokens if isinstance(token, dict)]
    for text in candidates:
        claimed = parse_ocr_values(text, expected_count=2, allow_extra_numbers=True)
        if claimed is not None:
            return _normalize_claimed_fraction(str(claimed[0]), str(claimed[1]))
    roi_text = "".join(
        str(token.get("text") or "")
        for token in tokens
        if isinstance(token, dict)
    )
    claimed = parse_ocr_values(roi_text, expected_count=2, allow_extra_numbers=True)
    if claimed is not None:
        return _normalize_claimed_fraction(str(claimed[0]), str(claimed[1]))
    return None


def daily_signin_milestone_boxes(tokens: list[dict[str, Any]]) -> dict[int, list[dict[str, float]]]:
    """Return milestone boxes without ever merging different Paddle text lines."""

    result: dict[int, list[dict[str, float]]] = {}
    for fragment in group_ocr_tokens(tokens):
        text = str(fragment.get("text") or "").translate(_FULLWIDTH_DIGITS)
        match = _DAY_LABEL_PATTERN.fullmatch(text)
        if match is None:
            continue
        box = {
            key: float(fragment.get(key) or 0)
            for key in ("x", "y", "w", "h")
        }
        if box["w"] <= 0 or box["h"] <= 0:
            continue
        result.setdefault(int(match.group(1)), []).append(box)
    return result


def daily_signin_day_box(
    tokens: list[dict[str, Any]],
    target_day: int,
) -> dict[str, float] | None:
    """Locate a day label, or reconstruct one occluded slot from its row.

    The calendar is a five-column ordered grid.  Login attribute floaters can
    cover the complete label and make OCR omit one slot, while four adjacent
    labels in the same row still uniquely establish the missing geometry.
    Reconstruction is accepted only from the complete remainder of that
    target's row with a stable horizontal pitch; it never invents eligibility.
    """

    boxes = daily_signin_milestone_boxes(tokens)
    direct = boxes.get(int(target_day), [])
    if len(direct) == 1:
        return {**direct[0], "day": float(target_day), "source": "ocr"}
    if direct:
        return None

    row_start = ((int(target_day) - 1) // 5) * 5 + 1
    row_days = tuple(range(row_start, min(row_start + 5, _DAILY_SIGNIN_TOTAL_DAYS + 1)))
    visible_days = tuple(day for day in row_days if day != int(target_day))
    if len(visible_days) < 3 or any(len(boxes.get(day, [])) != 1 for day in visible_days):
        return None
    row = [boxes[day][0] for day in visible_days]
    centers = [float(box["x"]) + float(box["w"]) / 2.0 for box in row]
    ys = [float(box["y"]) for box in row]
    heights = [float(box["h"]) for box in row]
    if max(ys) - min(ys) > 12.0 or max(heights) - min(heights) > 12.0:
        return None
    pitches = [
        (right_center - left_center) / (right_day - left_day)
        for left_day, right_day, left_center, right_center in zip(
            visible_days,
            visible_days[1:],
            centers,
            centers[1:],
        )
    ]
    pitch = float(np.median(pitches))
    if not 120.0 <= pitch <= 200.0 or max(abs(value - pitch) for value in pitches) > 8.0:
        return None
    projected_centers = [
        center + (int(target_day) - day) * pitch
        for day, center in zip(visible_days, centers)
    ]
    if max(projected_centers) - min(projected_centers) > 8.0:
        return None
    width = float(np.median([float(box["w"]) for box in row]))
    return {
        "x": float(np.median(projected_centers)) - width / 2.0,
        "y": float(np.median(ys)),
        "w": width,
        "h": float(np.median(heights)),
        "day": float(target_day),
        "source": "ordered_row_inference",
    }


def daily_signin_day_green_check_ratio(
    frame_data_url: str,
    day_box: dict[str, float],
    *,
    check_offset_below_label: float = 100.0,
    crop_radius: int = 32,
) -> float:
    """Measure the green claimed-check signal directly below one day label.

    It is deliberately a *fallback* for a fraction obscured by an external
    floating widget.  A non-zero/unclear signal never authorizes a click; only
    a near-zero signal can prove the current day's visible card is unclaimed.
    """

    payload = str(frame_data_url or "")
    if "," not in payload:
        raise RuntimeError("日常_签到：当前帧不是有效 data URL")
    try:
        encoded = payload.split(",", 1)[1]
        image = cv2.imdecode(
            np.frombuffer(base64.b64decode(encoded), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
    except Exception as exc:
        raise RuntimeError(f"日常_签到：当前帧解码失败：{exc}") from exc
    if image is None:
        raise RuntimeError("日常_签到：当前帧解码为空")
    required = ("x", "y", "w", "h")
    if any(key not in day_box for key in required):
        raise RuntimeError("日常_签到：日期 OCR 缺少定位框")
    x = int(round(float(day_box["x"]) + float(day_box["w"]) / 2.0))
    y = int(
        round(
            float(day_box["y"])
            + float(day_box["h"])
            + float(check_offset_below_label)
        )
    )
    radius = max(12, int(crop_radius))
    height, width = image.shape[:2]
    x1, x2 = max(0, x - radius), min(width, x + radius)
    y1, y2 = max(0, y - radius), min(height, y + radius)
    if x1 >= x2 or y1 >= y2:
        raise RuntimeError(f"日常_签到：第{day_box.get('day', '?')}天勾选区超出当前帧")
    hsv = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, _DAILY_SIGNIN_CHECK_LOWER_HSV, _DAILY_SIGNIN_CHECK_UPPER_HSV)
    return float(np.count_nonzero(mask)) / float(mask.size)


class DailySigninTaskMixin:
    @staticmethod
    def _daily_signin_read_milestone_snapshot() -> ActivitySigninSnapshot:
        return read_activity_signin_snapshot()

    @staticmethod
    def _daily_signin_read_activity_menu(kind: str) -> ActivityMenuSnapshot:
        """Read the currently rendered menu through the strict read-only probe."""

        return read_activity_menu_snapshot(kind)  # type: ignore[arg-type]

    @staticmethod
    def _daily_signin_menu_gui_candidates(
        tokens: list[dict[str, Any]],
        target: str,
    ) -> tuple[tuple[dict[str, Any], ...], str | None]:
        """Canonicalize only a unique, short sign-in OCR line.

        The game decoration may omit or corrupt the optional ``每日`` prefix.
        This alias changes no business identity: Runtime has already selected
        the unique target row, while the short OCR line contributes geometry.
        A long joined line or multiple ``签到`` lines stays ambiguous.
        """

        values = tuple(tokens)
        if target != _DAILY_SIGNIN_ENTRY_TARGET:
            return values, None
        matches: list[dict[str, Any]] = []
        for fragment in group_ocr_tokens(tokens):
            text = re.sub(r"\s+", "", str(fragment.get("text") or ""))
            if "签到" not in text or len(text) > 6:
                continue
            matches.append(
                {
                    "key": f"daily-signin-alias:{len(matches) + 1}",
                    "text": _DAILY_SIGNIN_ENTRY_TARGET,
                    **{
                        key: float(fragment.get(key) or 0)
                        for key in ("x", "y", "w", "h")
                    },
                }
            )
        if len(matches) > 1:
            return values, "当前帧存在多个短「签到」OCR 行"
        return values + tuple(matches), None

    @staticmethod
    def _daily_signin_expand_joined_runtime_candidates(
        tokens: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        snapshot: ActivityMenuSnapshot,
    ) -> tuple[dict[str, Any], ...]:
        """Split OCR-joined grid rows by exact authoritative Runtime names.

        Paddle commonly merges adjacent first-row labels into one line, for
        example ``每日签到试炼手册成长基金``.  Runtime already owns the item
        identities and order, so exact non-overlapping name spans can safely
        contribute current-frame geometry without making OCR authoritative.
        """

        values = tuple(tokens)
        expanded: list[dict[str, Any]] = []
        for fragment in group_ocr_tokens(list(values)):
            text = re.sub(r"\s+", "", str(fragment.get("text") or ""))
            if not text or not all(key in fragment for key in ("x", "y", "w", "h")):
                continue
            spans: list[tuple[int, int, Any]] = []
            for item in snapshot.items:
                name = re.sub(r"\s+", "", str(item.name or ""))
                if not name:
                    continue
                start = text.find(name)
                if start < 0 or text.find(name, start + 1) >= 0:
                    continue
                spans.append((start, start + len(name), item))
            spans.sort(key=lambda value: (value[0], value[1]))
            if (
                len(spans) < 2
                or spans[0][0] != 0
                or spans[-1][1] != len(text)
                or any(left[1] != right[0] for left, right in zip(spans, spans[1:]))
                or any(
                    int(left[2].index) + 1 != int(right[2].index)
                    for left, right in zip(spans, spans[1:])
                )
            ):
                continue
            x = float(fragment["x"])
            width = float(fragment["w"])
            for start, end, item in spans:
                expanded.append(
                    {
                        "key": f"runtime-joined:{item.index}:{fragment.get('parent_line_id', '')}",
                        "text": str(item.name),
                        "x": x + width * start / len(text),
                        "y": float(fragment["y"]),
                        "w": width * (end - start) / len(text),
                        "h": float(fragment["h"]),
                    }
                )
        return values + tuple(expanded)

    def _daily_signin_click_menu_target(
        self,
        runtime: Any,
        *,
        scene_id: int,
        menu_kind: str,
        target: str,
        grid: ActivityMenuGrid,
        label: str,
        attempts: int,
        retry_wait_seconds: float,
        minimum_anchor_score: float = 0.45,
        ocr_shape_names: tuple[str, ...] = (),
        fallback_ocr_shape_names: tuple[str, ...] = (),
    ):
        """Align one authoritative Runtime menu item to the current GUI frame.

        Runtime owns item identity and order.  OCR contributes only current-frame
        geometry.  The menu is read again immediately before the click so an old
        list/fingerprint can never authorize a later frame.
        """

        attempt_count = max(1, int(attempts))
        last_reason = "未开始对齐"
        for attempt in range(attempt_count):
            current_scene, _score, frame = runtime.current_scene(
                [scene_id],
                update=True,
            )
            # A group popup is a transient layer above #34.  Its visual scene
            # projection can lag or be confused with the world, while the
            # active ActivityBtnGroup Runtime sequence is the authoritative
            # proof that this very popup is live.  Do not make a stale #34
            # projection reject otherwise current Runtime-GUI evidence.
            before = self._daily_signin_read_activity_menu(menu_kind)
            popup_runtime_visible = menu_kind == "group_popup" and before.complete
            if current_scene != scene_id and not popup_runtime_visible:
                last_reason = f"当前场景不是 #{scene_id}"
            else:
                # The popup's full-frame OCR may join every first-row title
                # into one long line.  A dedicated action ROI supplies only
                # geometry; Runtime remains the authority for the item ID and
                # order.  Keep the world-list path full-frame because it has
                # no equivalent narrow per-item action ROI.
                tokens = (
                    runtime.ocr_tokens_in_shapes(
                        scene_id,
                        list(ocr_shape_names),
                        frame_data_url=frame,
                        padding=0,
                    )
                    if ocr_shape_names
                    else runtime.ocr_tokens(frame)
                )
                gui_candidates, candidate_error = self._daily_signin_menu_gui_candidates(
                    tokens,
                    target,
                )
                gui_candidates = self._daily_signin_expand_joined_runtime_candidates(
                    gui_candidates,
                    before,
                )
                if candidate_error is not None:
                    last_reason = candidate_error
                    if attempt + 1 < attempt_count:
                        self._log(
                            "wait",
                            f"{label}暂未安全对齐，刷新重试 {attempt + 1}/{attempt_count - 1}：{last_reason}",
                        )
                        yield from runtime.wait_action_settle(max(0.0, retry_wait_seconds))
                    continue
                plan = plan_activity_menu_click(
                    before,
                    target,
                    gui_candidates,
                    grid=grid,
                    minimum_anchor_score=minimum_anchor_score,
                )
                if (
                    not plan.ready
                    and plan.status == "incomplete_runtime"
                    and menu_kind == "group_popup"
                    and scene_id == 403
                    and target == _DAILY_SIGNIN_ENTRY_TARGET
                ):
                    # #403's required scene identity is the same exact
                    # "每日签到" text, and this action ROI is formally marked
                    # as the #404 edge.  When ActivityBtnGroup's transient Lua
                    # list is not materialized, two fresh frames with one
                    # unique short sign-in line are therefore sufficient for
                    # this one action only; no other menu target gets an OCR
                    # bypass.
                    direct = [
                        item
                        for item in gui_candidates
                        if str(item.get("key") or "").startswith("daily-signin-alias:")
                    ]
                    if len(direct) == 1:
                        candidate = direct[0]
                        point = (
                            float(candidate["x"]) + float(candidate["w"]) / 2.0,
                            float(candidate["y"]) - float(candidate["h"]),
                        )
                        fresh_scene, _fresh_score, fresh_frame = runtime.current_scene(
                            [scene_id], update=True
                        )
                        fresh_tokens = runtime.ocr_tokens_in_shapes(
                            scene_id,
                            list(ocr_shape_names),
                            frame_data_url=fresh_frame,
                            padding=0,
                        )
                        fresh_candidates, fresh_error = self._daily_signin_menu_gui_candidates(
                            fresh_tokens, target
                        )
                        fresh_direct = [
                            item
                            for item in fresh_candidates
                            if str(item.get("key") or "").startswith("daily-signin-alias:")
                        ]
                        if fresh_scene == scene_id and fresh_error is None and len(fresh_direct) == 1:
                            fresh = fresh_direct[0]
                            fresh_point = (
                                float(fresh["x"]) + float(fresh["w"]) / 2.0,
                                float(fresh["y"]) - float(fresh["h"]),
                            )
                            if all(abs(left - right) <= 6.0 for left, right in zip(point, fresh_point)):
                                self._log(
                                    "info",
                                    f"{label}：Runtime 列表未物化，使用可靠 #403 与双帧唯一‘每日签到’动作",
                                )
                                runtime.click_frame_point(scene_id, *fresh_point)
                                return plan
                    last_reason = "incomplete_runtime/双帧唯一‘每日签到’动作未成立"
                if (
                    not plan.ready
                    and plan.status == "insufficient_geometry"
                    and fallback_ocr_shape_names
                ):
                    # The narrow action ROI can become stale when the popup
                    # reflows, while the formal grid container still proves
                    # the current #403 layout.  Reuse the same fresh frame and
                    # the same Runtime sequence; the broader OCR contributes
                    # geometry only and remains subject to the strict unique
                    # short-signin/ordered-grid checks below.
                    fallback_tokens = runtime.ocr_tokens_in_shapes(
                        scene_id,
                        list(fallback_ocr_shape_names),
                        frame_data_url=frame,
                        padding=0,
                        crop=True,
                    )
                    fallback_candidates, fallback_error = (
                        self._daily_signin_menu_gui_candidates(
                            fallback_tokens,
                            target,
                        )
                    )
                    if fallback_error is None:
                        fallback_candidates = self._daily_signin_expand_joined_runtime_candidates(
                            fallback_candidates,
                            before,
                        )
                        fallback_plan = plan_activity_menu_click(
                            before,
                            target,
                            fallback_candidates,
                            grid=grid,
                            minimum_anchor_score=minimum_anchor_score,
                        )
                        if fallback_plan.ready:
                            gui_candidates = fallback_candidates
                            plan = fallback_plan
                if plan.ready and plan.point is not None:
                    # The popup object is rebound on every read and the world
                    # menu may be replaced after a UI refresh.  Re-read both
                    # process identity and fingerprint immediately before act.
                    after = self._daily_signin_read_activity_menu(menu_kind)
                    if (
                        after.complete
                        and after.pid == before.pid
                        and after.process_start_ticks == before.process_start_ticks
                        and after.fingerprint == before.fingerprint
                    ):
                        verified = plan_activity_menu_click(
                            after,
                            target,
                            gui_candidates,
                            grid=grid,
                            minimum_anchor_score=minimum_anchor_score,
                        )
                        if verified.ready and verified.point == plan.point:
                            runtime.click_frame_point(
                                scene_id,
                                float(verified.point[0]),
                                float(verified.point[1]),
                            )
                            return verified
                        last_reason = (
                            "点击前 Runtime 重读后目标坐标不再唯一稳定："
                            f"{verified.status}/{verified.reason}"
                        )
                    else:
                        last_reason = "点击前 Runtime 菜单身份或有序清单已变化"
                else:
                    last_reason = f"{plan.status}/{plan.reason}"
            if attempt + 1 < attempt_count:
                self._log(
                    "wait",
                    f"{label}暂未安全对齐，刷新重试 {attempt + 1}/{attempt_count - 1}：{last_reason}",
                )
                yield from runtime.wait_action_settle(max(0.0, retry_wait_seconds))
        raise RuntimeError(f"{label}连续 {attempt_count} 次未安全对齐：{last_reason}")

    @staticmethod
    def _daily_signin_calendar_day(now: datetime | None = None) -> int:
        return int((now or job_now()).day)

    @staticmethod
    def _daily_signin_signed_days_fingerprint(days: tuple[int, ...]) -> str:
        canonical = ",".join(str(day) for day in sorted(days))
        return hashlib.sha256(canonical.encode("ascii")).hexdigest()[:16]

    @staticmethod
    def _daily_signin_optional_green_ratio(
        frame_data_url: str,
        day_box: dict[str, float],
    ) -> float | None:
        """Read visual corroboration without promoting it to a success gate."""

        try:
            return daily_signin_day_green_check_ratio(frame_data_url, day_box)
        except RuntimeError:
            return None

    @staticmethod
    def _daily_signin_validate_snapshot(
        snapshot: ActivitySigninSnapshot,
        *,
        label: str,
    ) -> tuple[int, ...]:
        if snapshot.activity_id != _DAILY_SIGNIN_ACTIVITY_ID:
            raise RuntimeError(f"日常_签到：{label} Runtime 活动异常 {snapshot.activity_id}")
        if snapshot.total_days != _DAILY_SIGNIN_TOTAL_DAYS:
            raise RuntimeError(
                f"日常_签到：{label} Runtime 周期异常 {snapshot.total_days}"
            )
        signed_days = tuple(sorted(snapshot.signed_days))
        if (
            len(signed_days) != snapshot.signed_day_count
            or len(set(signed_days)) != len(signed_days)
            or any(day < 1 or day > snapshot.total_days for day in signed_days)
        ):
            raise RuntimeError(
                f"日常_签到：{label} Runtime 已签到日期集合异常"
            )
        return signed_days

    @staticmethod
    def _daily_signin_click_offset(runtime: Any, scene_id: int, box: dict[str, float], y_heights: float) -> None:
        runtime.click_frame_point(
            scene_id,
            float(box["x"]) + float(box["w"]) / 2,
            float(box["y"]) + float(box["h"]) * float(y_heights),
        )

    @staticmethod
    def _daily_signin_read_claimed(runtime: Any, *, attempts: int = 3) -> tuple[int, int]:
        claimed: tuple[int, int] | None = None
        for _attempt in range(max(1, int(attempts))):
            frame = runtime.cur_frame(update=True)
            tokens = runtime.ocr_tokens_in_shapes(
                404,
                ["已领"],
                frame_data_url=frame,
                padding=0,
            )
            claimed = parse_daily_signin_claimed(tokens)
            if claimed is None:
                # The small red fraction may be absent from shared OCR while
                # the claimed-reward animation is settling.  Retry the tight
                # crop, then obtain a genuinely fresh frame on the next pass.
                tokens = runtime.ocr_tokens_in_shapes(
                    404,
                    ["已领"],
                    frame_data_url=frame,
                    padding=8,
                    crop=True,
                )
                claimed = parse_daily_signin_claimed(tokens)
            if claimed is not None:
                break
        if claimed is None:
            raise RuntimeError("日常_签到：无法从 #404「已领」识别已领天数")
        numerator, denominator = claimed
        if denominator <= 0 or numerator < 0 or numerator > denominator:
            raise RuntimeError(f"日常_签到：已领天数异常 {numerator}/{denominator}")
        return numerator, denominator

    def _daily_signin_read_claimed_after_animation(
        self,
        runtime: Any,
        *,
        attempts: int = 3,
        retry_wait_seconds: float = 0.45,
    ):
        """Retry the whole OCR read on spaced, fresh post-animation frames."""

        attempt_count = max(1, int(attempts))
        last_error: RuntimeError | None = None
        for attempt in range(attempt_count):
            try:
                return self._daily_signin_read_claimed(runtime, attempts=1)
            except RuntimeError as exc:
                last_error = exc
                if attempt + 1 < attempt_count:
                    yield from runtime.wait_action_settle(max(0.0, float(retry_wait_seconds)))
        assert last_error is not None
        raise last_error

    def _daily_signin_claim_available_milestones(
        self,
        runtime: Any,
        *,
        reached_days: int,
        total_days: int,
        settle_seconds: float,
    ):
        """Claim every reached milestone whose live controller authorizes it."""

        snapshot = self._daily_signin_read_milestone_snapshot()
        if snapshot.activity_id != _DAILY_SIGNIN_ACTIVITY_ID:
            raise RuntimeError(
                f"日常_签到：累签 Runtime 活动异常 {snapshot.activity_id}"
            )
        if snapshot.total_days != total_days or snapshot.signed_day_count != reached_days:
            raise RuntimeError(
                "日常_签到：累签 Runtime 与页面进度不一致："
                f"runtime={snapshot.signed_day_count}/{snapshot.total_days}, "
                f"page={reached_days}/{total_days}"
            )
        days = tuple(item.day for item in snapshot.milestones)
        if days != _DAILY_SIGNIN_MILESTONE_DAYS:
            raise RuntimeError(f"日常_签到：累签节点异常 {days}")
        if any(item.can_get_reward and item.day > reached_days for item in snapshot.milestones):
            raise RuntimeError("日常_签到：未达到的累签节点被 Runtime 标记为可领取")

        claimed_now: list[int] = []
        for milestone in snapshot.milestones:
            if not milestone.can_get_reward:
                continue
            frame = runtime.cur_frame(update=True)
            tokens = runtime.ocr_tokens_in_shapes(
                404,
                ["累签奖励"],
                frame_data_url=frame,
                padding=0,
            )
            boxes = daily_signin_milestone_boxes(tokens)
            if tuple(sorted(boxes)) != _DAILY_SIGNIN_MILESTONE_DAYS:
                raise RuntimeError(
                    f"日常_签到：累签 OCR 节点不完整 {tuple(sorted(boxes))}"
                )
            matches = boxes.get(milestone.day, [])
            if len(matches) != 1:
                raise RuntimeError(
                    f"日常_签到：第{milestone.day}天累签奖励坐标不唯一"
                )
            before_green_ratio: float | None = None
            if milestone.day < total_days:
                before_green_ratio = daily_signin_day_green_check_ratio(
                    frame,
                    {**matches[0], "day": milestone.day},
                    check_offset_below_label=-140.0,
                    crop_radius=36,
                )
                if before_green_ratio > _DAILY_SIGNIN_UNCLAIMED_GREEN_MAX:
                    raise RuntimeError(
                        f"日常_签到：第{milestone.day}天 Runtime 可领但已有绿色勾"
                        f"（green_ratio={before_green_ratio:.4f}）"
                    )

            if milestone.day == total_days:
                self._log("action", "日常_签到：领取第28天最终累签大奖")
                runtime.click_shape_center(404, "最终大奖。")
            else:
                self._log("action", f"日常_签到：领取第{milestone.day}天累签奖励")
                self._daily_signin_click_offset(runtime, 404, matches[0], -4.0)
            yield from runtime.wait_action_settle(settle_seconds)

            after = self._daily_signin_read_milestone_snapshot()
            after_item = next(
                (item for item in after.milestones if item.day == milestone.day),
                None,
            )
            if (
                after.activity_id != snapshot.activity_id
                or after.turn_id != snapshot.turn_id
                or after_item is None
                or after_item.can_get_reward
                or len(after.got_reward_ids) != len(snapshot.got_reward_ids) + 1
                or not set(snapshot.got_reward_ids) < set(after.got_reward_ids)
            ):
                raise RuntimeError(
                    f"日常_签到：第{milestone.day}天累签奖励点击后 Runtime 未收敛"
                )
            if milestone.day < total_days:
                after_frame = runtime.cur_frame(update=True)
                after_tokens = runtime.ocr_tokens_in_shapes(
                    404,
                    ["累签奖励"],
                    frame_data_url=after_frame,
                    padding=0,
                )
                after_matches = daily_signin_milestone_boxes(after_tokens).get(
                    milestone.day, []
                )
                if len(after_matches) != 1:
                    raise RuntimeError(
                        f"日常_签到：第{milestone.day}天领取后 OCR 节点不唯一"
                    )
                after_green_ratio = daily_signin_day_green_check_ratio(
                    after_frame,
                    {**after_matches[0], "day": milestone.day},
                    check_offset_below_label=-140.0,
                    crop_radius=36,
                )
                if after_green_ratio < _DAILY_SIGNIN_CLAIMED_GREEN_MIN:
                    raise RuntimeError(
                        f"日常_签到：第{milestone.day}天领取后未出现绿色勾"
                        f"（before={before_green_ratio:.4f}, after={after_green_ratio:.4f}）"
                    )
            claimed_now.append(milestone.day)
            self._log(
                "info",
                f"日常_签到：第{milestone.day}天累签奖励已由 Runtime 与页面状态共同确认领取",
            )
            snapshot = after
        return tuple(claimed_now)

    def _daily_signin_result(
        self,
        outcome: str,
        message: str,
        *,
        payload: dict[str, Any] | None = None,
        claimed_before: int | None = None,
        claimed_after: int | None = None,
        total_days: int | None = None,
        milestones_claimed: tuple[int, ...] = (),
        business_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        scheduler_task_id = str((payload or {}).get("__scheduler_task_id") or "").strip()
        if scheduler_task_id:
            next_time = next_business_time(("00:00",))
            self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
            message = f"{message}，下次 {next_time}"
        return {
            "result": "success",
            "message": message,
            "outcome": outcome,
            "claimed_before": claimed_before,
            "claimed_after": claimed_after,
            "total_days": total_days,
            "milestones_claimed": list(milestones_claimed),
            "business_evidence": dict(business_evidence or {}),
            "current_scene": 34,
        }

    def _daily_signin_return_from_404(
        self,
        runtime: Any,
        *,
        settle_seconds: float,
        timeout: float,
    ):
        # A reward popup may consume the first click at the real #404 return
        # coordinate.  A second bounded click then performs the actual return.
        for _attempt in range(2):
            runtime.click_shape_center(404, "返回")
            yield from runtime.wait_action_settle(settle_seconds)
            scene_id, _score, _frame = runtime.current_scene([34, 404], update=True)
            if scene_id == 34:
                return
            if scene_id != 404:
                # The first return may already have left #404, but a floating
                # layer or world variant can keep the narrow [34, 404]
                # classifier from naming it #34.  Never reuse the #404 return
                # coordinate on an unconfirmed page: on the world it is the
                # entrance to #20.  Let the formal scene graph perform the
                # remaining bounded navigation instead.
                break
        yield from runtime.goto_view(34)
        yield from runtime.wait_view(34, timeout=timeout, label="日常_签到：等待返回世界 #34")

    def _daily_signin_finish_from_404(
        self,
        runtime: Any,
        outcome: str,
        message: str,
        *,
        payload: dict[str, Any],
        claimed_before: int,
        claimed_after: int,
        total_days: int,
        milestones_claimed: tuple[int, ...] = (),
        business_evidence: dict[str, Any] | None = None,
        settle_seconds: float,
        timeout: float,
    ):
        """Commit the proven business result before best-effort departure.

        Returning from #404 can enter a long, text-free world animation.  Once
        the current day's claimed fact has been proven, that animation is not
        allowed to erase the business completion point or trigger a duplicate
        sign-in attempt.
        """

        result = self._daily_signin_result(
            outcome,
            message,
            payload=payload,
            claimed_before=claimed_before,
            claimed_after=claimed_after,
            total_days=total_days,
            milestones_claimed=milestones_claimed,
            business_evidence=business_evidence,
        )
        try:
            yield from self._daily_signin_return_from_404(
                runtime,
                settle_seconds=settle_seconds,
                timeout=timeout,
            )
        except (RuntimeError, TimeoutError) as exc:
            warning = f"日常_签到：业务已闭环，返回世界仍在过渡中：{exc}"
            self._log("warning", warning)
            result["message"] = f"{result['message']}；返回世界仍在过渡中"
            result["current_scene"] = None
        return result

    def _execute_daily_signin_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_签到资产树路径，无法执行作业")

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        view_timeout = float(payload.get("view_timeout") or 12.0)
        popup_wait_seconds = float(payload.get("popup_wait_seconds") or 3.0)
        return_settle_seconds = float(payload.get("return_settle_seconds") or 1.0)

        # The registered Cell wrapper has already normalized this new attempt
        # to #34.  This business function always runs the complete entry chain;
        # it never interprets #403/#404 as a persisted step from an older run.
        for entry_attempt in range(2):
            yield from self._daily_signin_click_menu_target(
                runtime,
                scene_id=34,
                menu_kind="world_left",
                target=_DAILY_SIGNIN_WORLD_TARGET,
                grid=WORLD_LEFT_ACTIVITY_GRID,
                label="日常_签到：#34 左侧「特惠」",
                attempts=3,
                retry_wait_seconds=return_settle_seconds,
            )
            try:
                yield from runtime.wait_view(
                    403,
                    timeout=view_timeout,
                    label="日常_签到：等待特惠页 #403",
                )
                break
            except TimeoutError:
                scene_id, _score, _frame = runtime.current_scene([34, 403], update=True)
                if scene_id != 34 or entry_attempt >= 1:
                    raise
                self._log(
                    "wait",
                    "日常_签到：特惠入口首次点击未生效且仍可靠位于 #34，重读菜单后再试一次",
                )
                yield from runtime.wait_action_settle(return_settle_seconds)

        yield from self._daily_signin_click_menu_target(
            runtime,
            scene_id=403,
            menu_kind="group_popup",
            target=_DAILY_SIGNIN_ENTRY_TARGET,
            grid=GROUP_POPUP_ACTIVITY_GRID,
            label="日常_签到：特惠页「每日签到」",
            attempts=3,
            retry_wait_seconds=return_settle_seconds,
            minimum_anchor_score=0.35,
            ocr_shape_names=("每日签到",),
            fallback_ocr_shape_names=("特惠活动网格",),
        )
        yield from runtime.wait_view(404, timeout=view_timeout, label="日常_签到：等待签到页 #404")

        effective_now = job_now()
        business_date = effective_now.date().isoformat()
        calendar_day = self._daily_signin_calendar_day(effective_now)
        snapshot_before = self._daily_signin_read_milestone_snapshot()
        signed_days_before = self._daily_signin_validate_snapshot(
            snapshot_before,
            label="领取前",
        )
        signed_before_fingerprint = self._daily_signin_signed_days_fingerprint(
            signed_days_before
        )
        business_evidence_before = {
            "business_date": business_date,
            "effective_now": effective_now.isoformat(sep=" "),
            "target_day": calendar_day,
            "activity_id": snapshot_before.activity_id,
            "turn_id": snapshot_before.turn_id,
            "signed_days": list(signed_days_before),
            "signed_days_fingerprint": signed_before_fingerprint,
            "target_membership": calendar_day in signed_days_before,
        }
        self._log(
            "info",
            "日常_签到：业务证据 "
            f"business_date={business_date}, effective_now={effective_now.isoformat(sep=' ')}, "
            f"target_day={calendar_day}, activity={snapshot_before.activity_id}, "
            f"turn={snapshot_before.turn_id}, signed_days={signed_days_before}, "
            f"fingerprint={signed_before_fingerprint}, "
            f"target_membership={calendar_day in signed_days_before}",
        )
        fraction_available = True
        try:
            claimed_before, total_days = yield from self._daily_signin_read_claimed_after_animation(runtime)
        except RuntimeError as exc:
            # The floating assistant can cover just the red X/28 fraction.
            # Keep the error as evidence; a direct current-day check below can
            # only establish "unclaimed", never invent a claimed count.
            fraction_available = False
            fraction_error = exc
            claimed_before, total_days = -1, _DAILY_SIGNIN_TOTAL_DAYS
            self._log("wait", f"日常_签到：已领分数不可见，改用当前日期格勾选证据：{exc}")
        if total_days != _DAILY_SIGNIN_TOTAL_DAYS:
            raise RuntimeError(
                f"日常_签到：签到周期异常 {claimed_before}/{total_days}，预期共 {_DAILY_SIGNIN_TOTAL_DAYS} 天"
            )
        if fraction_available and (
            claimed_before != snapshot_before.signed_day_count
            or total_days != snapshot_before.total_days
        ):
            raise RuntimeError(
                "日常_签到：页面分数与 Runtime 已签到集合不一致："
                f"page={claimed_before}/{total_days}, "
                f"runtime={snapshot_before.signed_day_count}/{snapshot_before.total_days}"
            )
        if fraction_available and claimed_before == total_days and calendar_day >= total_days:
            milestones_claimed = yield from self._daily_signin_claim_available_milestones(
                runtime,
                reached_days=claimed_before,
                total_days=total_days,
                settle_seconds=popup_wait_seconds,
            )
            return (yield from self._daily_signin_finish_from_404(
                runtime,
                "milestone_claimed" if milestones_claimed else "already_claimed",
                f"日常_签到：已领 {claimed_before}/{total_days}"
                + (f"，补领累签第{','.join(map(str, milestones_claimed))}天" if milestones_claimed else "，累签奖励已收敛"),
                payload=payload,
                claimed_before=claimed_before,
                claimed_after=claimed_before,
                total_days=total_days,
                milestones_claimed=milestones_claimed,
                business_evidence=business_evidence_before,
                settle_seconds=return_settle_seconds,
                timeout=view_timeout,
            ))

        if calendar_day > total_days:
            milestones_claimed = ()
            if fraction_available:
                milestones_claimed = yield from self._daily_signin_claim_available_milestones(
                    runtime,
                    reached_days=claimed_before,
                    total_days=total_days,
                    settle_seconds=popup_wait_seconds,
                )
            return (yield from self._daily_signin_finish_from_404(
                runtime,
                "milestone_claimed" if milestones_claimed else "outside_daily_reward_days",
                f"日常_签到：今天是{calendar_day}日，普通签到仅开放1-{total_days}日"
                + (f"；补领累签第{','.join(map(str, milestones_claimed))}天" if milestones_claimed else ""),
                payload=payload,
                claimed_before=claimed_before,
                claimed_after=claimed_before,
                total_days=total_days,
                milestones_claimed=milestones_claimed,
                business_evidence=business_evidence_before,
                settle_seconds=return_settle_seconds,
                timeout=view_timeout,
            ))
        if fraction_available and claimed_before > calendar_day:
            raise RuntimeError(
                f"日常_签到：今天是{calendar_day}日，但已领为 {claimed_before}/{total_days}"
            )
        if calendar_day in signed_days_before:
            milestones_claimed = yield from self._daily_signin_claim_available_milestones(
                runtime,
                reached_days=snapshot_before.signed_day_count,
                total_days=total_days,
                settle_seconds=popup_wait_seconds,
            )
            return (yield from self._daily_signin_finish_from_404(
                runtime,
                "milestone_claimed" if milestones_claimed else "already_claimed",
                f"日常_签到：今天第{calendar_day}天奖励已领取（{snapshot_before.signed_day_count}/{total_days}）"
                + (
                    f"；补领累签第{','.join(map(str, milestones_claimed))}天"
                    if milestones_claimed
                    else "，累签奖励已收敛"
                ),
                payload=payload,
                claimed_before=snapshot_before.signed_day_count,
                claimed_after=snapshot_before.signed_day_count,
                total_days=total_days,
                milestones_claimed=milestones_claimed,
                business_evidence=business_evidence_before,
                settle_seconds=return_settle_seconds,
                timeout=view_timeout,
            ))

        target_day = calendar_day
        date_frame = runtime.cur_frame(update=True)
        date_tokens = runtime.ocr_tokens_in_shapes(
            404,
            ["日期"],
            frame_data_url=date_frame,
            padding=0,
        )
        target_box = daily_signin_day_box(date_tokens, target_day)
        if target_box is None:
            raise RuntimeError(
                f"日常_签到：未找到第{target_day}天日期格；"
                "日期格缺失不能证明今日已领取，保留签到页现场"
            )

        before_green_ratio: float | None = None
        before_green_ratio = self._daily_signin_optional_green_ratio(
            date_frame,
            {**target_box, "day": target_day},
        )

        self._daily_signin_click_offset(runtime, 404, target_box, 2.0)
        yield from runtime.wait_action_settle(popup_wait_seconds)

        # Clicking an unclaimed day opens #250 (reward details).  The previous
        # implementation kept reading #404[已领] behind that popup, so OCR was
        # guaranteed to be empty and the task failed while visibly stuck on
        # the sign-in flow.  Claim first, then return to #404 before verifying.
        post_click_scene, _score, _frame = runtime.current_scene([250, 404], update=True)
        if post_click_scene == 250:
            self._log("action", "日常_签到：#250 奖励页点击「领取」")
            runtime.click_shape_center(250, "领取")
            yield from runtime.wait_action_settle(popup_wait_seconds)
            yield from runtime.goto_view(404)
            yield from runtime.wait_view(404, timeout=view_timeout, label="日常_签到：领奖后回到签到页 #404")

        snapshot_after = self._daily_signin_read_milestone_snapshot()
        signed_days_after = self._daily_signin_validate_snapshot(
            snapshot_after,
            label="领取后",
        )
        if (
            snapshot_after.activity_id != snapshot_before.activity_id
            or snapshot_after.turn_id != snapshot_before.turn_id
        ):
            raise RuntimeError("日常_签到：领取前后活动期次发生变化")
        signed_delta = set(signed_days_after) - set(signed_days_before)
        removed_days = set(signed_days_before) - set(signed_days_after)
        if signed_delta != {target_day} or removed_days:
            raise RuntimeError(
                "日常_签到：领取后 Runtime 已签到日期集合未产生精确目标 delta："
                f"target={target_day}, added={sorted(signed_delta)}, "
                f"removed={sorted(removed_days)}"
            )

        claimed_after = snapshot_after.signed_day_count
        refreshed_total = snapshot_after.total_days
        after_green_ratio: float | None = None
        verify_frame = runtime.cur_frame(update=True)
        verify_tokens = runtime.ocr_tokens_in_shapes(
            404,
            ["日期"],
            frame_data_url=verify_frame,
            padding=0,
        )
        verify_box = daily_signin_day_box(verify_tokens, target_day)
        if verify_box is not None:
            after_green_ratio = self._daily_signin_optional_green_ratio(
                verify_frame,
                {**verify_box, "day": target_day},
            )
        if fraction_available:
            page_claimed_after, page_total_after = yield from self._daily_signin_read_claimed_after_animation(runtime)
            if (
                page_claimed_after != claimed_after
                or page_total_after != refreshed_total
            ):
                raise RuntimeError(
                    "日常_签到：领取后页面分数与 Runtime 已签到集合不一致："
                    f"page={page_claimed_after}/{page_total_after}, "
                    f"runtime={claimed_after}/{refreshed_total}"
                )
        signed_after_fingerprint = self._daily_signin_signed_days_fingerprint(
            signed_days_after
        )
        business_evidence_after = {
            **business_evidence_before,
            "signed_days_before": list(signed_days_before),
            "signed_days": list(signed_days_after),
            "signed_days_fingerprint": signed_after_fingerprint,
            "target_membership": target_day in signed_days_after,
            "signed_days_delta": sorted(signed_delta),
            "ocr_fraction": [claimed_after, refreshed_total]
            if fraction_available
            else None,
            "green_ratio_before": before_green_ratio,
            "green_ratio_after": after_green_ratio,
        }
        self._log(
            "info",
            "日常_签到：领取后业务证据 "
            f"business_date={business_date}, effective_now={effective_now.isoformat(sep=' ')}, "
            f"target_day={target_day}, activity={snapshot_after.activity_id}, "
            f"turn={snapshot_after.turn_id}, signed_days={signed_days_after}, "
            f"fingerprint={signed_after_fingerprint}, target_membership=True, "
            f"delta={sorted(signed_delta)}, page={claimed_after}/{refreshed_total}, "
            f"green_before={before_green_ratio if before_green_ratio is not None else 'unavailable'}, "
            f"green_after={after_green_ratio if after_green_ratio is not None else 'unavailable'}",
        )
        if refreshed_total != total_days:
            raise RuntimeError(
                f"日常_签到：领取前后总天数变化 {total_days} -> {refreshed_total}"
            )
        if claimed_after != snapshot_before.signed_day_count + 1:
            raise RuntimeError(
                "日常_签到：Runtime 已签到日期数量未按精确集合 delta 递增 "
                f"{snapshot_before.signed_day_count} -> {claimed_after}"
            )

        milestones_claimed = yield from self._daily_signin_claim_available_milestones(
            runtime,
            reached_days=claimed_after,
            total_days=total_days,
            settle_seconds=popup_wait_seconds,
        )

        return (yield from self._daily_signin_finish_from_404(
            runtime,
            "claimed",
            f"日常_签到：已领取今天第{target_day}天奖励（累计 {claimed_after}/{total_days}）",
            payload=payload,
            claimed_before=claimed_before,
            claimed_after=claimed_after,
            total_days=total_days,
            milestones_claimed=milestones_claimed,
            business_evidence=business_evidence_after,
            settle_seconds=return_settle_seconds,
            timeout=view_timeout,
        ))
