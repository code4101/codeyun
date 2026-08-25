from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from backend.core.fanxiu.activity.exchange_event import (
    is_exchange_activity_active,
    list_exchange_activity_snapshot,
    replace_exchange_rankings,
    upsert_exchange_activity_snapshot,
)
from backend.core.fanxiu.activity.standard_observation import (
    ActivityObservationUnavailable,
    read_activity_rank_fact,
)
from backend.core.fanxiu.activity.yunmeng_rank_reward import (
    load_activity_rank_reward_tiers,
)
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.resource_ranking import (
    read_lingzhuang_huadao_snapshot,
)
from backend.models import FanxiuExchangeActivity


LINGZHUANG_HUADAO_ACTIVITY_TYPE = "lingzhuang-huadao"
YAOCHI_FLOWER_FESTIVAL_ACTIVITY_TYPE = "yaochi-flower-festival"
YUANDING_SANSHENG_ACTIVITY_TYPE = "yuanding-sansheng"
YUANDING_SANSHENG_PARENT_ACTIVITY_ID = 16045101
YUANDING_SANSHENG_PERSONAL_RANK_ID = 45105
YUANDING_SANSHENG_GROUP_RANK_ID = 45107
TALENT_PILL_ITEM_ID = 9070095


def _reward_item_count(rewards: list[Any], item_id: int) -> int:
    total = 0
    for raw_reward in rewards:
        match = re.fullmatch(r"Item\|(\d+)_(-?\d+)(?:_.*)?", str(raw_reward or ""))
        if match is not None and int(match.group(1)) == int(item_id):
            total += int(match.group(2))
    return total


def _load_config_rows(root: Path, table: str) -> list[dict[str, Any]]:
    path = root / "parsed_configs" / table / "rows.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"无法读取凡修 {table} 配置") from exc
    rows: Any = payload if isinstance(payload, list) else payload.get("rows", payload)
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list):
        raise ValueError(f"凡修 {table} 配置格式无效")
    return [row for row in rows if isinstance(row, dict)]


def resolve_yaochi_flower_activity_references(
    *,
    rank_activity_id: int,
    cross_count: int | None = None,
    export_root: str | Path | None = None,
) -> dict[str, int | None]:
    """Resolve one flower-festival instance through the game's shared template graph.

    Cross-server flower festivals declare a shared parent Activity row whose
    ``follow`` list references the personal and plane ranking activities.  The
    parent also owns the ActiveTask ladder.  A same-server instance owns its
    task ladder directly and intentionally has no plane ranking.
    """

    root = resolve_fanxiu_export_root(export_root)
    rows = _load_config_rows(root, "Activity")
    rows_by_id = {int(row.get("id") or 0): row for row in rows}
    rank_id = int(rank_activity_id)
    rank_row = rows_by_id.get(rank_id, {})
    resolved_cross_count = int(cross_count or rank_row.get("crossGroup") or 1)
    result: dict[str, int | None] = {
        "template_activity_id": rank_id,
        "task_activity_id": rank_id,
        "personal_rank_activity_id": rank_id,
        "plane_rank_activity_id": None,
    }
    if resolved_cross_count <= 1:
        return result

    parents = [
        row
        for row in rows
        if rank_id in [int(value) for value in (row.get("follow") or [])]
        and int(row.get("crossGroup") or 0) == resolved_cross_count
    ]
    if not parents:
        raise ValueError(
            f"瑶池花会 {resolved_cross_count} 跨未找到共享活动模板引用"
        )
    parent = max(
        parents,
        key=lambda row: (
            "瑶池花会" in str(row.get("name_plain") or row.get("name") or ""),
            int(row.get("id") or 0),
        ),
    )
    template_id = int(parent.get("id") or rank_id)
    result["template_activity_id"] = template_id
    result["task_activity_id"] = template_id
    for referenced_id in [int(value) for value in (parent.get("follow") or [])]:
        referenced = rows_by_id.get(referenced_id, {})
        referenced_name = str(
            referenced.get("name_plain") or referenced.get("name") or ""
        )
        if referenced_name == "位面" or int(referenced.get("baseId") or 0) == 42802:
            result["plane_rank_activity_id"] = referenced_id
        elif referenced_name == "个人" or int(referenced.get("baseId") or 0) == 42801:
            result["personal_rank_activity_id"] = referenced_id
    if result["plane_rank_activity_id"] is None:
        raise ValueError(
            f"瑶池花会 {resolved_cross_count} 跨共享模板缺少位面榜引用"
        )
    return result


