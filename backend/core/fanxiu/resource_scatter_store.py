from __future__ import annotations

"""Persistent before/after scatter points for bounded resource actions."""

import json
import os
import threading
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Iterable

from backend.core.fanxiu.resource_target_planner import ResourceActionPoint
from backend.core.settings import get_settings


_LOCK = threading.RLock()
_SCHEMA_VERSION = 1


def default_resource_scatter_path() -> Path:
    return (
        Path(get_settings().data_dir)
        / "fanxiu"
        / "resource-planning"
        / "resource-action-points.json"
    )


def _point_from_dict(payload: dict) -> ResourceActionPoint:
    return ResourceActionPoint(
        instance_id=str(payload.get("instance_id") or ""),
        selected_pet_id=int(payload.get("selected_pet_id") or 0),
        applicable_gift_ids=tuple(int(value) for value in payload.get("applicable_gift_ids") or ()),
        action_id=str(payload.get("action_id") or ""),
        phase=str(payload.get("phase") or ""),  # type: ignore[arg-type]
        resource_id=int(payload.get("resource_id") or 0),
        quantity=int(payload.get("quantity") or 0),
        activity_progress=int(payload.get("activity_progress") or 0),
        inventory=int(payload.get("inventory") or 0),
        aptitude_values=tuple(int(value) for value in payload.get("aptitude_values") or ()),  # type: ignore[arg-type]
        captured_at=str(payload.get("captured_at") or ""),
    )


def _validate_point(point: ResourceActionPoint) -> None:
    if (
        not point.instance_id
        or point.selected_pet_id <= 0
        or not point.action_id
        or point.phase not in {"before_action", "after_action"}
        or point.resource_id <= 0
        or point.quantity <= 0
        or point.activity_progress < 0
        or point.inventory < 0
        or len(point.aptitude_values) != 5
        or any(value < 0 for value in point.aptitude_values)
    ):
        raise ValueError("资源动作散点字段不完整")
    # Constructing context_key validates the applicable gift identity.
    point.context_key


def _business_payload(point: ResourceActionPoint) -> dict:
    payload = asdict(point)
    payload.pop("captured_at", None)
    return payload


class ResourceScatterStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser().resolve() if path else default_resource_scatter_path()

    def _read_unlocked(self) -> list[ResourceActionPoint]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if int(payload.get("schema_version") or 0) != _SCHEMA_VERSION:
            raise ValueError("资源动作散点 schema_version 不受支持")
        rows = payload.get("points")
        if not isinstance(rows, list):
            raise ValueError("资源动作散点文件格式无效")
        points = [_point_from_dict(dict(row)) for row in rows if isinstance(row, dict)]
        for point in points:
            _validate_point(point)
        if len(points) != len(rows):
            raise ValueError("资源动作散点包含非对象记录")
        return points

    def _write_unlocked(self, points: Iterable[ResourceActionPoint]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "points": [asdict(point) for point in points],
        }
        temporary = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def list_points(
        self,
        *,
        instance_id: str | None = None,
        selected_pet_id: int | None = None,
    ) -> list[ResourceActionPoint]:
        with _LOCK:
            points = self._read_unlocked()
        return [
            point
            for point in points
            if (instance_id is None or point.instance_id == instance_id)
            and (selected_pet_id is None or point.selected_pet_id == selected_pet_id)
        ]

    def append(self, point: ResourceActionPoint) -> bool:
        """Persist one point; exact retries are no-ops, conflicts fail closed."""

        if not point.captured_at:
            point = replace(
                point,
                captured_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            )
        _validate_point(point)
        with _LOCK:
            points = self._read_unlocked()
            same_key = [
                row
                for row in points
                if row.instance_id == point.instance_id
                and row.action_id == point.action_id
                and row.phase == point.phase
            ]
            if same_key:
                if len(same_key) == 1 and _business_payload(same_key[0]) == _business_payload(point):
                    return False
                raise ValueError(f"资源动作散点键冲突：{point.action_id}/{point.phase}")

            action_rows = [
                row
                for row in points
                if row.instance_id == point.instance_id and row.action_id == point.action_id
            ]
            if point.phase == "after_action":
                before = next((row for row in action_rows if row.phase == "before_action"), None)
                if before is None:
                    raise ValueError(f"after_action 缺少已持久化 before_action：{point.action_id}")
                if (
                    before.context_key != point.context_key
                    or before.resource_id != point.resource_id
                    or before.quantity != point.quantity
                ):
                    raise ValueError(f"动作前后身份不一致：{point.action_id}")
                aptitude_deltas = tuple(
                    after - prior
                    for prior, after in zip(before.aptitude_values, point.aptitude_values)
                )
                if (
                    before.inventory - point.inventory != before.quantity
                    or point.activity_progress <= before.activity_progress
                    or any(delta < 0 for delta in aptitude_deltas)
                    or sum(aptitude_deltas) <= 0
                ):
                    raise ValueError(f"after_action 不是有效单调结果：{point.action_id}")
            else:
                pending = self.pending_actions(
                    points=points,
                    instance_id=point.instance_id,
                    selected_pet_id=point.selected_pet_id,
                    context_key=point.context_key,
                )
                if pending:
                    raise ValueError(f"已有未闭合动作，拒绝写入新的 before_action：{pending}")

            points.append(point)
            self._write_unlocked(points)
            return True

    @staticmethod
    def pending_actions(
        *,
        points: Iterable[ResourceActionPoint],
        instance_id: str,
        selected_pet_id: int,
        context_key: str,
    ) -> list[str]:
        phases: dict[str, set[str]] = {}
        for point in points:
            if (
                point.instance_id == instance_id
                and point.selected_pet_id == selected_pet_id
                and point.context_key == context_key
            ):
                phases.setdefault(point.action_id, set()).add(point.phase)
        return sorted(
            action_id
            for action_id, values in phases.items()
            if "before_action" in values and "after_action" not in values
        )


__all__ = ["ResourceScatterStore", "default_resource_scatter_path"]
