from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation.gongfa_book_selection import (
    gongfa_category_click_point,
)
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.instrumentation.gongfa_equipment import (
    read_gongfa_equipment_book_plan_snapshot,
    read_gongfa_training_snapshot,
)
from backend.core.fanxiu.instrumentation.gongfa_priority import (
    apply_saved_gongfa_priority_to_plan,
)

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９，．潛", "0123456789,.潜")
_ORDINARY_BOOK_PATTERN = re.compile(r"(?:潜修心得|功法心得)")
_GREEN_AURA_EFFECT_PATTERN = re.compile(r"\d+周天修炼效果")

# These are same-attempt UI facts, not resumable Job progress.  Every new Job
# still starts from #34 and recomputes the live business facts from scratch.
_EXPERIENCE_WORLD_SCENE = 34
_EXPERIENCE_BOTTLE_SCENE = 20
_EXPERIENCE_TRAINING_SCENE = 405
_EXPERIENCE_BOOKS_SCENE = 406
_EXPERIENCE_RESULT_SCENE = 413
_EXPERIENCE_OPEN_BOOK_LANDINGS = (
    _EXPERIENCE_BOOKS_SCENE,
    _EXPERIENCE_RESULT_SCENE,
)
_EXPERIENCE_RESULT_CLOSE_LANDINGS = (
    _EXPERIENCE_TRAINING_SCENE,
    _EXPERIENCE_BOOKS_SCENE,
)
_EXPERIENCE_OBSERVATION_PRIORITY = (413, 408, 407, 406, 405, 414)
_EXPERIENCE_DEFAULT_VIEW_TIMEOUT_SECONDS = 18.0
_EXPERIENCE_ANIMATION_TIMEOUT_SECONDS = 60.0
_EXPERIENCE_OPEN_RECOVERY_LIMIT = 3


@dataclass(frozen=True)
class ExperienceBookGroup:
    title: str
    title_line: dict[str, Any]
    detail: str = ""
    detail_line: dict[str, Any] | None = None


class DailyExperienceProviderUnavailable(RuntimeError):
    """The read-only progression provider is unavailable; no action is safe."""


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).translate(_FULLWIDTH_DIGITS)


def _experience_title(text: str) -> str | None:
    compact = _compact_text(text)
    for exact in ("小绿瓶灵气", "潜修真悟", "修炼心得", "日常"):
        if exact in compact:
            return exact
    match = _ORDINARY_BOOK_PATTERN.search(compact)
    if match is None:
        return None
    suffix = compact[match.start():]
    suffix = re.split(r"(?:数量|拥有|修为|周天|经验[+＋]?\d)", suffix, maxsplit=1)[0]
    return suffix or match.group(0)


