from __future__ import annotations

"""Locate the second-day Xutian Yandi decree without consuming it.

The item panel is a local overlay on #614. Full-frame OCR misses its translucent
rows, so the task reads OCR inside the annotated panel and scrolls that panel
only. The final use transaction has not been observed during an eligible window;
this task stops at a verified item row instead of claiming that a tap consumed
one. ``preview`` permits navigation outside the business window for R&D only.
"""

from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Any, Iterator

from backend.core.fanxiu.activity.ranking_lifecycle import discover_ranking_occurrences
from backend.core.fanxiu.activity.runtime_schedule import read_fanxiu_activity_runtime_schedule
from backend.core.fanxiu.data_annotation.effective_time import job_now


XUTIAN_YANDI_TASK_TYPE = "xutian_yandi_prepare"
XUTIAN_YANDI_CUTOFF = time(21, 30)
XUTIAN_YANDI_LABEL = "炎帝战令"


def xutian_yandi_window(now: datetime, schedule: dict[str, Any]) -> dict[str, Any]:
    """Resolve the unique second-day occurrence and its 21:30 use cutoff."""

    occurrences = [
        occurrence
        for occurrence in discover_ranking_occurrences(schedule)
        if occurrence.activity_type == "xutian-palace"
        and occurrence.start_at.date() + timedelta(days=1) == now.date()
    ]
    if len(occurrences) != 1:
        return {"eligible": False, "reason": f"当日虚天殿第2天实例数={len(occurrences)}"}
    occurrence = occurrences[0]
    local_now = now.astimezone(occurrence.start_at.tzinfo)
    cutoff = datetime.combine(local_now.date(), XUTIAN_YANDI_CUTOFF, tzinfo=local_now.tzinfo)
    eligible = occurrence.start_at <= local_now < occurrence.end_at and local_now < cutoff
    return {
        "eligible": eligible,
        "reason": "窗口开放" if eligible else "已过21:30使用截止或活动未开放",
        "instance_key": occurrence.instance_key,
        "cutoff": cutoff.isoformat(timespec="seconds"),
    }


def _yandi_row(tokens: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return only a row whose ordered OCR tokens spell the target item."""

    lines: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for token in tokens:
        if isinstance(token, dict):
            lines[str(token.get("parent_line_id") or "")].append(token)
    matches = []
    for line in lines.values():
        ordered = sorted(line, key=lambda token: (int(token.get("order") or 0), float(token.get("x") or 0)))
        text = "".join(str(token.get("text") or "") for token in ordered)
        if XUTIAN_YANDI_LABEL not in text:
            continue
        xs = [float(token.get("x") or 0) for token in ordered]
        ys = [float(token.get("y") or 0) for token in ordered]
        rights = [float(token.get("x") or 0) + float(token.get("w") or 0) for token in ordered]
        bottoms = [float(token.get("y") or 0) + float(token.get("h") or 0) for token in ordered]
        matches.append({"text": text, "box": [min(xs), min(ys), max(rights), max(bottoms)]})
    if len(matches) > 1:
        raise RuntimeError(f"炎帝战令 OCR 行不唯一：{matches!r}")
    return matches[0] if matches else None


def execute_xutian_yandi_prepare_job(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: Any,
) -> Iterator[Any]:
    """Enter #614 through optional business views, then locate the decree."""

    window = xutian_yandi_window(
        job_now(),
        read_fanxiu_activity_runtime_schedule(allow_discovery=True),
    )
    preview = payload.get("preview") is True
    if not window["eligible"] and not preview:
        return {"status": "skipped", "message": window["reason"], "window": window}

    from backend.core.fanxiu.data_annotation.tasks.xutian_native_auto import (
        enter_xutian_map,
    )

    context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
    yield from context.go_scene(34)
    yield from enter_xutian_map(context)
    yield from context.wait_scene([614], wait=8.0)

    context.click_shape_center(614, "道具")
    yield from context.wait_action_settle(0.7)
    target = None
    scrolls = 0
    for scrolls in range(9):
        target = _yandi_row(context.ocr_tokens_in_shapes(614, ("道具列表",), crop=True))
        if target is not None:
            break
        if scrolls == 8 or not (yield from context.scroll_shape_content(614, "道具列表", direction="down")):
            raise RuntimeError("虚天殿道具列表未找到唯一的炎帝战令行")
    result = {
        "status": "prepared",
        "message": "炎帝战令已定位；使用与消耗确认尚待活动窗口真实验收",
        "window": window,
        "target": target,
        "scrolls": scrolls,
        "preview": preview,
    }
    if payload.get("return_world") is True:
        yield from context.go_scene(34)
        result["final_scene"] = 34
    return result
