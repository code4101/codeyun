"""日常页审计规则：任务身份、标题与进度配对、完成判据和跨帧合并。

仅解析调用方提供的事实，不依赖执行器、资产树、Runtime 或调度状态。
"""
from __future__ import annotations

import re
from typing import Any
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.ocr_values import FULLWIDTH_DIGIT_TRANSLATION


_DAILY_AUDIT_TASK_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("daily_boss", "daily-boss", r"击败首领"),
    ("daily_dungeon", "legacy-daily-dungeon", r"通关每日副本|每日副本|副本探险"),
    ("daily_shuangxiu", "legacy-daily-shuangxiu", r"完成双人修炼|双人修炼|双修"),
    ("daily_jianling", "legacy-daily-jianling", r"淬剑试炼|剑试"),
    ("daily_lingta", "legacy-daily-lingta", r"混沌灵塔|灵塔"),
    ("daily_youli", "legacy-daily-youli", r"完成修仙传游历|修仙传游历"),
    ("daily_xianyuan", "legacy-daily-xianyuan", r"挑战仙缘"),
    ("daily_lingzu", "legacy-daily-lingzu", r"灵祖|圣雷龙"),
    ("daily_yaowang", "legacy-daily-yaowang", r"妖王来袭|妖王"),
    ("daily_yaozu", "legacy-daily-yaozu", r"妖族袭城|妖族"),
    ("daily_dongtian", "legacy-daily-dongtian", r"九曜\s*玄墨|玄墨|採炁|采炁"),
    ("daily_xianshi", "legacy-daily-xianshi", r"仙市"),
    # 以下为日常页里长期存在、但此前未映射的日常条目。只映射每日重置且
    # 每日运行的作业；周常条目（圣祖、韩立等）不在此映射，避免跨周误判。
    ("daily_lingquan", "legacy-daily-lingquan", r"宗门灵泉|灵泉"),
    ("daily_zhenxie", "daily-zhenxie", r"宗门镇邪|镇邪"),
    ("daily_lundao", "daily-lundao-seat", r"参与论道|论道"),
    ("daily_mojie_raid", "legacy-daily-mojie-raid", r"奇袭魔界"),
    ("daily_xuanhuang", "daily-xuanhuang", r"玄荒古域|玄荒"),
    ("moyu_challenge", "moyu-challenge", r"魔狱封阵|完成一次魔狱"),
)


_DAILY_AUDIT_COMPLETION_MIN_TOTAL: dict[str, int] = {
    "daily_dungeon": 6,
}


def daily_audit_task_identity(row_text: str) -> dict[str, str] | None:
    normalized = _sanitize_ocr_text(row_text)
    for task_type, task_id, pattern in _DAILY_AUDIT_TASK_PATTERNS:
        if re.search(pattern, normalized):
            return {"task_type": task_type, "task_id": task_id}
    return None


def normalize_daily_audit_title(row_text: str) -> str:
    text = _sanitize_ocr_text(row_text).translate(FULLWIDTH_DIGIT_TRANSLATION)
    # Strip the bullet misread as O/0, preserving meaningful title digits.
    return re.sub(r"^[Oo0◎。·•●○\s]+", "", text).strip()


def daily_audit_row_done(
    *,
    task_type: str,
    current: int,
    total: int,
    row_text: str,
) -> bool:
    min_total = _DAILY_AUDIT_COMPLETION_MIN_TOTAL.get(task_type)
    if min_total is not None:
        return total >= min_total and current >= total
    return current >= total


def parse_daily_audit_rows(
    lines: list[dict[str, Any]],
    list_box: dict[str, Any],
    *,
    frame_width: float,
    y_tolerance: float = 150.0,
) -> list[dict[str, Any]]:
    """只接受进度列与唯一标题配对的完整行；输入为同一帧的 OCR 和列表区域。"""
    box = list_box
    left = float(box.get("x") or 0)
    top = float(box.get("y") or 0)
    right = left + float(box.get("w") or 0)
    bottom = top + float(box.get("h") or 0)

    visible_lines: list[dict[str, Any]] = []
    for line in lines:
        text = _sanitize_ocr_text(line.get("text")).translate(FULLWIDTH_DIGIT_TRANSLATION)
        if not text:
            continue
        x = float(line.get("x") or 0)
        y = float(line.get("y") or 0)
        w = float(line.get("w") or 0)
        h = float(line.get("h") or 0)
        cx = x + w / 2
        cy = y + h / 2
        if left <= cx <= right and top <= cy <= bottom:
            next_line = dict(line)
            next_line["_text"] = text
            next_line["_cx"] = cx
            next_line["_cy"] = cy
            visible_lines.append(next_line)

    scale = float(frame_width) / 900.0
    progress_rows: list[tuple[dict[str, Any], tuple[int, int]]] = []
    for line in visible_lines:
        text = str(line.get("_text") or "")
        # Independent fraction in the proven progress column. A title
        # such as '0完成双人修炼1次' is not a progress observation.
        fraction = re.fullmatch(r"[次活关]?\s*(\d+)\s*[/／丨|｜]\s*(\d+)", text)
        if fraction and 460 * scale <= float(line["_cx"]) <= 555 * scale:
            current, total = map(int, fraction.groups())
            if 0 <= current <= total and total > 0:
                progress_rows.append((line, (current, total)))

    rows: list[dict[str, Any]] = []
    for progress_line, progress in sorted(progress_rows, key=lambda item: item[0]["_cy"]):
        center = float(progress_line["_cy"])
        title_lines = [
            line
            for line in visible_lines
            if -min(float(y_tolerance), 155.0) * scale <= float(line["_cy"]) - center <= -75 * scale
            and 395 * scale <= float(line.get("x") or 0) <= 550 * scale
            and float(line.get("y") or 0) >= top
            and not re.search(r"[:：！!]|功勋|榜单积分|经验加成|经验效率|触发|击杀.*修士", str(line["_text"]))
        ]
        if len(title_lines) != 1:
            continue
        title = normalize_daily_audit_title(str(title_lines[0]["_text"]))
        if not title or not re.search(r"[\u4e00-\u9fff]", title):
            continue
        row_text = title + " " + str(progress_line["_text"])
        current, total = progress
        identity = daily_audit_task_identity(title)
        task_type = (identity or {}).get("task_type") or ""
        rows.append({
            "title": title or row_text[:40],
            "text": row_text,
            "progress": {"current": current, "total": total},
            "done": daily_audit_row_done(task_type=task_type, current=current, total=total, row_text=row_text),
            "task_type": task_type,
            "task_id": (identity or {}).get("task_id") or "",
            "center_y": center,
            "row_complete": True,
        })
    return rows


def merge_daily_audit_rows(rows: list[dict[str, Any]], next_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for row in [*rows, *next_rows]:
        key = normalize_daily_audit_title(str(row.get("title") or ""))
        if key and (key not in by_key or bool(row.get("row_complete"))):
            by_key[key] = row
    return list(by_key.values())
