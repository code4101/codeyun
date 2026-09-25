"""调度日志的纯展示转换：稳定编号、源码展示及历史 Cell 分组。

不读取或写入 Kernel 状态，也不提交执行；HTTP 与持久化调用方共享此规则。
"""

import hashlib
import json
from typing import Any
from backend.core.fanxiu.data_annotation.models import (
    FanxiuKernelSchedulerCellLog,
    FanxiuKernelSchedulerLogEntry,
)

def log_entry_base_id(item: dict[str, Any]) -> str:
    return hashlib.sha1(
        json.dumps(
            {
                "time": item.get("time") or "",
                "kind": item.get("kind") or "",
                "scope": item.get("scope") or "",
                "item_id": item.get("item_id") or "",
                "message": item.get("message") or "",
                "action": item.get("action") or "",
                "source_file": item.get("source_file") or "",
                "source_line": item.get("source_line") or "",
                "source_expr": item.get("source_expr") or "",
                "ts": item.get("ts") or "",
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:16]

def log_entry_from_item(item: dict[str, Any], entry_id: str) -> FanxiuKernelSchedulerLogEntry:
    return FanxiuKernelSchedulerLogEntry(
        id=entry_id,
        time=str(item.get("time") or ""),
        kind=str(item.get("kind") or ""),
        scope=str(item.get("scope") or ""),
        item_id=str(item.get("item_id") or ""),
        message=str(item.get("message") or ""),
        action=str(item.get("action") or ""),
        source_file=str(item.get("source_file") or ""),
        source_path=str(item.get("source_path") or ""),
        source_line=item.get("source_line") if isinstance(item.get("source_line"), int) else None,
        source_expr=str(item.get("source_expr") or ""),
        ts=str(item.get("ts") or ""),
    )


def log_entries(items: list[dict[str, Any]]) -> list[FanxiuKernelSchedulerLogEntry]:
    """保留原始顺序；同内容的重复日志通过出现序号获得不同的稳定 ID。"""
    seen_ids: dict[str, int] = {}
    entries: list[FanxiuKernelSchedulerLogEntry] = []
    for item in items:
        base_id = log_entry_base_id(item)
        occurrence = seen_ids.get(base_id, 0)
        seen_ids[base_id] = occurrence + 1
        entries.append(log_entry_from_item(item, f"scheduler-{base_id}-{occurrence}"))
    return entries

def _cell_py_literal(value: Any) -> str:
    return repr(value)

def cell_source(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("code"), str) and payload["code"].strip():
        return payload["code"].strip()
    return f"cell_meta = {_cell_py_literal(payload)}"

def cell_display_source(source: str) -> str:
    stripped = source.strip()
    if not stripped.startswith("{"):
        return source
    try:
        payload = json.loads(stripped)
    except Exception:
        return source
    if not isinstance(payload, dict):
        return source
    return cell_source(payload)

def _historical_cell_source(title: str, entries: list[FanxiuKernelSchedulerLogEntry]) -> str:
    first = entries[0] if entries else FanxiuKernelSchedulerLogEntry()
    return (
        "# 历史运行日志回放\n"
        "# 这条 cell 来自旧运行日志，当时没有保存提交源码。\n"
        f"查看日志(scope={_cell_py_literal(first.scope)}, item_id={_cell_py_literal(first.item_id)})"
    )

def _historical_cell_title(entry: FanxiuKernelSchedulerLogEntry) -> str:
    message = entry.message.strip()
    if "启动" in message and "任务" in message:
        return message
    if entry.scope == "job":
        return "自动作业 cell"
    if entry.scope == "guard":
        return "守护 cell"
    return "运行日志 cell"

def _historical_cell_boundary(entry: FanxiuKernelSchedulerLogEntry) -> bool:
    message = entry.message
    return ("启动" in message and "任务" in message) or "作业已启动" in message or "task cell 已启动" in message or "Scheduler：启动" in message


def persisted_cell_views(status: dict[str, Any], limit: int) -> list[FanxiuKernelSchedulerCellLog]:
    response_cells: list[FanxiuKernelSchedulerCellLog] = []
    seen_cell_ids: set[str] = set()
    persisted_cells = status.get("cell_logs") if isinstance(status.get("cell_logs"), list) else []
    for item in persisted_cells:
        if not isinstance(item, dict):
            continue
        item = {**item, "source": cell_display_source(str(item.get("source") or ""))}
        try:
            cell = FanxiuKernelSchedulerCellLog.model_validate(item)
        except Exception:
            continue
        if cell.id in seen_cell_ids:
            continue
        seen_cell_ids.add(cell.id)
        response_cells.append(cell)
        if len(response_cells) >= limit:
            return response_cells

    return response_cells


def historical_cell_views(log_items: list[dict[str, Any]], existing: list[FanxiuKernelSchedulerCellLog], limit: int) -> list[FanxiuKernelSchedulerCellLog]:
    response_cells = list(existing)
    seen_cell_ids = {cell.id for cell in response_cells}
    entries = log_entries(log_items)

    cells: list[list[FanxiuKernelSchedulerLogEntry]] = []
    current: list[FanxiuKernelSchedulerLogEntry] = []
    for entry in entries:
        if current and _historical_cell_boundary(entry):
            cells.append(current)
            current = []
        current.append(entry)
    if current:
        cells.append(current)

    for group in cells[:limit]:
        first = group[0]
        last = group[-1]
        title = _historical_cell_title(first)
        cell_id = hashlib.sha1("|".join(item.id for item in group).encode("utf-8")).hexdigest()[:16]
        full_cell_id = f"cell-{cell_id}"
        if full_cell_id in seen_cell_ids:
            continue
        seen_cell_ids.add(full_cell_id)
        response_cells.append(
            FanxiuKernelSchedulerCellLog(
                id=full_cell_id,
                title=title,
                source_kind="command",
                source=_historical_cell_source(title, group),
                started_at=first.time,
                ended_at=last.time,
                entries=group,
            )
        )
        if len(response_cells) >= limit:
            break
    return response_cells
