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
    require_ratio_separator: bool = False,
) -> bool | None:
    """已领取为 False；达成为 True；读不清为 None。

    会跳转到任务目标的页面用 require_ratio_separator，拒绝描述数字授权点击。
    """
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
    if require_ratio_separator:
        # Navigation-capable task rows need a whole, explicit progress ratio.
        # Two incidental numbers in task descriptions cannot authorize a claim.
        return None
    values = parse_ocr_values(text, expected_count=2)
    if values is None or values[1] <= 0:
        return None
    return values[0] >= values[1]


def claim_task_rows_by_ocr(
    context: Any, *, scene_id: int, first_row_shape: str, observer_shape: str,
    label: str, progress_shape: str | None = None,
    progress_context_shape: str | None = None,
    progress_context_padding: int = 0,
    progress_ocr_options: dict[str, Any] | None = None,
    progress_ocr_padding: int = 0,
    progress_ocr_min_score: float = 0.0,
    progress_ocr_prefer_padded: bool = False,
    claimed_shape: str | None = None,
    claimed_texts: tuple[str, ...] = ("已领取",),
    require_ratio_separator: bool = False,
    claimable_confirmations: int = 1,
    click_settle_seconds: float = 3.0,
    no_change_confirmations: int = 3, max_clicks: int = 30,
) -> Generator[Any, None, dict[str, Any]]:
    """领取置顶、领后递补的列表；目标档位和资源消耗不属于本接口。

    Task 提供安全领取区及稳定观察区。未达成行点击会跳转的页面，必须
    提供 progress_shape（只含当前进度/条件或已领取状态），每次点击前
    由 OCR 判断可领取；未提供时要求页面已证明无奖励点击无副作用。
    progress_context_shape 提供行级识别上下文，再按观察区和进度区筛选文字。
    progress_context_padding 按页面实测扩展裁剪上下文；不改变进度判断或点击范围。
    claimed_texts 仅填写该页面明确表示已领的文案；“已完成”不默认等于已领。
    OCR 为空或解析失败有限重读后报错，不视为完成。列表变化次数不是
    精确领奖件数；空列表需要页面适配器提供可靠终态，不能靠空 OCR 推断。
    claimed_shape 可提供领后移动的状态徽章；仅精确匹配本页 claimed_texts
    才视为已领，并沿用连续新帧确认，不放宽数字进度区的解析。
    require_ratio_separator 与 claimable_confirmations 用于点击未达成任务会
    离场的页面：要求完整比例，并用指定数量的新帧确认相同达标进度。
    progress_ocr_prefer_padded 仅在页面实测证明带上下文的进度裁剪更可靠时
    启用；仍按原进度 ROI、分数和完整比例解析，不扩展点击授权。
    """
    def task_page_frame():
        # 活动提醒等全局弹窗会盖住列表；经场景识别取帧，由其处理已登记
        # 的中断动作，并拒绝在非本页画面上读进度或标题。
        match = yield from context.wait_scene([scene_id], wait=5, label=f"{label}：观察帧")
        if int(match.scene_id or 0) != int(scene_id):
            raise RuntimeError(f"{label}：观察帧不在任务页：#{match.scene_id}")
        return match.frame_data_url

    def read_padded_progress(frame):
        lines = context.ocr_lines_in_shapes(
            scene_id, (progress_shape,), padding=progress_ocr_padding,
            frame_data_url=frame, crop=True, options=progress_ocr_options,
        )
        lines = query_ocr_lines(lines, context.shape_box(scene_id, progress_shape))
        lines = [line for line in lines if float(line.get('score') or 0) >= progress_ocr_min_score]
        candidates = [str(line.get('text') or '').strip() for line in lines
                      if str(line.get('text') or '').strip() in claimed_texts
                      or re.fullmatch(r'\d+(?:\.\d+)?[万亿]?\s*[/／|｜丨]\s*\d+(?:\.\d+)?[万亿]?', str(line.get('text') or '').strip())]
        text = candidates[0] if len(candidates) == 1 else ' '.join(str(line.get('text') or '') for line in lines)
        value = parse_task_reward_progress(text, claimed_texts=claimed_texts, require_ratio_separator=require_ratio_separator) if len(candidates) == 1 else None
        return text, value

    def read_claimable():
        negative_text = None
        negative_count = 0
        positive_text = None
        positive_count = 0
        # 最后一档领后可能先读到两帧旧进度，再出现已领终态；
        # 留出完整的三帧终态确认窗口，期间不重复点击。
        # 全服跑马灯可连续遮挡进度条数十秒：只有可读帧消耗 7 帧
        # 结论窗口，遮挡帧另受总帧数上限约束，超限仍报错。
        readable = 0
        max_frames = 20
        for attempt in range(max_frames):
            context.clear_frame()
            frame = yield from task_page_frame()
            # Some pages replace the requirement/progress strip with a badge
            # higher in the row after claiming. Its distinct ROI must not widen
            # numeric parsing to include the task-description text.
            claimed_badge = context.ocr_text_in_shapes(
                scene_id, (claimed_shape,), padding=0, frame_data_url=frame, crop=True,
            ).strip() if claimed_shape else ""
            source = "claimed_badge"
            value = None
            if claimed_badge in claimed_texts:
                text = claimed_badge
                value = False
            elif progress_ocr_prefer_padded and progress_ocr_options is not None:
                text, value = read_padded_progress(frame)
                source = "padded_progress"
            if value is None and progress_context_shape:
                lines = context.ocr_lines_in_shapes(
                    scene_id, (progress_context_shape,), padding=progress_context_padding,
                    frame_data_url=frame, crop=True,
                )
                lines = query_ocr_lines(lines, context.shape_box(scene_id, progress_shape))
                text = " ".join(str(line.get("text") or "") for line in lines)
                value = parse_task_reward_progress(text, claimed_texts=claimed_texts, require_ratio_separator=require_ratio_separator)
                source = "row_context"
            elif value is None:
                text = context.ocr_text_in_shapes(
                    scene_id, (progress_shape,), padding=0, frame_data_url=frame, crop=True,
                )
                value = parse_task_reward_progress(text, claimed_texts=claimed_texts, require_ratio_separator=require_ratio_separator)
                source = "progress_crop"
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
                value = parse_task_reward_progress(text, claimed_texts=claimed_texts, require_ratio_separator=require_ratio_separator)
                source = "full_frame_progress"
            if value is None and progress_ocr_options is not None:
                # An adapter may tune detection for its small progress text.
                # Padding gives OCR context only; authorization stays inside
                # the original status ROI, with a strict ratio/claimed parser.
                # Padded detection can include a description touching the ROI
                # edge. Only one complete status line may authorize this read;
                # never concatenate that description with the progress ratio.
                text, value = read_padded_progress(frame)
                source = "padded_progress_fallback"
            if value is True:
                normalized = re.sub(r"\s+", "", text)
                positive_count = positive_count + 1 if normalized == positive_text else 1
                positive_text = normalized
            else:
                positive_count = 0
                positive_text = None
            if value is False:
                # 领后递补会出现进度尚未填满的过渡帧；一次负判定不能
                # 结束整个列表。只有连续新帧读到相同终态才允许停止。
                normalized = re.sub(r"\s+", "", text)
                negative_count = negative_count + 1 if normalized == negative_text else 1
                negative_text = normalized
            elif value is True:
                # 读不清的帧（如全服跑马灯遮挡进度条）不构成反证，
                # 不打断“不可领”的连续确认；只有达标读数才清零。
                negative_count = 0
                negative_text = None
            readable += value is not None
            context.runner._log(
                "detail", f"{label}：进度新帧 {attempt + 1}/{max_frames} 可读 {readable}/7 source={source} "
                f"text={text!r} value={value} positive={positive_count} negative={negative_count}",
            )
            if value is True and positive_count >= max(1, int(claimable_confirmations)):
                return True
            if value is False and negative_count >= 3:
                return False
            if readable >= 7:
                break
            if attempt < max_frames - 1:
                yield from context.wait_action_settle(2.0)
        raise RuntimeError(f"{label}：任务进度未形成稳定 OCR 结论：{text!r}")

    def read_title():
        for attempt in range(5):
            context.clear_frame()
            frame = yield from task_page_frame()
            if progress_context_shape:
                # 小字号标题单独裁剪会丢失识别上下文；沿用行区域，
                # 再按观察区筛选，避免把进度或奖励文字当作标题。
                lines = context.ocr_lines_in_shapes(
                    scene_id, (progress_context_shape,), padding=progress_context_padding,
                    frame_data_url=frame, crop=True,
                )
                lines = query_ocr_lines(lines, context.shape_box(scene_id, observer_shape))
                text = " ".join(str(line.get("text") or "") for line in lines)
            else:
                text = context.ocr_text_in_shapes(
                    scene_id, (observer_shape,), padding=0, frame_data_url=frame, crop=True,
                )
            title = re.sub(r"\s+", "", str(text or "")).strip()
            if not title:
                text = context.ocr_text_in_shapes(
                    scene_id, (observer_shape,), padding=0,
                    frame_data_url=frame, crop=False,
                )
                title = re.sub(r"\s+", "", str(text or "")).strip()
            if title:
                return title
            if attempt < 4:
                yield from context.wait_action_settle(2.0)
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