def load_yaochi_flower_task_milestones(
    *,
    rank_activity_id: int,
    export_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load the complete friendship-task ladder from the game's ActiveTask table."""

    root = resolve_fanxiu_export_root(export_root)
    try:
        rows = _load_config_rows(root, "ActiveTask")
    except ValueError as exc:
        raise ValueError("无法读取瑶池花会任务配置") from exc

    milestone_groups: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict) or int(row.get("activityId") or 0) != int(rank_activity_id):
            continue
        conditions = row.get("finishCondition") or []
        target = 0
        for condition in conditions:
            match = re.fullmatch(r"NpcFlower\|(\d+)", str(condition or ""))
            if match is not None:
                target = int(match.group(1))
                break
        if target <= 0:
            continue
        rewards = row.get("reward")
        reward_tokens = rewards if isinstance(rewards, list) else []
        task_id = int(row.get("id") or 0)
        order = int(row.get("sort") or 0)
        group_key = task_id - order
        milestone_groups.setdefault(group_key, []).append(
            {
                "task_id": task_id,
                "order": order,
                "name": str(row.get("name_plain") or row.get("name") or ""),
                "target": target,
                "talent_pill_count": _reward_item_count(
                    reward_tokens,
                    TALENT_PILL_ITEM_ID,
                ),
                "must_get": str(row.get("corner_plain") or row.get("corner") or "") == "必拿",
            }
        )
    if not milestone_groups:
        raise ValueError(f"瑶池花会 {int(rank_activity_id)} 未找到友好度任务配置")
    # ActiveTask can retain multiple ladders for the same activity id. The
    # current high-tier ladder shown in game is the most complete one; mixing
    # variants would duplicate milestones and shift Tianzi-pill rewards.
    milestones = max(
        milestone_groups.values(),
        key=lambda group: (
            len(group),
            max(item["target"] for item in group),
            max(item["task_id"] for item in group),
        ),
    )
    milestones.sort(key=lambda row: (row["target"], row["order"], row["task_id"]))
    return milestones


