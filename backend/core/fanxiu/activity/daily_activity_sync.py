from __future__ import annotations

"""Safely persist an already-built Runtime activity discovery plan.

The complete Runtime schedule is the lossless fact layer.  The dated activity
list remains a backward-compatible user-facing projection.  Static wiki/config
data only resolves identities and is never mutated here.  Discovery and
persistence remain separate so a read-only probe cannot acquire write authority
by accident.
"""

import hashlib
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from backend.core.fanxiu.catalog.inventory import (
    load_activity_list,
    save_activity_list,
)
from backend.core.fanxiu.activity.daily_activity_discovery import (
    DEFAULT_TIMEZONE,
    read_daily_activity_discovery_plan,
)
from backend.core.settings import get_settings


TRUSTED_SOURCE_KIND = "worldline_activity_runtime_memory"
DEFAULT_MAX_PLAN_AGE_SECONDS = 300.0
_AUDIT_VERSION = 1
_AUDIT_HISTORY_LIMIT = 64
_SCHEDULE_SNAPSHOT_VERSION = 1
_SYNC_LOCK = threading.RLock()


class DailyActivitySyncError(ValueError):
    """The plan or persistence result did not satisfy the sync contract."""


def get_daily_activity_sync_audit_path() -> Path:
    return (
        get_settings().data_dir
        / "fanxiu"
        / "activity-discovery"
        / "daily-activity-sync-audit.json"
    )


def get_worldline_activity_schedule_snapshot_path() -> Path:
    return (
        get_settings().data_dir
        / "fanxiu"
        / "activity-discovery"
        / "worldline-activity-schedule.json"
    )