def group_experience_books(lines: list[dict[str, Any]]) -> list[ExperienceBookGroup]:
    """Pair each native OCR title line with its nearest aligned detail line."""

    ordered = sorted(
        (
            line
            for line in lines
            if isinstance(line, dict)
            and float(line.get("w") or 0) > 0
            and float(line.get("h") or 0) > 0
        ),
        key=lambda line: (float(line.get("y") or 0), float(line.get("x") or 0)),
    )
    anchors: list[tuple[int, str, dict[str, Any]]] = []
    for index, line in enumerate(ordered):
        title = _experience_title(str(line.get("text") or ""))
        if title is not None:
            anchors.append((index, title, line))

    result: list[ExperienceBookGroup] = []
    for anchor_index, (line_index, title, title_line) in enumerate(anchors):
        next_line_index = anchors[anchor_index + 1][0] if anchor_index + 1 < len(anchors) else len(ordered)
        title_x = float(title_line.get("x") or 0)
        title_y = float(title_line.get("y") or 0)
        title_h = float(title_line.get("h") or 0)
        detail_line: dict[str, Any] | None = None
        for candidate in ordered[line_index + 1:next_line_index]:
            text = _compact_text(candidate.get("text"))
            if not text or re.fullmatch(r"[xX×*]?\d+", text) or text == "修为":
                continue
            candidate_y = float(candidate.get("y") or 0)
            candidate_x = float(candidate.get("x") or 0)
            if candidate_y <= title_y + title_h * 0.35:
                continue
            if abs(candidate_x - title_x) > 180:
                continue
            detail_line = candidate
            break
        result.append(
            ExperienceBookGroup(
                title=title,
                title_line=title_line,
                detail=_compact_text(detail_line.get("text")) if detail_line else "",
                detail_line=detail_line,
            )
        )

    def recover_from_detail(title: str, detail_pattern: re.Pattern[str]) -> None:
        if any(group.title == title for group in result):
            return
        for detail_index, detail_line in enumerate(ordered):
            detail = _compact_text(detail_line.get("text"))
            if detail_pattern.search(detail) is None:
                continue
            if any(group.detail_line is detail_line for group in result):
                continue
            detail_x = float(detail_line.get("x") or 0)
            detail_y = float(detail_line.get("y") or 0)
            title_line = next(
                (
                    candidate
                    for candidate in reversed(ordered[:detail_index])
                    if 0 < detail_y - float(candidate.get("y") or 0) <= 96
                    and abs(float(candidate.get("x") or 0) - detail_x) <= 180
                    and _compact_text(candidate.get("text"))
                    and re.fullmatch(r"[xX×*]?\d+", _compact_text(candidate.get("text"))) is None
                    and re.search(r"[\u3400-\u9fff]", _compact_text(candidate.get("text"))) is not None
                ),
                None,
            )
            if title_line is None:
                continue
            result.append(
                ExperienceBookGroup(
                    title=title,
                    title_line=title_line,
                    detail=detail,
                    detail_line=detail_line,
                )
            )
            return

    # Full-frame OCR can miss the stylized item title while still reading its
    # plain green detail.  These details are unique business facts in #406 and
    # recover the action instead of allowing a false completion.
    recover_from_detail("潜修真悟", re.compile(r"点击购买道具"))
    recover_from_detail("小绿瓶灵气", _GREEN_AURA_EFFECT_PATTERN)
    return sorted(result, key=lambda group: float(group.title_line.get("y") or 0))


def parse_experience_amount(text: str) -> int | None:
    compact = _compact_text(text).replace(",", "")
    wan = re.search(r"(\d+(?:\.\d+)?)万", compact)
    if wan is not None:
        return int(float(wan.group(1)) * 10_000)
    values = parse_ocr_values(compact)
    return values[0] if values is not None else None


def select_daily_experience_action(
    groups: list[ExperienceBookGroup],
) -> tuple[str, ExperienceBookGroup] | None:
    for group in groups:
        if _ORDINARY_BOOK_PATTERN.search(group.title):
            return "ordinary_book", group
        if group.title == "修炼心得":
            amount = parse_experience_amount(group.detail)
            if amount is not None and amount >= 10_000:
                return "training_experience", group
    true_insight = next((group for group in groups if group.title == "潜修真悟"), None)
    if true_insight is not None:
        return "true_insight", true_insight
    # The acquisition row remains visible after the bottle has been consumed.
    # Only a fresh numeric cultivation-effect detail proves that it is usable;
    # the title alone (or "点击查看获取途径") is an exhausted catalogue row.
    green_aura = next(
        (
            group
            for group in groups
            if group.title == "小绿瓶灵气"
            and _GREEN_AURA_EFFECT_PATTERN.search(group.detail) is not None
        ),
        None,
    )
    if green_aura is not None:
        return "green_aura", green_aura
    return None


def is_daily_experience_completion_candidate(groups: list[ExperienceBookGroup]) -> bool:
    if select_daily_experience_action(groups) is not None:
        return False
    if not any(group.title == "日常" for group in groups):
        return False
    for group in groups:
        if group.title == "修炼心得" and parse_experience_amount(group.detail) is None:
            return False
    return True


