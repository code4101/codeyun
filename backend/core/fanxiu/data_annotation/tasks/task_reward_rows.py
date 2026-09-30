from __future__ import annotations

"""已验证的领奖列表：点首行安全区，以 OCR 观察条目推进，不读取 Runtime。"""

import re
from decimal import Decimal
from collections.abc import Generator
from typing import Any

from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.data_annotation.ocr_spatial import query_ocr_lines


def parse_task_reward_progress(
    text: str, *, claimed_texts: tuple[str, ...] = ("已领取",),
) -> bool | None:
    """已领取为 False；进度达成为 True；读不清为 None，不能当作无奖励。"""
    normalized = re.sub(r"\s+", "", str(text or ""))
    if normalized in claimed_texts:
        return False
    # Progress labels may abbreviate both sides independently (352.63万 /
    # 393.6万). Keep the whole ROI strict so unrelated task requirement numbers
    # or floating reward text cannot authorize a click.
    scaled = re.fullmatch(
        r"(\d+(?:\.\d+)?)(万|亿)?\s*[/／|｜丨]\s*(\d+(?:\.\d+)?)(万|亿)?",
        str(text or "").strip(),
    )
    if scaled:
        units = {None: 1, "万": 10000, "亿": 100000000}
        current = Decimal(scaled[1]) * units[scaled[2]]
        target = Decimal(scaled[3]) * units[scaled[4]]
        return current >= target if target > 0 else None
    if re.search(r"[.万亿]", normalized):
        return None
    values = parse_ocr_values(text, expected_count=2)
    if values is None or values[1] <= 0:
        return None
    return values[0] >= values[1]


def claim_task_rows_by_ocr(
    context: Any, *, scene_id: int, first_row_shape: str, observer_shape: str,
    label: str, progress_shape: str | None = None,
    progress_context_shape: str | None = None,
    claimed_texts: tuple[str, ...] = ("已领取",),
    click_settle_seconds: float = 3.0,
    no_change_confirmations: int = 3, max_clicks: int = 30,
) -> Generator[Any, None, dict[str, Any]]:
    """领取置顶、领后递补的列表；目标档位和资源消耗不属于本接口。

    Task 提供安全领取区及稳定观察区。未达成行点击会跳转的页面，必须
    提供 progress_shape（只含当前进度/条件或已领取状态），每次点击前
    由 OCR 判断可领取；未提供时要求页面已证明无奖励点击无副作用。
    progress_context_shape 可提供较大识别上下文，再按 progress_shape 筛选文字。
    claimed_texts 仅填写该页面明确表示已领的文案；“已完成”不默认等于已领。
    OCR 为空或解析失败有限重读后报错，不视为完成。列表变化次数不是
    精确领奖件数；空列表需要页面适配器提供可靠终态，不能靠空 OCR 推断。
    """
    def read_claimable():
        negative_text = None
        negative_count = 0
        for attempt in range(5):
            context.clear_frame()
            frame = context.cur_frame(update=True)
            if progress_context_shape:
                lines = context.ocr_lines_in_shapes(
                    scene_id, (progress_context_shape,), padding=0,
                    frame_data_url=frame, crop=True,
                )
                lines = query_ocr_lines(lines, context.shape_box(scene_id, progress_shape))
                text = " ".join(str(line.get("text") or "") for line in lines)
            else:
                text = context.ocr_text_in_shapes(
                    scene_id, (progress_shape,), padding=0, frame_data_url=frame, crop=True,
                )
            value = parse_task_reward_progress(text, claimed_texts=claimed_texts)
            if value is None:
                # Cropping can change Paddle's recognition of small slashes and
                # units. Retry the same frame with full-image context, retaining
                # the strict progress ROI and parser before authorizing a click.
                lines = context.ocr_lines_in_shapes(
                    scene_id, (progress_shape,), padding=0,
                    frame_data_url=frame, crop=False,
                )
                lines = query_ocr_lines(lines, context.shape_box(scene_id, progress_shape))
                text = " ".join(str(line.get("text") or "") for line in lines)
                value = parse_task_reward_progress(text, claimed_texts=claimed_texts)
            if value is True:
                return True
            if value is False:
                # 领后递补会出现进度尚未填满的过渡帧；一次负判定不能
                # 结束整个列表。只有连续新帧读到相同终态才允许停止。
                normalized = re.sub(r"\s+", "", text)
                negative_count = negative_count + 1 if normalized == negative_text else 1
                negative_text = normalized
                if negative_count >= 3:
                    return False
            else:
                negative_count = 0
                negative_text = None
            if attempt < 4:
                yield from context.wait_action_settle(2.0)
        raise RuntimeError(f"{label}：任务进度未形成稳定 OCR 结论：{text!r}")

    def read_title():
        for attempt in range(3):
            context.clear_frame()
            frame = context.cur_frame(update=True)
            text = context.ocr_text_in_shapes(
                scene_id, (observer_shape,), padding=0, frame_data_url=frame, crop=True,
            )
            title = re.sub(r"\s+", "", str(text or "")).strip()
            if title:
                return title
            if attempt < 2:
                yield from context.wait_action_settle(0.4)
        raise RuntimeError(f"{label}：观察区连续 OCR 为空")

    confirmations = max(1, int(no_change_confirmations))
    limit = max(confirmations, int(max_clicks))
    title = yield from read_title()
    unchanged = advances = clicks = 0
    reason = "unchanged"
    while unchanged < confirmations:
        if progress_shape and not (yield from read_claimable()):
            reason = "no_claimable_reward"
            break
        if clicks >= limit:
            raise RuntimeError(f"{label}：{limit} 次点击内未收敛")
        yield from context.wait_scene([scene_id], wait=5)
        context.click_shape_center(scene_id, first_row_shape)
        clicks += 1
        yield from context.wait_action_settle(click_settle_seconds)
        next_title = yield from read_title()
        if next_title != title:
            title = next_title
            unchanged = 0
            advances += 1
        else:
            unchanged += 1
    if progress_shape and unchanged >= confirmations:
        if (yield from read_claimable()):
            raise RuntimeError(f"{label}：任务可领取但连续点击后未推进")
        reason = "no_claimable_reward"
    return {"reason": reason, "clicks": clicks, "detected_advances": advances,
            "unchanged_confirmations": unchanged, "final_observer": title}


__all__ = ["claim_task_rows_by_ocr", "parse_task_reward_progress"]