def load_yuanding_sansheng_task_milestones(
    *,
    activity_id: int = YUANDING_SANSHENG_PARENT_ACTIVITY_ID,
    export_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load the marriage-score ladder declared by the active event template."""

    root = resolve_fanxiu_export_root(export_root)
    rows = _load_config_rows(root, "ActiveTask")
    milestones: list[dict[str, Any]] = []
    for row in rows:
        if int(row.get("activityId") or 0) != int(activity_id):
            continue
        target = 0
        for condition in row.get("finishCondition") or []:
            match = re.fullmatch(r"MarriageScore\|(\d+)", str(condition or ""))
            if match is not None:
                target = int(match.group(1))
                break
        if target <= 0:
            continue
        rewards = [str(value) for value in (row.get("reward") or [])]
        milestones.append(
            {
                "task_id": int(row.get("id") or 0),
                "order": int(row.get("sort") or 0),
                "name": str(row.get("name_plain") or row.get("name") or ""),
                "target": target,
                "talent_pill_count": _reward_item_count(rewards, TALENT_PILL_ITEM_ID),
                "must_get": str(row.get("corner_plain") or row.get("corner") or "") == "必拿",
                "rewards": rewards,
            }
        )
    if not milestones:
        raise ValueError(f"缘定三生 {int(activity_id)} 未找到联姻评分任务配置")
    milestones.sort(key=lambda row: (row["target"], row["order"], row["task_id"]))
    return milestones


def ensure_yuanding_sansheng_activity(session: Session) -> str:
    """Project the current worldline instance into the resource-ranking store."""

    from backend.models import FanxiuPacketBusinessRecord

    record = session.exec(
        select(FanxiuPacketBusinessRecord)
        .where(
            FanxiuPacketBusinessRecord.domain == "worldline_activity",
            FanxiuPacketBusinessRecord.entity_id == str(YUANDING_SANSHENG_PARENT_ACTIVITY_ID),
        )
        .order_by(FanxiuPacketBusinessRecord.captured_at.desc())
    ).first()
    if record is None:
        raise ValueError("尚未采集到缘定三生活动实例")
    payload = dict(record.payload or {})
    item = payload.get("item") if isinstance(payload.get("item"), dict) else payload

    def _event_date(text_key: str, timestamp_key: str) -> str:
        raw_text = str(item.get(text_key) or "").strip()
        if raw_text:
            return raw_text[:10]
        timestamp = int(item.get(timestamp_key) or 0)
        if timestamp <= 0:
            raise ValueError("缘定三生活动时间事实不完整")
        return datetime.fromtimestamp(timestamp / 1000).astimezone().date().isoformat()

    start_date = _event_date("startTimeText", "startTime")
    end_date = _event_date("endTimeText", "endTime")
    # The schedule packet exposes both a matchmaking group code (crossGroup)
    # and the actual number of participating servers.  The latter is what the
    # game's user-facing "跨服[n]" label describes for this instance.
    cross_count = int(item.get("serverCount") or item.get("crossGroup") or 1)
    evidence = {
        "parent_activity_id": YUANDING_SANSHENG_PARENT_ACTIVITY_ID,
        "personal_rank_activity_id": YUANDING_SANSHENG_PERSONAL_RANK_ID,
        "group_rank_activity_id": YUANDING_SANSHENG_GROUP_RANK_ID,
        "cross_group": int(item.get("crossGroup") or 0),
        "server_ids": list(item.get("serverIds") or []),
        "world_level": int(item.get("avgWorldLevel") or 0),
        "schedule_id": item.get("scheduleId"),
        "worldline": item,
    }
    return upsert_exchange_activity_snapshot(
        session,
        {
            "activity_type": YUANDING_SANSHENG_ACTIVITY_TYPE,
            "cross_count": cross_count,
            "start_date": start_date,
            "end_date": end_date,
            "game_rank_activity_id": YUANDING_SANSHENG_PERSONAL_RANK_ID,
            "currency_name": "联姻评分",
            "captured_at": record.captured_at,
            "source_kind": "standard_runtime_facts",
            "resource_strategy": {
                "score_metric": "双方弟子的联姻总评分",
                "entry": "仙府 → 弟子 → 联姻",
                "automation": "自动联姻",
                "automation_filters": [
                    "好友/仙盟/全体",
                    "招亲池",
                    "对方最低评分",
                    "己方评分上限",
                    "优先评分相近（相差 20% 内）",
                    "本次联姻数量",
                ],
            },
            "evidence": evidence,
        },
    )


def _yuanding_rank_rows(rank: dict[str, Any], *, scope: str) -> list[dict[str, Any]]:
    size = int(rank.get("rank_list_size") or 0)
    rows: list[dict[str, Any]] = []
    for item in rank.get("items") or []:
        item_rank = int(item.get("rank") or 0)
        if item_rank <= 0 or (size > 0 and item_rank > size):
            continue
        role_key = str(item.get("key") or item.get("id") or f"{scope}:{item_rank}")
        rows.append(
            {
                "ranking_scope": scope,
                "rank": item_rank,
                "score": int(item.get("score") or 0),
                "role_key": role_key,
                "name": str(item.get("name") or ""),
                "server_id": item.get("server_id") or item.get("id"),
                "server_name": str(item.get("server_name") or ""),
                "club_name": str(item.get("club_name") or ""),
                "is_self": item_rank == int(rank.get("rank") or 0),
                "is_reward_guard": False,
                "is_last_player": size > 0 and item_rank == size,
                "has_player": True,
            }
        )
    self_rank = int(rank.get("rank") or 0)
    self_key = str(rank.get("role_key") or rank.get("name") or "self")
    if self_rank != 0 and not any(
        row["rank"] == self_rank and row["role_key"] == self_key for row in rows
    ):
        rows.append(
            {
                "ranking_scope": scope,
                "rank": self_rank,
                "score": int(rank.get("score") or 0),
                "role_key": self_key,
                "name": str(rank.get("name") or ""),
                "server_id": rank.get("server_id"),
                "server_name": str(rank.get("server_name") or ""),
                "club_name": str(rank.get("club_name") or ""),
                "is_self": True,
                "is_reward_guard": False,
                "is_last_player": False,
                "has_player": True,
            }
        )
    return rows


def collect_and_store_yuanding_sansheng_activity(
    session: Session,
    *,
    activity_id: str | None = None,
    today: date | None = None,
) -> Any:
    """Refresh Yuanding Sansheng personal and matchmaking-group rankings."""

    current_id = ensure_yuanding_sansheng_activity(session)
    if activity_id is not None and activity_id != current_id:
        activity = session.get(FanxiuExchangeActivity, activity_id)
    else:
        activity = session.get(FanxiuExchangeActivity, current_id)
    if activity is None or activity.activity_type != YUANDING_SANSHENG_ACTIVITY_TYPE:
        raise ValueError("缘定三生活动不存在")
    current_day = today or datetime.now().astimezone().date()
    if not is_exchange_activity_active(activity, today=current_day):
        raise ValueError("缘定三生活动不在有效日期内")

    personal = read_activity_rank_fact(session, YUANDING_SANSHENG_PERSONAL_RANK_ID)
    group = read_activity_rank_fact(session, YUANDING_SANSHENG_GROUP_RANK_ID)
    rows = _yuanding_rank_rows(personal, scope="personal")
    rows.extend(_yuanding_rank_rows(group, scope="plane"))
    captured_at = max(str(personal["captured_at"]), str(group["captured_at"]))
    evidence = dict(activity.evidence or {})
    evidence.update(
        {
            "personal_rank_protocol": personal.get("protocol"),
            "personal_rank_list_size": int(personal.get("rank_list_size") or 0),
            "group_rank_protocol": group.get("protocol"),
            "group_rank_list_size": int(group.get("rank_list_size") or 0),
        }
    )
    activity.evidence = evidence
    activity.captured_at = captured_at
    activity.source_kind = "standard_runtime_facts"
    session.add(activity)
    replace_exchange_rankings(
        session,
        activity_type=YUANDING_SANSHENG_ACTIVITY_TYPE,
        activity_id=activity.id,
        rows=rows,
        captured_at=captured_at,
    )
    return list_exchange_activity_snapshot(
        session,
        activity_type=YUANDING_SANSHENG_ACTIVITY_TYPE,
        activity_id=activity.id,
    ).selected_activity


def _yaochi_runtime_context(session: Session, activity: FanxiuExchangeActivity) -> dict[str, int]:
    """Resolve reward-scope fields from the persisted worldline activity fact."""

    evidence = dict(activity.evidence or {})
    context = {
        "server_day": int(evidence.get("server_day") or 0),
        "world_level": int(evidence.get("world_level") or 0),
    }
    if all(context.values()) or activity.game_rank_activity_id is None:
        return context

    from backend.models import FanxiuPacketBusinessRecord
    from backend.core.fanxiu.activity.rank_reward_context import (
        latest_open_server_time_ms,
    )

    open_server_time = latest_open_server_time_ms(session)

    records = session.exec(
        select(FanxiuPacketBusinessRecord)
        .where(
            FanxiuPacketBusinessRecord.domain == "worldline_activity",
            FanxiuPacketBusinessRecord.entity_id == str(activity.game_rank_activity_id),
        )
        .order_by(FanxiuPacketBusinessRecord.captured_at.desc())
    ).all()
    for record in records:
        payload = record.payload if isinstance(record.payload, dict) else {}
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        if not context["server_day"]:
            start_time = int(item.get("startTime") or 0)
            if open_server_time > 0 and start_time >= open_server_time:
                context["server_day"] = int(
                    (start_time - open_server_time) // 86_400_000
                ) + 1
        if not context["world_level"]:
            context["world_level"] = int(item.get("avgWorldLevel") or 0)
        if all(context.values()):
            break
    return context


def collect_and_store_lingzhuang_huadao_activity(
    session: Session,
    *,
    activity_id: str,
    today: date | None = None,
) -> Any:
    """Refresh one active Lingzhuang Huadao instance from read-only runtime data."""

    activity = session.get(FanxiuExchangeActivity, activity_id)
    if activity is None or activity.activity_type != LINGZHUANG_HUADAO_ACTIVITY_TYPE:
        raise ValueError("灵装化道活动不存在")
    current_day = today or datetime.now().astimezone().date()
    if not is_exchange_activity_active(activity, today=current_day):
        raise ValueError("灵装化道活动不在有效日期内")

    snapshot = read_lingzhuang_huadao_snapshot()
    if not snapshot.get("ok") or not snapshot.get("complete"):
        raise ValueError(str(snapshot.get("reason") or "游戏尚未加载灵装化道榜单"))

    personal_rows = list(snapshot.get("rankings") or [])
    plane_rows = list(snapshot.get("plane_rankings") or [])
    if (
        int(snapshot.get("rank_list_size") or 0) > 0
        and int(snapshot.get("loaded_rank_count") or 0) <= 0
    ) or (
        int(snapshot.get("plane_rank_list_size") or 0) > 0
        and int(snapshot.get("plane_loaded_rank_count") or 0) <= 0
    ):
        raise ValueError("灵装化道榜单明细尚未加载完整，已保留上次快照")

    self_personal = next(
        (row for row in personal_rows if row.get("is_self")),
        None,
    )
    if self_personal is not None:
        self_server_id = self_personal.get("server_id")
        self_server_name = str(self_personal.get("server_name") or "").strip()
        for row in plane_rows:
            row_server_id = row.get("server_id")
            row_server_name = str(row.get("server_name") or row.get("name") or "").strip()
            row["is_self"] = bool(
                (self_server_id is not None and row_server_id == self_server_id)
                or (self_server_name and row_server_name == self_server_name)
            )

    captured_at = str(snapshot["captured_at"])
    rows: list[dict[str, Any]] = []
    for scope, source_rows, rank_list_size in (
        ("personal", personal_rows, snapshot.get("rank_list_size")),
        ("plane", plane_rows, snapshot.get("plane_rank_list_size")),
    ):
        for source in source_rows:
            row = dict(source)
            row["ranking_scope"] = scope
            row["raw_data"] = {
                "talent_pill_count": source.get("talent_pill_count"),
                "rank_list_size": rank_list_size,
            }
            rows.append(row)

    evidence = dict(activity.evidence or {})
    evidence.update(
        {
            "rank_list_size": int(snapshot.get("rank_list_size") or 0),
            "plane_rank_list_size": int(snapshot.get("plane_rank_list_size") or 0),
            "runtime": dict(snapshot.get("evidence") or {}),
        }
    )
    activity.evidence = evidence
    activity.source_kind = "read_only_runtime_memory"
    session.add(activity)
    replace_exchange_rankings(
        session,
        activity_type=LINGZHUANG_HUADAO_ACTIVITY_TYPE,
        activity_id=activity.id,
        rows=rows,
        captured_at=captured_at,
    )
    return list_exchange_activity_snapshot(
        session,
        activity_type=LINGZHUANG_HUADAO_ACTIVITY_TYPE,
        activity_id=activity.id,
    ).selected_activity


def collect_and_store_yaochi_flower_festival_activity(
    session: Session,
    *,
    activity_id: str,
    today: date | None = None,
) -> Any:
    """Refresh one Yaochi Flower Festival ranking from standard packet facts."""

    activity = session.get(FanxiuExchangeActivity, activity_id)
    if activity is None or activity.activity_type != YAOCHI_FLOWER_FESTIVAL_ACTIVITY_TYPE:
        raise ValueError("瑶池花会活动不存在")
    current_day = today or datetime.now().astimezone().date()
    if not is_exchange_activity_active(activity, today=current_day):
        raise ValueError("瑶池花会活动不在有效日期内")
    if activity.game_rank_activity_id is None:
        raise ValueError("瑶池花会缺少游戏榜单 ID")

    references = resolve_yaochi_flower_activity_references(
        rank_activity_id=activity.game_rank_activity_id,
        cross_count=activity.cross_count,
    )
    personal_rank_activity_id = int(
        references.get("personal_rank_activity_id") or activity.game_rank_activity_id
    )
    plane_rank_activity_id = references.get("plane_rank_activity_id")
    runtime_context = _yaochi_runtime_context(session, activity)
    reward_tiers = load_activity_rank_reward_tiers(
        rank_activity_id=personal_rank_activity_id,
        event_date=activity.start_date,
        server_day=runtime_context["server_day"] or None,
        world_level=runtime_context["world_level"] or None,
    )
    rank = read_activity_rank_fact(session, personal_rank_activity_id)
    rank_list_size = int(rank.get("rank_list_size") or 0)
    visible_items = [
        item
        for item in rank.get("items") or []
        if 0 < int(item.get("rank") or 0) <= rank_list_size
    ]
    if rank_list_size > 0 and not visible_items:
        raise ValueError("瑶池花会榜单明细尚未加载，已保留上次快照")

    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    reward_tiers_by_guard = {int(tier["rank_end"]): tier for tier in reward_tiers}
    for item in visible_items:
        item_rank = int(item.get("rank") or 0)
        role_key = str(item.get("key") or item.get("id") or item.get("name") or item_rank)
        identity = (item_rank, role_key)
        if identity in seen:
            continue
        seen.add(identity)
        tier = reward_tiers_by_guard.get(item_rank)
        rows.append(
            {
                "ranking_scope": "personal",
                "rank": item_rank,
                "score": int(item.get("score") or 0),
                "role_key": role_key,
                "name": str(item.get("name") or ""),
                "server_id": item.get("server_id"),
                "server_name": str(item.get("server_name") or ""),
                "club_name": str(item.get("club_name") or ""),
                "is_self": False,
                "is_reward_guard": tier is not None,
                "is_last_player": item_rank == rank_list_size,
                "has_player": True,
                "reward_rank_start": int(tier["rank_start"]) if tier else None,
                "reward_rank_end": int(tier["rank_end"]) if tier else None,
            }
        )

    self_rank = int(rank.get("rank") or 0)
    self_key = str(rank.get("role_key") or rank.get("name") or "self")
    matched_self = next(
        (row for row in rows if row["rank"] == self_rank and row["role_key"] == self_key),
        None,
    )
    if matched_self is not None:
        matched_self["is_self"] = True
    else:
        rows.append(
            {
                "ranking_scope": "personal",
                "rank": self_rank,
                "score": int(rank.get("score") or 0),
                "role_key": self_key,
                "name": str(rank.get("name") or ""),
                "server_id": rank.get("server_id"),
                "server_name": str(rank.get("server_name") or ""),
                "club_name": str(rank.get("club_name") or ""),
                "is_self": True,
                "is_reward_guard": False,
                "is_last_player": False,
                "has_player": True,
            }
        )

    plane_rank: dict[str, Any] | None = None
    if plane_rank_activity_id is not None:
        try:
            plane_rank = read_activity_rank_fact(session, int(plane_rank_activity_id))
        except ActivityObservationUnavailable as exc:
            raise ValueError("瑶池花会位面榜尚未加载，已保留上次快照") from exc
    if plane_rank is not None:
        plane_size = int(plane_rank.get("rank_list_size") or 0)
        plane_items = [
            item
            for item in plane_rank.get("items") or []
            if 0 < int(item.get("rank") or 0) <= plane_size
        ]
        if plane_size > 0 and not plane_items:
            raise ValueError("瑶池花会位面榜明细尚未加载，已保留上次快照")
        self_plane_rank = int(plane_rank.get("rank") or 0)
        for item in plane_items:
            item_rank = int(item.get("rank") or 0)
            server_id = item.get("server_id") or item.get("id")
            rows.append(
                {
                    "ranking_scope": "plane",
                    "rank": item_rank,
                    "score": int(item.get("score") or 0),
                    "role_key": str(item.get("key") or item.get("id") or server_id or item_rank),
                    "name": str(item.get("name") or ""),
                    "server_id": server_id,
                    "server_name": str(item.get("server_name") or item.get("name") or ""),
                    "club_name": "",
                    "is_self": item_rank == self_plane_rank,
                    "is_reward_guard": False,
                    "is_last_player": item_rank == plane_size,
                    "has_player": True,
                }
            )

    captured_at = max(
        str(rank["captured_at"]),
        str(plane_rank["captured_at"]) if plane_rank is not None else "",
    )
    evidence = dict(activity.evidence or {})
    evidence.update(
        {
            "rank_list_size": rank_list_size,
            "plane_rank_list_size": (
                int(plane_rank.get("rank_list_size") or 0)
                if plane_rank is not None
                else 0
            ),
            "rank_protocol": rank.get("protocol"),
            "rank_fact": dict(rank.get("evidence") or {}),
            "activity_references": references,
            "server_day": runtime_context["server_day"] or None,
            "world_level": runtime_context["world_level"] or None,
        }
    )
    activity.evidence = evidence
    activity.source_kind = "standard_runtime_facts"
    session.add(activity)
    replace_exchange_rankings(
        session,
        activity_type=YAOCHI_FLOWER_FESTIVAL_ACTIVITY_TYPE,
        activity_id=activity.id,
        rows=rows,
        captured_at=captured_at,
    )
    return list_exchange_activity_snapshot(
        session,
        activity_type=YAOCHI_FLOWER_FESTIVAL_ACTIVITY_TYPE,
        activity_id=activity.id,
    ).selected_activity