def load_worldline_activity_schedule_snapshot(
    path: str | Path | None = None,
) -> dict[str, Any]:
    resolved = (
        Path(path).expanduser().resolve()
        if path is not None
        else get_worldline_activity_schedule_snapshot_path()
    )
    if not resolved.exists():
        return {
            "version": _SCHEDULE_SNAPSHOT_VERSION,
            "source_kind": "",
            "captured_at": "",
            "timezone": DEFAULT_TIMEZONE,
            "projection_date": "",
            "source_evidence": {},
            "occurrence_count": 0,
            "occurrences": [],
            "activity_observation_count": 0,
            "activity_observations": [],
        }
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DailyActivitySyncError(f"读取世界线活动全量快照失败：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise DailyActivitySyncError("世界线活动全量快照不是有效 JSON") from exc
    if not isinstance(payload, Mapping) or not isinstance(
        payload.get("occurrences"), list
    ):
        raise DailyActivitySyncError("世界线活动全量快照结构无效")
    result = dict(payload)
    result["occurrences"] = [
        dict(item) for item in payload["occurrences"] if isinstance(item, Mapping)
    ]
    result["occurrence_count"] = len(result["occurrences"])
    raw_observations = payload.get("activity_observations", [])
    if not isinstance(raw_observations, list):
        raise DailyActivitySyncError("世界线活动全量快照 observation 结构无效")
    result["activity_observations"] = [
        dict(item) for item in raw_observations if isinstance(item, Mapping)
    ]
    result["activity_observation_count"] = len(
        result["activity_observations"]
    )
    return result


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _occurrence_key(item: Mapping[str, Any]) -> tuple[str, int, str, str]:
    return (
        str(item.get("name") or "").strip(),
        _as_int(item.get("cross_count")) or 0,
        str(item.get("start_date") or "").strip(),
        str(item.get("end_date") or "").strip(),
    )


def _parse_captured_at(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _plan_gate_reason(
    plan: Mapping[str, Any],
    *,
    now: datetime,
    max_plan_age_seconds: float,
) -> str:
    if str(plan.get("status") or "") != "ready":
        return "Runtime 活动清单未完整加载"
    if str(plan.get("source_kind") or "") != TRUSTED_SOURCE_KIND:
        return "同步计划不是由受信任的 Runtime 内存清单生成"
    captured_at = _parse_captured_at(plan.get("captured_at"))
    if captured_at is None:
        return "同步计划缺少带时区的 Runtime 捕获时间"
    if now.tzinfo is None:
        raise DailyActivitySyncError("同步时钟必须带时区")
    age_seconds = (now - captured_at.astimezone(now.tzinfo)).total_seconds()
    if age_seconds < -5:
        return "Runtime 捕获时间晚于当前时间"
    if age_seconds > max(0.0, float(max_plan_age_seconds)):
        return "Runtime 同步计划已经过期"
    return ""


def _validated_proposal(operation: Mapping[str, Any]) -> dict[str, Any] | None:
    if str(operation.get("action") or "") != "propose_create":
        return None
    occurrence = operation.get("occurrence")
    proposal = operation.get("proposed_occurrence")
    if not isinstance(occurrence, Mapping) or not isinstance(proposal, Mapping):
        return None
    if not occurrence.get("identity_complete"):
        return None
    if str(occurrence.get("catalog_status") or "") != "known":
        return None
    if (_as_int(occurrence.get("schedule_id")) or 0) <= 0:
        return None
    normalized = {
        "id": str(proposal.get("id") or "").strip(),
        "name": str(proposal.get("name") or "").strip(),
        "cross_count": _as_int(proposal.get("cross_count")) or 0,
        "start_date": str(proposal.get("start_date") or "").strip(),
        "end_date": str(proposal.get("end_date") or "").strip(),
    }
    if (
        not normalized["id"]
        or not normalized["name"]
        or not normalized["start_date"]
        or not normalized["end_date"]
        or _occurrence_key(normalized)
        != (
            str(occurrence.get("name") or "").strip(),
            _as_int(occurrence.get("cross_count")) or 0,
            str(occurrence.get("start_date") or "").strip(),
            str(occurrence.get("end_date") or "").strip(),
        )
    ):
        return None
    return normalized


def _review_record(
    operation: Mapping[str, Any],
    *,
    action: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    occurrence = dict(operation.get("occurrence") or {})
    return {
        "action": action or str(operation.get("action") or "review"),
        "reason": reason or str(operation.get("reason") or "需要人工复核"),
        "activity_id": _as_int(occurrence.get("activity_id")),
        "schedule_id": _as_int(occurrence.get("schedule_id")),
        "runtime_ids": list(occurrence.get("runtime_ids") or []),
        "name": str(occurrence.get("display_name") or occurrence.get("name") or ""),
        "cross_count": _as_int(occurrence.get("cross_count")) or 0,
        "start_date": str(occurrence.get("start_date") or ""),
        "end_date": str(occurrence.get("end_date") or ""),
        "raw_runtime": dict(occurrence.get("raw") or {}),
    }


def _reconcile_plan(
    plan: Mapping[str, Any],
    current_items: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    current = [dict(item) for item in current_items]
    exact = {_occurrence_key(item): item for item in current}
    by_id = {str(item.get("id") or "").strip(): item for item in current}
    by_name_dates: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in current:
        key = _occurrence_key(item)
        by_name_dates.setdefault((key[0], key[2], key[3]), []).append(item)

    creates: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    noop_count = 0
    for operation in plan.get("operations") or []:
        if not isinstance(operation, Mapping):
            continue
        action = str(operation.get("action") or "")
        if action == "noop":
            noop_count += 1
            continue
        if action in {"review_unknown_identity", "review_scope_conflict"}:
            reviews.append(_review_record(operation))
            continue
        if action != "propose_create":
            continue
        proposal = _validated_proposal(operation)
        if proposal is None:
            reviews.append(
                _review_record(
                    operation,
                    action="review_invalid_proposal",
                    reason="创建提案与已验证 Runtime 身份不一致",
                )
            )
            continue
        key = _occurrence_key(proposal)
        if key in exact:
            noop_count += 1
            continue
        same_name_dates = by_name_dates.get((key[0], key[2], key[3]), [])
        if same_name_dates:
            reviews.append(
                _review_record(
                    operation,
                    action="review_scope_conflict",
                    reason="写入前复核发现同名同期活动的跨数不同",
                )
            )
            continue
        same_id = by_id.get(proposal["id"])
        if same_id is not None and _occurrence_key(same_id) != key:
            reviews.append(
                _review_record(
                    operation,
                    action="review_id_conflict",
                    reason="确定性活动实例 ID 已被另一条日期实例占用",
                )
            )
            continue
        creates.append(proposal)
        exact[key] = proposal
        by_id[proposal["id"]] = proposal
        by_name_dates.setdefault((key[0], key[2], key[3]), []).append(proposal)
    return creates, reviews, noop_count


def _receipt_id(receipt: Mapping[str, Any]) -> str:
    identity = {
        "target_date": receipt.get("target_date"),
        "source_kind": receipt.get("source_kind"),
        "captured_at": receipt.get("captured_at"),
        "created_items": receipt.get("created_items"),
        "created_sources": receipt.get("created_sources"),
        "reviews": receipt.get("reviews"),
        "occurrences": receipt.get("occurrences"),
        "activity_observations": receipt.get("activity_observations"),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _append_audit_receipt(path: Path, receipt: dict[str, Any]) -> bool:
    receipt = dict(receipt)
    receipt["receipt_id"] = _receipt_id(receipt)
    history: list[dict[str, Any]] = []
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, Mapping):
            history = [
                dict(item)
                for item in payload.get("history", [])
                if isinstance(item, Mapping)
            ]
    if history and history[-1].get("receipt_id") == receipt["receipt_id"]:
        return False
    history.append(receipt)
    history = history[-_AUDIT_HISTORY_LIMIT:]
    _atomic_write_json(
        path,
        {"version": _AUDIT_VERSION, "latest": receipt, "history": history},
    )
    return True


def _created_source_evidence(
    plan: Mapping[str, Any],
    creates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    created_ids = {str(item.get("id") or "") for item in creates}
    evidence: list[dict[str, Any]] = []
    for operation in plan.get("operations") or []:
        if not isinstance(operation, Mapping):
            continue
        proposal = operation.get("proposed_occurrence")
        if not isinstance(proposal, Mapping):
            continue
        if str(proposal.get("id") or "") not in created_ids:
            continue
        evidence.append(
            _review_record(
                operation,
                action="create",
                reason="已通过 Runtime 身份、图鉴身份和写前冲突门禁",
            )
        )
    return evidence


def _full_schedule_snapshot(plan: Mapping[str, Any]) -> dict[str, Any]:
    raw_occurrences = plan.get("occurrences")
    if isinstance(raw_occurrences, list):
        occurrences = [
            dict(occurrence)
            for occurrence in raw_occurrences
            if isinstance(occurrence, Mapping)
        ]
    else:
        # Backward compatibility for callers/tests that still build the old
        # projection-only plan shape.
        occurrences = [
            dict(occurrence)
            for operation in plan.get("operations") or []
            if isinstance(operation, Mapping)
            and isinstance((occurrence := operation.get("occurrence")), Mapping)
        ]
    raw_observations = plan.get("activity_observations")
    observations = (
        [dict(item) for item in raw_observations if isinstance(item, Mapping)]
        if isinstance(raw_observations, list)
        else []
    )
    return {
        "version": _SCHEDULE_SNAPSHOT_VERSION,
        "source_kind": str(plan.get("source_kind") or ""),
        "captured_at": str(plan.get("captured_at") or ""),
        "timezone": str(plan.get("timezone") or ""),
        "projection_date": str(plan.get("target_date") or ""),
        "source_evidence": dict(plan.get("source_evidence") or {}),
        "occurrence_count": len(occurrences),
        "occurrences": occurrences,
        "activity_observation_count": len(observations),
        "activity_observations": observations,
    }


def synchronize_daily_activity_plan(
    plan: Mapping[str, Any],
    *,
    persist: bool = False,
    max_plan_age_seconds: float = DEFAULT_MAX_PLAN_AGE_SECONDS,
    now: datetime | None = None,
    load_occurrences: Callable[[], list[dict[str, Any]]] | None = None,
    save_occurrences: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    audit_path: str | Path | None = None,
    schedule_snapshot_path: str | Path | None = None,
) -> dict[str, Any]:
    """Persist the full Runtime schedule and update the dated projection.

    ``persist=False`` is a dry run.  A complete, fresh Runtime plan and an
    explicit ``persist=True`` are both required before any file is written.
    Every Runtime occurrence is preserved in the canonical snapshot, including
    unknown identities, future rows and rows with no downstream operation.
    Unknown identities and conflicts are retained as review evidence and never
    converted into dated user-facing occurrences.
    """

    clock = now or datetime.now().astimezone()
    gate_reason = _plan_gate_reason(
        plan,
        now=clock,
        max_plan_age_seconds=max_plan_age_seconds,
    )
    base = {
        "target_date": str(plan.get("target_date") or ""),
        "source_kind": str(plan.get("source_kind") or ""),
        "captured_at": str(plan.get("captured_at") or ""),
        "source_evidence": dict(plan.get("source_evidence") or {}),
        "write_authorized": bool(persist),
        "persisted": False,
        "created_count": 0,
        "noop_count": 0,
        "review_count": 0,
        "created_items": [],
        "created_sources": [],
        "reviews": [],
        "schedule_occurrence_count": 0,
        "activity_observation_count": 0,
        "schedule_snapshot_written": False,
    }
    if gate_reason:
        return {
            **base,
            "status": "not_written",
            "reason": gate_reason,
        }

    loader = load_occurrences or load_activity_list
    saver = save_occurrences or save_activity_list
    with _SYNC_LOCK:
        latest = loader()
        creates, reviews, noop_count = _reconcile_plan(plan, latest)
        created_sources = _created_source_evidence(plan, creates)
        schedule_snapshot = _full_schedule_snapshot(plan)
        result = {
            **base,
            "status": (
                "review_required"
                if reviews and not creates
                else "planned_with_review"
                if reviews
                else "planned"
                if creates
                else "no_change"
            ),
            "reason": "已按最新活动日期清单完成写前复核",
            "created_count": len(creates),
            "noop_count": noop_count,
            "review_count": len(reviews),
            "created_items": creates,
            "created_sources": created_sources,
            "reviews": reviews,
            "schedule_occurrence_count": schedule_snapshot["occurrence_count"],
            "activity_observation_count": schedule_snapshot[
                "activity_observation_count"
            ],
        }
        if not persist:
            return result

        if creates:
            saved = saver([*latest, *creates])
            saved_keys = {_occurrence_key(item) for item in saved}
            missing = [item for item in creates if _occurrence_key(item) not in saved_keys]
            if missing:
                raise DailyActivitySyncError("活动日期清单保存后缺少已批准实例")
            result["persisted"] = True
            result["status"] = "updated_with_review" if reviews else "updated"
            result["reason"] = "已把已验证的新实例追加到活动日期清单"

        resolved_schedule_path = (
            Path(schedule_snapshot_path).expanduser().resolve()
            if schedule_snapshot_path is not None
            else get_worldline_activity_schedule_snapshot_path()
        )
        _atomic_write_json(resolved_schedule_path, schedule_snapshot)
        result["schedule_snapshot_path"] = os.fspath(resolved_schedule_path)
        result["schedule_snapshot_written"] = True

        receipt = {
            "recorded_at": clock.isoformat(timespec="seconds"),
            "target_date": result["target_date"],
            "source_kind": result["source_kind"],
            "captured_at": result["captured_at"],
            "source_evidence": result["source_evidence"],
            "status": result["status"],
            "created_items": creates,
            "created_sources": created_sources,
            "reviews": reviews,
            "noop_count": noop_count,
            "occurrences": schedule_snapshot["occurrences"],
            "activity_observations": schedule_snapshot[
                "activity_observations"
            ],
        }
        resolved_audit_path = (
            Path(audit_path).expanduser().resolve()
            if audit_path is not None
            else get_daily_activity_sync_audit_path()
        )
        result["audit_path"] = os.fspath(resolved_audit_path)
        result["audit_written"] = _append_audit_receipt(
            resolved_audit_path,
            receipt,
        )
        return result


def synchronize_daily_activities(
    *,
    persist: bool = False,
    target_date: Any = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    allow_discovery: bool = False,
    force_refresh: bool = False,
    export_root: str | Path | None = None,
    max_plan_age_seconds: float = DEFAULT_MAX_PLAN_AGE_SECONDS,
    now: datetime | None = None,
    load_occurrences: Callable[[], list[dict[str, Any]]] | None = None,
    save_occurrences: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    audit_path: str | Path | None = None,
    schedule_snapshot_path: str | Path | None = None,
    plan_reader: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read one current Runtime plan and pass it through the safe sync gate."""

    reader = plan_reader or read_daily_activity_discovery_plan
    plan = reader(
        target_date=target_date,
        timezone_name=timezone_name,
        allow_discovery=allow_discovery,
        force_refresh=force_refresh,
        export_root=export_root,
    )
    return synchronize_daily_activity_plan(
        plan,
        persist=persist,
        max_plan_age_seconds=max_plan_age_seconds,
        now=now,
        load_occurrences=load_occurrences,
        save_occurrences=save_occurrences,
        audit_path=audit_path,
        schedule_snapshot_path=schedule_snapshot_path,
    )


__all__ = [
    "DEFAULT_MAX_PLAN_AGE_SECONDS",
    "DailyActivitySyncError",
    "TRUSTED_SOURCE_KIND",
    "get_daily_activity_sync_audit_path",
    "get_worldline_activity_schedule_snapshot_path",
    "load_worldline_activity_schedule_snapshot",
    "synchronize_daily_activities",
    "synchronize_daily_activity_plan",
]