class DailyExperienceTaskMixin:
    @staticmethod
    def _daily_experience_progression_snapshot() -> dict[str, Any]:
        return read_gongfa_training_snapshot()

    @staticmethod
    def _daily_experience_book_plan_snapshot() -> dict[str, Any]:
        return apply_saved_gongfa_priority_to_plan(
            read_gongfa_equipment_book_plan_snapshot()
        )

    @staticmethod
    def _daily_experience_item_point(group: ExperienceBookGroup) -> tuple[float, float]:
        line = group.title_line
        return (
            float(line.get("x") or 0) - 64.0,
            float(line.get("y") or 0) + float(line.get("h") or 0) + 17.0,
        )

    def _daily_experience_open_books(self, runtime: Any, *, timeout: float):
        """Open #406 through a bounded, same-attempt local transition loop."""

        for recovery_index in range(_EXPERIENCE_OPEN_RECOVERY_LIMIT):
            landed = yield from runtime.wait_click_then_view(
                _EXPERIENCE_TRAINING_SCENE,
                "提升",
                _EXPERIENCE_OPEN_BOOK_LANDINGS,
                timeout=timeout,
                label=(
                    "日常_经验：打开经验书，等待 #406；"
                    "允许已知遗留结算 #413 后有界清理"
                ),
            )
            landed_id = int(getattr(landed, "id", landed))
            if landed_id == _EXPERIENCE_BOOKS_SCENE:
                return
            if landed_id != _EXPERIENCE_RESULT_SCENE:
                raise RuntimeError(f"日常_经验：打开经验书落到非法场景 #{landed_id}")
            yield from self._daily_experience_close_result_to_training(
                runtime,
                timeout=timeout,
            )
        raise RuntimeError(
            "日常_经验：连续清理遗留 #413 后仍无法打开 #406；"
            f"已达到 {_EXPERIENCE_OPEN_RECOVERY_LIMIT} 次同attempt恢复上限"
        )

    @staticmethod
    def _daily_experience_observe_scene(
        runtime: Any,
        frame: Any,
        allowed_scene_ids: set[int] | frozenset[int],
    ) -> int | None:
        """Observe one known same-attempt scene using overlay-first priority."""

        for scene_id in _EXPERIENCE_OBSERVATION_PRIORITY:
            if scene_id not in allowed_scene_ids:
                continue
            matched, _score, _ = runtime.match_view(scene_id, frame_data_url=frame)
            if matched:
                return scene_id
        return None

    def _daily_experience_enter(self, runtime: Any, *, timeout: float):
        yield from runtime.wait_click(34, "进入绿瓶")
        yield from runtime.wait_view(20, timeout=timeout, label="日常_经验：等待绿瓶 #20")
        # #20 底部菜单会保留上次横向滚动位置；「修炼」固定在最左端。
        # 每轮从稳定事实重新归位，不能从遗留的后段菜单直接查找。
        for _ in range(30):
            changed = yield from runtime.scroll_shape_content(20, "菜单", direction="left")
            if not changed:
                break
        frame = runtime.cur_frame(update=True)
        runtime.click_ocr_text(
            20,
            "修炼",
            in_shapes=["菜单"],
            frame_data_url=frame,
            anchor="top_center",
            offset=(0.0, -1.0),
            offset_unit="height",
        )
        yield from runtime.wait_view(405, timeout=timeout, label="日常_经验：等待修炼页 #405")
        yield from self._daily_experience_open_books(runtime, timeout=timeout)

    def _daily_experience_reopen_books(self, runtime: Any, *, timeout: float):
        matched_405, _score, _frame = runtime.match_view(405, update=True)
        if not matched_405:
            yield from runtime.wait_view(405, timeout=timeout, label="日常_经验：等待返回修炼页 #405")
        yield from self._daily_experience_open_books(runtime, timeout=timeout)

    def _daily_experience_close_bugged_result(self, runtime: Any, *, timeout: float):
        yield from self._daily_experience_close_result_to_training(
            runtime,
            timeout=timeout,
        )
        yield from self._daily_experience_reopen_books(runtime, timeout=timeout)

    def _daily_experience_close_result_to_training(self, runtime: Any, *, timeout: float):
        """Close the known #413 overlay and stop on the proven #405 page."""

        # 游戏会显示“点击屏幕继续”，但该热区会反复生成同一结算页。
        # 被结算层覆盖的 #406「返回」固定位置只能先关闭
        # #413：真实运行中可以直接落 #405，也可以落普通 #406。
        runtime.click_shape_center(_EXPERIENCE_BOOKS_SCENE, "返回")
        landed = yield from self._daily_experience_wait_bugged_result_landing(
            runtime,
            timeout=timeout,
        )
        if landed == _EXPERIENCE_BOOKS_SCENE:
            yield from runtime.wait_click(_EXPERIENCE_BOOKS_SCENE, "返回")
        yield from self._daily_experience_wait_training_without_books(
            runtime,
            timeout=timeout,
        )

    def _daily_experience_wait_bugged_result_landing(
        self,
        runtime: Any,
        *,
        timeout: float,
    ):
        """Distinguish a real #405 landing from the ordinary #406 overlay."""

        attempts = max(2, int(max(1.0, float(timeout)) / 0.5))
        for _attempt in range(attempts):
            frame = runtime.cur_frame(update=True)
            scene_id = self._daily_experience_observe_scene(
                runtime,
                frame,
                frozenset(
                    {
                        _EXPERIENCE_RESULT_SCENE,
                        _EXPERIENCE_BOOKS_SCENE,
                        _EXPERIENCE_TRAINING_SCENE,
                    }
                ),
            )
            if scene_id in _EXPERIENCE_RESULT_CLOSE_LANDINGS:
                return scene_id
            yield from runtime.wait_action_settle(0.5)
        raise TimeoutError(
            "日常_经验：#413 结算层关闭后未落到 "
            f"{list(_EXPERIENCE_RESULT_CLOSE_LANDINGS)}"
        )

    def _daily_experience_run_breakthrough(self, runtime: Any, *, timeout: float):
        yield from runtime.wait_click(408, "提升")
        yield from runtime.wait_view(409, timeout=timeout, label="日常_经验：等待第一段升阶 #409")
        yield from runtime.wait_click(409, "升阶")
        yield from runtime.wait_view(410, timeout=timeout, label="日常_经验：等待第二段升阶 #410")
        yield from runtime.wait_click(410, "升阶")
        yield from runtime.wait_view(411, timeout=timeout, label="日常_经验：等待升阶结果 #411")
        yield from runtime.wait_click(411, "继续")
        yield from runtime.wait_view(412, timeout=timeout, label="日常_经验：等待升阶收尾 #412")
        yield from runtime.wait_click(412, "返回")
        yield from runtime.wait_view(405, timeout=timeout, label="日常_经验：等待升阶返回 #405")
        yield from self._daily_experience_open_books(runtime, timeout=timeout)

    def _daily_experience_replace_full_book(self, runtime: Any, *, timeout: float):
        """Recompute the live book plan and replace the currently full book."""

        snapshot = self._daily_experience_book_plan_snapshot()
        if snapshot.get("complete") is not True:
            raise DailyExperienceProviderUnavailable(
                "日常_经验：实时功法书清单不完整，拒绝猜测换书；"
                f"error={snapshot.get('error')!r}，evidence={snapshot.get('evidence')!r}"
            )
        yield from runtime.wait_click(405, "更换")
        yield from runtime.wait_view(439, timeout=timeout, label="日常_经验：等待选择功法书 #439")
        target = snapshot.get("next_upgradable_book")
        if not isinstance(target, dict):
            if snapshot.get("all_books_full") is True:
                raise RuntimeError("日常_经验：实时清单及其余已学习功法、心法均已满级")
            raise RuntimeError(
                "日常_经验：实时清单未能确定下一本可升级功法；"
                f"evidence={snapshot.get('evidence')!r}"
            )
        target_name = str(target.get("name") or "").strip()
        target_category = str(target.get("filter_category") or "").strip()
        if not target_name or not target_category:
            raise RuntimeError(f"日常_经验：下一本功法缺少名称或筛选分类：{target!r}")

        frame = runtime.cur_frame(update=True)
        option_tokens = runtime.ocr_tokens_in_shapes(
            439,
            ["选项"],
            frame_data_url=frame,
            crop=True,
        )
        category_x, category_y = gongfa_category_click_point(
            option_tokens,
            target_category,
        )
        runtime.click_frame_point(439, category_x, category_y)
        yield from runtime.wait_action_settle(1.0)

        match = yield from runtime.wait_ocr_text(
            439,
            target_name,
            in_shapes=("窗口",),
            timeout_seconds=max(timeout, 45.0),
        )
        if match is None:
            raise RuntimeError(
                f"日常_经验：#439「窗口」遍历后仍未找到目标功法「{target_name}」"
            )
        book_x, book_y = match.point()
        runtime.click_frame_point(439, book_x, book_y)
        yield from runtime.wait_view(440, timeout=max(timeout, 30.0), label="日常_经验：等待功法详情 #440")
        yield from runtime.wait_click(440, "修炼")
        yield from runtime.wait_view(405, timeout=timeout, label="日常_经验：等待更换功法返回 #405")
        yield from self._daily_experience_open_books(runtime, timeout=timeout)
        return {
            "book_id": target.get("book_id"),
            "name": target_name,
            "category": target_category,
            "selection_pool": target.get("selection_pool"),
        }

    def _daily_experience_route_full_role_exp(self, runtime: Any, *, timeout: float):
        """Leave the book list and preserve every already-known progression branch."""

        # 「空白」是经验书浮层自身的安全关闭热区；不能用系统返回键。
        yield from runtime.wait_click(406, "空白")
        yield from runtime.wait_action_settle(1.0)
        for _attempt in range(8):
            frame = runtime.cur_frame(update=True)
            scene_id = self._daily_experience_observe_scene(
                runtime,
                frame,
                frozenset({413, 408, 406, 405}),
            )
            if scene_id == 413:
                yield from self._daily_experience_close_bugged_result(
                    runtime,
                    timeout=timeout,
                )
                return
            if scene_id == 408:
                yield from self._daily_experience_run_breakthrough(
                    runtime,
                    timeout=timeout,
                )
                return
            if scene_id == 406:
                # #405 is the background of the #406 overlay and can also
                # score highly.  The overlay identity is authoritative.
                runtime.click_shape_center(406, "空白")
                yield from runtime.wait_action_settle(0.5)
                continue
            if scene_id == 405:
                yield from self._daily_experience_replace_full_book(
                    runtime,
                    timeout=timeout,
                )
                return
            yield from runtime.wait_action_settle(0.5)
        raise RuntimeError(
            "日常_经验：角色修为已满；返回经验书页后未识别到已有 #408 升阶分支，"
            "需要配置整套功法全满后的突破境界场景"
        )

    def _daily_experience_settle_after_long_press(self, runtime: Any, *, timeout: float):
        yield from runtime.wait_action_settle(1.0)
        for _attempt in range(12):
            frame = runtime.cur_frame(update=True)
            text = re.sub(r"\s+", "", runtime.ocr_text(frame))
            if "服用丹药" in text and "增加属性" in text and "确认" in text:
                runtime.click_ocr_text(
                    406,
                    "确认",
                    frame_data_url=frame,
                )
                yield from runtime.wait_action_settle(1.0)
                continue
            scene_id = self._daily_experience_observe_scene(
                runtime,
                frame,
                frozenset({413, 408, 407, 406, 405}),
            )
            if scene_id == 413:
                yield from self._daily_experience_close_bugged_result(
                    runtime,
                    timeout=timeout,
                )
                return False
            if scene_id == 408:
                yield from self._daily_experience_run_breakthrough(runtime, timeout=timeout)
                return True
            if scene_id == 407:
                yield from runtime.wait_click(407, "确认")
                yield from runtime.wait_action_settle(1.0)
                continue
            if scene_id == 406:
                return False
            if scene_id == 405:
                yield from self._daily_experience_open_books(runtime, timeout=timeout)
                return False
            yield from runtime.wait_action_settle(1.0)
        raise RuntimeError("日常_经验：吃经验后未回到 #406，且未识别到 #407/#408 分支")

    def _daily_experience_buy_true_insight(
        self,
        runtime: Any,
        group: ExperienceBookGroup,
        *,
        timeout: float,
        max_purchases: int,
    ):
        x, y = self._daily_experience_item_point(group)
        runtime.click_frame_point(406, x, y)
        landed = yield from runtime.wait_view(
            414,
            413,
            timeout=timeout,
            label="日常_经验：等待潜修真悟购买 #414 或直接使用 #413",
        )
        if landed.id == 413:
            yield from self._daily_experience_close_bugged_result(runtime, timeout=timeout)
            return
        for _attempt in range(max_purchases):
            frame = runtime.cur_frame(update=True)
            scene_id = self._daily_experience_observe_scene(
                runtime,
                frame,
                frozenset({413, 414}),
            )
            if scene_id == 413:
                yield from self._daily_experience_close_bugged_result(runtime, timeout=timeout)
                return
            if scene_id != 414:
                break
            before_purchase = _compact_text(runtime.ocr_text(frame))
            yield from runtime.wait_click(414, "购买")
            yield from runtime.wait_action_settle(1.0)
            after_frame = runtime.cur_frame(update=True)
            after_scene_id = self._daily_experience_observe_scene(
                runtime,
                after_frame,
                frozenset({413, 414}),
            )
            if after_scene_id == 413:
                yield from self._daily_experience_close_bugged_result(
                    runtime,
                    timeout=timeout,
                )
                return
            if after_scene_id == 414:
                after_purchase = _compact_text(runtime.ocr_text(after_frame))
                if not before_purchase or after_purchase == before_purchase:
                    # The purchase sheet is an overlay on #406.  A successful
                    # purchase can leave its OCR text unchanged, while closing
                    # the overlay consumes the owned item and lands on #413 or
                    # directly on #405.  Close it through the already-proven
                    # #406 return control before deciding whether the purchase
                    # was ineffective; never click ``购买`` a second time from
                    # an unconfirmed postcondition.
                    runtime.click_shape_center(_EXPERIENCE_BOOKS_SCENE, "返回")
                    closed = yield from runtime.wait_view(
                        _EXPERIENCE_RESULT_SCENE,
                        _EXPERIENCE_BOOKS_SCENE,
                        _EXPERIENCE_TRAINING_SCENE,
                        timeout=timeout,
                        label="日常_经验：购买文本未变化，关闭 #414 后核验落点",
                    )
                    closed_id = int(getattr(closed, "id", closed))
                    if closed_id == _EXPERIENCE_RESULT_SCENE:
                        yield from self._daily_experience_close_bugged_result(
                            runtime,
                            timeout=timeout,
                        )
                        return
                    if closed_id == _EXPERIENCE_TRAINING_SCENE:
                        yield from self._daily_experience_reopen_books(
                            runtime,
                            timeout=timeout,
                        )
                        return
                    raise RuntimeError(
                        "日常_经验：购买潜修真悟后未观察到业务文本变化；"
                        "关闭购买层仅返回 #406，拒绝再次购买"
                    )
        frame = runtime.cur_frame(update=True)
        scene_id = self._daily_experience_observe_scene(
            runtime,
            frame,
            frozenset({413, 414}),
        )
        if scene_id == 413:
            yield from self._daily_experience_close_bugged_result(runtime, timeout=timeout)
            return
        if scene_id == 414:
            raise RuntimeError(f"日常_经验：购买潜修真悟超过 {max_purchases} 次仍停留在 #414")
        yield from self._daily_experience_reopen_books(runtime, timeout=timeout)

    def _daily_experience_use_green_aura(
        self,
        runtime: Any,
        group: ExperienceBookGroup,
        *,
        timeout: float,
    ):
        x, y = self._daily_experience_item_point(group)
        runtime.click_frame_point(406, x, y)
        landed = yield from runtime.wait_view(
            413,
            405,
            timeout=max(timeout, _EXPERIENCE_ANIMATION_TIMEOUT_SECONDS),
            label="日常_经验：等待绿瓶灵气动画 #413 或直接返回 #405",
        )
        if landed.id == 405:
            yield from self._daily_experience_open_books(runtime, timeout=timeout)
            return
        yield from self._daily_experience_close_bugged_result(runtime, timeout=timeout)

    def _daily_experience_wait_training_without_books(
        self,
        runtime: Any,
        *,
        timeout: float,
    ):
        """Wait until #406 is gone, not merely until its #405 background matches."""

        attempts = max(2, int(max(1.0, float(timeout)) / 0.5))
        for _attempt in range(attempts):
            frame = runtime.cur_frame(update=True)
            matched_406, _score_406, _ = runtime.match_view(406, frame_data_url=frame)
            matched_405, _score_405, _ = runtime.match_view(405, frame_data_url=frame)
            if matched_405 and not matched_406:
                return
            yield from runtime.wait_action_settle(0.5)
        raise TimeoutError("日常_经验：#406 经验书层未关闭，拒绝点击背景 #405")

    def _daily_experience_return_world(self, runtime: Any, *, timeout: float):
        yield from runtime.wait_click(406, "返回")
        yield from self._daily_experience_wait_training_without_books(
            runtime,
            timeout=timeout,
        )
        yield from runtime.wait_click(405, "返回")
        yield from runtime.wait_view(20, timeout=timeout, label="日常_经验：等待返回绿瓶 #20")
        yield from runtime.wait_click(20, "回到世界")
        yield from runtime.wait_view(34, timeout=timeout, label="日常_经验：等待返回世界 #34")

    def _daily_experience_finish_consumables_exhausted(
        self,
        runtime: Any,
        payload: dict[str, Any],
        *,
        timeout: float,
    ):
        """Commit the proven business fact before best-effort departure."""

        task_id = str(payload.get("__scheduler_task_id") or "daily-experience")
        self._persist_scheduler_task_next_time(task_id, None)
        cleanup_warning = ""
        current_scene: int | None = _EXPERIENCE_WORLD_SCENE
        try:
            yield from self._daily_experience_return_world(runtime, timeout=timeout)
        except (InterruptedError, GeneratorExit):
            raise
        except Exception as exc:
            cleanup_warning = f"；业务已完成，但离场未完成：{type(exc).__name__}: {exc}"
            current_scene = None
            self._log(
                "warning",
                "日常_经验：消耗品清空事实已持久化；离场失败不重放消耗动作："
                f"{type(exc).__name__}: {exc}",
            )
        return {
            "result": "success",
            "outcome": "consumables_exhausted",
            "message": (
                "日常_经验：连续多帧确认无可处理经验书、潜修真悟和小绿瓶灵气"
                f"{cleanup_warning}"
            ),
            "current_scene": current_scene,
        }

    def _execute_daily_experience_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_经验资产树路径，无法执行作业")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        timeout = float(
            payload.get("view_timeout")
            or _EXPERIENCE_DEFAULT_VIEW_TIMEOUT_SECONDS
        )
        max_actions = max(1, min(200, int(payload.get("max_book_actions") or 80)))
        max_purchases = max(1, min(20, int(payload.get("max_true_insight_purchases") or 8)))
        completion_confirmation_scans = max(
            2,
            min(10, int(payload.get("completion_confirmation_scans") or 3)),
        )

        yield from self._daily_experience_enter(runtime, timeout=timeout)
        needs_progression_check = False
        completion_streak = 0
        uncertain_streak = 0
        for _action_index in range(max_actions):
            # isFullTip is an event latch: it only becomes true after an
            # experience action reports GongFaExpFull.  Reading it before an
            # action is both meaningless and can force a costly manager-root
            # rediscovery immediately after a breakthrough.
            if needs_progression_check:
                progression = self._daily_experience_progression_snapshot()
                if progression.get("complete") is not True:
                    raise DailyExperienceProviderUnavailable(
                        "日常_经验：无法读取当前功法是否已满，拒绝盲目吃经验；"
                        f"reason={progression.get('reason')!r}"
                    )
                needs_progression_check = False
                if progression.get("current_book_full") is True:
                    yield from self._daily_experience_route_full_role_exp(
                        runtime,
                        timeout=timeout,
                    )
                    continue
            frame = runtime.cur_frame(update=True)
            lines = runtime.ocr_lines_in_shapes(
                406,
                ["经验书"],
                frame_data_url=frame,
                padding=0,
            )
            groups = group_experience_books(lines)
            action = select_daily_experience_action(groups)
            cropped_groups: list[ExperienceBookGroup] = []
            if action is None:
                cropped_lines = runtime.ocr_lines_in_shapes(
                    406,
                    ["经验书"],
                    frame_data_url=frame,
                    padding=0,
                    crop=True,
                )
                cropped_groups = group_experience_books(cropped_lines)
                action = select_daily_experience_action(cropped_groups)

            if action is None:
                full_complete = is_daily_experience_completion_candidate(groups)
                crop_complete = is_daily_experience_completion_candidate(cropped_groups)
                if full_complete and crop_complete:
                    uncertain_streak = 0
                    completion_streak += 1
                    if completion_streak < completion_confirmation_scans:
                        continue
                    return (
                        yield from self._daily_experience_finish_consumables_exhausted(
                            runtime,
                            payload,
                            timeout=timeout,
                        )
                    )

                completion_streak = 0
                uncertain_streak += 1
                if uncertain_streak < completion_confirmation_scans:
                    continue
                full_summary = "；".join(
                    f"{group.title}:{group.detail or '-'}" for group in groups
                )
                crop_summary = "；".join(
                    f"{group.title}:{group.detail or '-'}" for group in cropped_groups
                )
                raise RuntimeError(
                    "日常_经验：连续多帧无法确认全部可处理项已清空；"
                    f"全帧OCR={full_summary or '空'}；区域OCR={crop_summary or '空'}"
                )

            completion_streak = 0
            uncertain_streak = 0
            action_kind, target = action
            if action_kind in {"ordinary_book", "training_experience"}:
                x, y = self._daily_experience_item_point(target)
                runtime.long_press_frame_point(406, x, y, duration=1.2)
                handled_full = yield from self._daily_experience_settle_after_long_press(
                    runtime,
                    timeout=timeout,
                )
                needs_progression_check = not handled_full
                continue

            if action_kind == "true_insight":
                yield from self._daily_experience_buy_true_insight(
                    runtime,
                    target,
                    timeout=timeout,
                    max_purchases=max_purchases,
                )
                needs_progression_check = True
                continue

            if action_kind == "green_aura":
                yield from self._daily_experience_use_green_aura(runtime, target, timeout=timeout)
                needs_progression_check = True
                continue
        raise RuntimeError(f"日常_经验：连续处理 {max_actions} 次仍未清空可处理项")
