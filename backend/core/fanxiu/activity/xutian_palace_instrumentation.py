from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping

from sqlmodel import Session, select

from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.activity_shop import (
    FanxiuActivityShopNotLoadedError,
    collect_activity_shop_runtime,
)


XUTIAN_PALACE_SHOP_BASE_ID = 80000
XUTIAN_PALACE_CURRENCY_TYPE = 12
XUTIAN_TALENT_PILL_ITEM_ID = 9070095
XUTIAN_PALACE_ACTIVITY_ROWS = "parsed_configs/Activity/rows.json"
_RELATIVE_END_DAY_RE = re.compile(r"^[A-Za-z]+\|(\d+)_")


def _runtime_currency_snapshot() -> dict[str, Any]:
    from backend.core.fanxiu.instrumentation.wallet import (
        read_wallet_currency_snapshot,
    )

    return read_wallet_currency_snapshot(
        XUTIAN_PALACE_CURRENCY_TYPE,
        allow_discovery=True,
    )


def resolve_xutian_palace_end_date(
    *,
    start_date: str,
    cross_count: int,
    activity_rows: Iterable[Mapping[str, Any]] | None = None,
) -> str:
    """Resolve the inclusive event end date from the game's Activity config."""

    start = date.fromisoformat(start_date)
    rows = activity_rows
    if rows is None:
        path = resolve_fanxiu_export_root() / XUTIAN_PALACE_ACTIVITY_ROWS
        rows = json.loads(path.read_text(encoding="utf-8"))
    end_days: set[int] = set()
    for row in rows:
        if int(row.get("baseId") or 0) != XUTIAN_PALACE_SHOP_BASE_ID:
            continue
        row_cross_count = int(row.get("crossGroup") or 1)
        if row_cross_count != int(cross_count):
            continue
        match = _RELATIVE_END_DAY_RE.match(str(row.get("endTime") or ""))
        if match:
            end_days.add(int(match.group(1)))
    if len(end_days) != 1:
        raise ValueError(
            f"虚天殿 {int(cross_count)} 跨活动时程配置不唯一：{sorted(end_days)}"
        )
    duration_days = end_days.pop()
    if duration_days < 1:
        raise ValueError("虚天殿活动持续天数无效")
    return (start + timedelta(days=duration_days - 1)).isoformat()


def read_xutian_palace_runtime_period(
    session: Session,
    *,
    cross_count: int | None = None,
) -> dict[str, Any]:
    """Read the authoritative absolute period from persisted game runtime facts."""

    def project(item: Mapping[str, Any], *, protocol: str, record_id: str,
                packet_id: str = "", captured_at: Any = "") -> dict[str, Any] | None:
        raw = item.get("raw") if isinstance(item.get("raw"), Mapping) else item
        if int(item.get("activity_type") or raw.get("activityType") or 0) != 8:
            return None
        current_cross = int(
            item.get("cross_count") or raw.get("serverCount") or 0
        )
        if cross_count is not None and current_cross != int(cross_count):
            return None
        start_ms = int(raw.get("startTime") or 0)
        end_ms = int(raw.get("endTime") or 0)
        if start_ms <= 0 or end_ms < start_ms:
            return None
        start_at = datetime.fromtimestamp(start_ms / 1000).astimezone()
        end_at = datetime.fromtimestamp(end_ms / 1000).astimezone()
        close_ms = int(raw.get("closePanelTime") or 0)
        close_at = (
            datetime.fromtimestamp(close_ms / 1000).astimezone()
            if close_ms > 0 else end_at
        )
        return {
            "start_date": start_at.date().isoformat(),
            "end_date": end_at.date().isoformat(),
            "close_panel_date": close_at.date().isoformat(),
            "start_time": start_ms,
            "end_time": end_ms,
            "close_panel_time": close_ms,
            "start_time_text": str(item.get("start_at") or start_at.strftime("%Y-%m-%d %H:%M:%S")),
            "end_time_text": str(item.get("end_at") or end_at.strftime("%Y-%m-%d %H:%M:%S")),
            "activity_id": int(item.get("activity_id") or raw.get("activityId") or 0),
            "cross_count": current_cross,
            "world_level": int(raw.get("avgWorldLevel") or 0),
            "record_id": record_id,
            "packet_id": packet_id,
            "captured_at": captured_at,
            "protocol": protocol,
        }

    # The daily occurrence snapshot is the durable projection of the strict
    # read-only worldline Runtime.  It survives a later manager unload/restart
    # and is therefore the primary materialization source for this occurrence.
    from backend.core.fanxiu.activity.daily_activity_sync import (
        load_worldline_activity_schedule_snapshot,
    )

    schedule = load_worldline_activity_schedule_snapshot()
    for occurrence in schedule.get("occurrences") or []:
        if not isinstance(occurrence, Mapping):
            continue
        period = project(
            occurrence,
            protocol=str(schedule.get("source_kind") or "worldline_activity_runtime_memory"),
            record_id=f"runtime:{occurrence.get('key') or occurrence.get('activity_id') or ''}",
            captured_at=str(schedule.get("captured_at") or ""),
        )
        if period is not None:
            today = datetime.now().astimezone().date()
            if date.fromisoformat(period["start_date"]) <= today <= date.fromisoformat(period["close_panel_date"]):
                return period

    from backend.models import FanxiuPacketBusinessRecord

    records = session.exec(
        select(FanxiuPacketBusinessRecord)
        .where(FanxiuPacketBusinessRecord.domain == "worldline_activity")
        .order_by(FanxiuPacketBusinessRecord.captured_at.desc())
    ).all()
    for record in records:
        payload = record.payload if isinstance(record.payload, dict) else {}
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        if int(item.get("activityType") or 0) != 8 and str(item.get("class") or "") != "HeavenActivityVO":
            continue
        if cross_count is not None and int(item.get("serverCount") or 0) != int(cross_count):
            continue
        start_ms = int(item.get("startTime") or 0)
        end_ms = int(item.get("endTime") or 0)
        if start_ms <= 0 or end_ms < start_ms:
            continue
        start_at = datetime.fromtimestamp(start_ms / 1000).astimezone()
        end_at = datetime.fromtimestamp(end_ms / 1000).astimezone()
        close_ms = int(item.get("closePanelTime") or 0)
        close_at = (
            datetime.fromtimestamp(close_ms / 1000).astimezone()
            if close_ms >= end_ms
            else end_at
        )
        return {
            "start_date": start_at.date().isoformat(),
            "end_date": end_at.date().isoformat(),
            "close_panel_date": close_at.date().isoformat(),
            "start_time": start_ms,
            "end_time": end_ms,
            "close_panel_time": close_ms if close_ms >= end_ms else end_ms,
            "start_time_text": str(item.get("startTimeText") or start_at.strftime("%Y-%m-%d %H:%M:%S")),
            "end_time_text": str(item.get("endTimeText") or end_at.strftime("%Y-%m-%d %H:%M:%S")),
            "activity_id": int(item.get("activityId") or 0),
            "cross_count": int(item.get("serverCount") or 0),
            "record_id": record.id,
            "packet_id": record.packet_id,
            "captured_at": record.captured_at,
            "protocol": record.protocol or "SM_ActivitySync",
        }
    label = f" {int(cross_count)} 跨" if cross_count is not None else ""
    raise ValueError(f"未找到虚天殿{label}运行时活动时程")


def ensure_xutian_palace_activity(session: Session) -> str:
    """Materialize the current Xutian occurrence before rank/shop collection."""

    from backend.core.fanxiu.activity.exchange_event import (
        upsert_exchange_activity_snapshot,
    )
    from backend.core.fanxiu.activity.rank_reward_context import (
        server_day_for_start_time,
    )
    from backend.models import FanxiuExchangeActivity

    period = read_xutian_palace_runtime_period(session)
    expected_end = resolve_xutian_palace_end_date(
        start_date=period["start_date"],
        cross_count=int(period["cross_count"]),
    )
    if expected_end != period["end_date"]:
        raise ValueError(
            "虚天殿运行时日期与静态配置不一致："
            f"runtime={period['start_date']}~{period['end_date']}, config_end={expected_end}"
        )
    existing = session.exec(
        select(FanxiuExchangeActivity).where(
            FanxiuExchangeActivity.activity_type == "xutian-palace",
            FanxiuExchangeActivity.cross_count == int(period["cross_count"]),
            FanxiuExchangeActivity.start_date == period["start_date"],
            FanxiuExchangeActivity.end_date == period["end_date"],
        )
    ).first()
    if existing is not None:
        return existing.id
    evidence = {
        "game_activity_id": period["activity_id"],
        "period_source": period["protocol"],
        "period_record_id": period["record_id"],
        "period_packet_id": period["packet_id"],
        "period_captured_at": period["captured_at"],
        "period_start_time": period["start_time"],
        "period_end_time": period["end_time"],
        "period_close_panel_time": period.get("close_panel_time"),
        "period_close_panel_date": period.get("close_panel_date"),
        "world_level": period.get("world_level", 0),
        "server_day": server_day_for_start_time(session, period["start_time"]),
        "refresh_status": {
            "currency": "pending",
            "shop": "pending",
            "rankings": "pending",
        },
    }
    return upsert_exchange_activity_snapshot(session, {
        "activity_type": "xutian-palace",
        "cross_count": int(period["cross_count"]),
        "start_date": period["start_date"],
        "end_date": period["end_date"],
        "game_rank_activity_id": 80000 + int(period["cross_count"]) * 100 + 91,
        "game_shop_base_id": XUTIAN_PALACE_SHOP_BASE_ID,
        "currency_type": XUTIAN_PALACE_CURRENCY_TYPE,
        "currency_name": "纳元晶",
        "current_currency": 0,
        "cumulative_currency": 0,
        "captured_at": str(period["captured_at"] or datetime.now().astimezone().isoformat(timespec="seconds")),
        "source_kind": str(period["protocol"]),
        "resource_strategy": {"活动方式": "原生自动挑战并积累纳元晶"},
        "evidence": evidence,
    })


def collect_xutian_palace_shop_snapshot(*, expected_cross_count: int) -> dict[str, Any]:
    """Read the complete active Xutian shop projection; never accepts stale cohorts."""

    cards = load_fanxiu_item_runtime_index(rebuild_missing=False)["cards_by_id"]
    item_names = {
        int(item_id): str(card.get("name") or "")
        for item_id, card in cards.items()
        if str(item_id).isdigit() and isinstance(card, dict)
    }
    snapshot = collect_activity_shop_runtime(
        shop_base_id=XUTIAN_PALACE_SHOP_BASE_ID,
        item_names=item_names,
        expected_currency_type=XUTIAN_PALACE_CURRENCY_TYPE,
        expected_cross_count=int(expected_cross_count),
    )
    if not snapshot.get("complete"):
        raise ValueError("虚天殿兑换宝阁运行态快照不完整")
    return snapshot


def _decorate_xutian_rank_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    reward_tiers_by_scope: Mapping[str, Iterable[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Decorate complete Xutian rows without discarding analysis detail."""

    from backend.core.fanxiu.activity.ranking_key_points import (
        project_ranking_key_points,
        rank_reward_item_count,
    )

    selected: list[dict[str, Any]] = []
    normalized_rows = [dict(row) for row in rows]
    scopes = tuple(dict.fromkeys(
        [*reward_tiers_by_scope, *(
            str(row.get("ranking_scope") or "personal") for row in normalized_rows
        )]
    ))
    for scope in scopes:
        scope_rows = [
            {**row, "ranking_scope": scope}
            for row in normalized_rows
            if str(row.get("ranking_scope") or "personal") == scope
        ]
        tiers = [dict(tier) for tier in reward_tiers_by_scope.get(scope, ())]
        selected.extend(
            project_ranking_key_points(
                scope_rows,
                reward_tiers=tiers,
                reward_count=lambda tier: rank_reward_item_count(
                    tier,
                    item_id=XUTIAN_TALENT_PILL_ITEM_ID,
                ),
                placeholder_factory=lambda start, end, pill_count, scope=scope: {
                    "ranking_scope": scope,
                    "rank": end,
                    "score": 0,
                    "role_key": f"reward-tier:{scope}:{start}-{end}",
                    "name": "",
                    "server_id": None,
                    "server_name": "",
                    "club_name": "",
                    "is_self": False,
                    "is_reward_guard": True,
                    "reward_rank_start": start,
                    "reward_rank_end": end,
                    "talent_pill_count": pill_count,
                    "score_per_talent_pill": None,
                    "has_player": False,
                    "is_last_player": False,
                },
                rank_list_size=max(
                    (int(row.get("rank_list_size") or 0) for row in scope_rows),
                    default=0,
                ),
                retain_non_key_rows=True,
                include_placeholders=False,
            )
        )
    return selected


def collect_xutian_palace_rank_snapshot(
    *,
    event_date: str,
    cross_count: int,
    server_day: int,
    personal_rank_activity_id: int | None = None,
    allow_discovery: bool = False,
) -> dict[str, Any]:
    """Read the complete loaded personal/plane models for durable storage."""

    from backend.core.fanxiu.activity.yunmeng_rank_reward import (
        load_yunmeng_rank_reward_tiers,
    )
    from backend.core.fanxiu.instrumentation.runtime_memory import (
        LuaJitReader,
        MumuProcessMemory,
    )
    from backend.core.fanxiu.instrumentation.activity_rank_runtime import (
        resolve_activity_rank_root,
    )
    from backend.core.fanxiu.instrumentation.resource_ranking import (
        _rank_data,
    )

    personal_rank_activity_id = int(
        personal_rank_activity_id
        or (80000 + int(cross_count) * 100 + 91)
    )
    plane_rank_activity_id = 80000 + int(cross_count) * 100 + 71
    from backend.core.fanxiu.activity.yunmeng_rank_reward import (
        YunmengRankRewardConfigError,
    )

    def reward_tiers(rank_activity_id: int) -> list[dict[str, Any]]:
        try:
            return load_yunmeng_rank_reward_tiers(
                rank_activity_id=rank_activity_id,
                event_date=event_date,
                server_day=server_day,
            )
        except YunmengRankRewardConfigError:
            # Full ranking rows are authoritative without reward decoration.
            # New Xutian cohorts can become live before the generated reward
            # table is available in the local static snapshot.
            return []

    personal_tiers = reward_tiers(personal_rank_activity_id)
    plane_tiers = reward_tiers(plane_rank_activity_id)
    memory = (
        MumuProcessMemory.discover()
        if allow_discovery
        else MumuProcessMemory.discover_cached(fallback_to_discovery=False)
    )
    reader = LuaJitReader(memory)
    root, cache_hit = resolve_activity_rank_root(
        memory,
        allow_discovery=allow_discovery,
    )
    personal = _rank_data(
        reader,
        root,
        personal_rank_activity_id,
        key_points_only=False,
    )
    plane = _rank_data(
        reader,
        root,
        plane_rank_activity_id,
        key_points_only=False,
    )
    rows = _decorate_xutian_rank_rows([
        {
            **row,
            "ranking_scope": "personal",
            "rank_list_size": int(personal["rank_list_size"]),
        }
        for row in personal["rankings"]
    ] + [
        {
            **row,
            "ranking_scope": "plane",
            "rank_list_size": int(plane["rank_list_size"]),
        }
        for row in plane["rankings"]
    ], reward_tiers_by_scope={"personal": personal_tiers, "plane": plane_tiers})
    return {
        "complete": True,
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rankings": rows,
        "personal_rank_list_size": int(personal["rank_list_size"]),
        "personal_loaded_rank_count": int(personal["loaded_rank_count"]),
        "plane_rank_list_size": int(plane["rank_list_size"]),
        "plane_loaded_rank_count": int(plane["loaded_rank_count"]),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "rank_root_address": f"0x{root:x}",
            "rank_root_cache_hit": cache_hit,
        },
    }


def collect_and_store_xutian_palace_rankings(
    session: Session, *, activity_id: str, allow_discovery: bool = False
) -> int:
    from backend.core.fanxiu.activity.exchange_event import replace_exchange_rankings

    from backend.models import FanxiuExchangeActivity

    activity = session.get(FanxiuExchangeActivity, activity_id)
    if activity is None or activity.activity_type != "xutian-palace":
        raise ValueError("虚天殿活动不存在")
    from backend.core.fanxiu.instrumentation.activity_rank_runtime import (
        loaded_activity_rank_ids,
        resolve_activity_rank_root,
    )
    from backend.core.fanxiu.instrumentation.runtime_memory import (
        LuaJitReader,
        MumuProcessMemory,
    )

    personal_rank_activity_id = int(activity.game_rank_activity_id or 0)
    memory = (
        MumuProcessMemory.discover()
        if allow_discovery
        else MumuProcessMemory.discover_cached(fallback_to_discovery=False)
    )
    reader = LuaJitReader(memory)
    root, _cache_hit = resolve_activity_rank_root(
        memory,
        allow_discovery=allow_discovery,
        force_refresh=True,
    )
    loaded_ids = set(loaded_activity_rank_ids(reader, root))
    if personal_rank_activity_id not in loaded_ids:
        base_id = 80000 + int(activity.cross_count) * 100
        loaded_personal_candidates = [
            candidate for candidate in (base_id + 51, base_id + 91)
            if candidate in loaded_ids
        ]
        if len(loaded_personal_candidates) != 1:
            raise ValueError(
                "虚天殿个人总榜身份不唯一："
                f"configured={personal_rank_activity_id}, loaded={sorted(loaded_personal_candidates)}"
            )
        personal_rank_activity_id = loaded_personal_candidates[0]
        activity.game_rank_activity_id = personal_rank_activity_id

    snapshot = collect_xutian_palace_rank_snapshot(
        event_date=activity.start_date,
        cross_count=activity.cross_count,
        server_day=int((activity.evidence or {}).get("server_day") or 0),
        personal_rank_activity_id=personal_rank_activity_id,
        allow_discovery=allow_discovery,
    )
    replace_exchange_rankings(
        session,
        activity_type="xutian-palace",
        activity_id=activity_id,
        rows=list(snapshot["rankings"]),
        captured_at=str(snapshot["captured_at"]),
    )
    return len(snapshot["rankings"])


def collect_and_store_xutian_palace_activity(
    session: Session,
    *,
    activity_id: str,
    today: date | None = None,
    prefer_runtime_rankings: bool = True,
    collect_runtime_wallet: bool = True,
) -> Any:
    """Refresh the active persisted activity using read-only runtime facts."""

    from backend.core.fanxiu.activity.exchange_event import (
        list_exchange_activity_snapshot,
        replace_exchange_rankings,
        store_exchange_activity_observation,
        upsert_exchange_activity_snapshot,
    )
    from backend.core.fanxiu.activity.standard_observation import (
        ActivityObservationUnavailable,
        ActivityObservationSpec,
        collect_standard_activity_observation,
        read_currency_fact,
        store_runtime_currency_fact,
    )
    from backend.core.fanxiu.activity.yunmeng_rank_reward import (
        YunmengRankRewardConfigError,
        load_yunmeng_rank_reward_tiers,
    )
    from backend.core.fanxiu.instrumentation.runtime_memory import (
        FanxiuRuntimeMemoryError,
    )
    from backend.models import FanxiuExchangeActivity, FanxiuExchangeShopItem

    activity = session.get(FanxiuExchangeActivity, activity_id)
    if activity is None or activity.activity_type != "xutian-palace":
        raise ValueError("虚天殿活动不存在")
    period = read_xutian_palace_runtime_period(
        session,
        cross_count=activity.cross_count,
    )
    current_day = today or datetime.now().astimezone().date()
    close_panel_date = date.fromisoformat(
        str(period.get("close_panel_date") or period["end_date"])
    )
    if not date.fromisoformat(period["start_date"]) <= current_day <= close_panel_date:
        raise ValueError("虚天殿活动页面已关闭")
    close_panel_time = int(period.get("close_panel_time") or 0)
    if (
        today is None
        and close_panel_time > 0
        and datetime.now().astimezone().timestamp() * 1000 > close_panel_time
    ):
        raise ValueError("虚天殿活动页面已关闭")

    expected_end_date = resolve_xutian_palace_end_date(
        start_date=period["start_date"],
        cross_count=activity.cross_count,
    )
    if expected_end_date != period["end_date"]:
        raise ValueError(
            "虚天殿运行时日期与静态配置不一致："
            f"runtime={period['start_date']}~{period['end_date']}, config_end={expected_end_date}"
        )

    rank_activity_id = int(activity.game_rank_activity_id or (80000 + activity.cross_count * 100 + 91))
    # Currency and ranking packets are already normalized into durable business
    # facts.  Do not make activity refresh depend on an ephemeral Lua manager
    # address: after a game restart that cache may be cold even though the
    # authoritative packets needed by this page have already arrived.
    currency_refresh_status = "retained"
    currency_unavailable_reason = "只读数据库物化未请求运行态钱包"
    if collect_runtime_wallet:
        try:
            runtime_currency = _runtime_currency_snapshot()
            store_runtime_currency_fact(session, runtime_currency)
            currency_refresh_status = "updated"
            currency_unavailable_reason = ""
        except (FanxiuRuntimeMemoryError, ValueError) as exc:
            currency_unavailable_reason = str(exc)
    currency = read_currency_fact(session, XUTIAN_PALACE_CURRENCY_TYPE)
    shop: dict[str, Any] | None = None
    shop_unavailable_reason = ""
    try:
        # Shop goods, prices and purchased counts are occurrence facts.  An
        # existing persisted cohort is only a fallback snapshot; it must never
        # suppress a fresh Runtime read for the current Xutian occurrence.
        shop = collect_xutian_palace_shop_snapshot(
            expected_cross_count=activity.cross_count
        )
    except FanxiuActivityShopNotLoadedError as exc:
        # A process restart can leave the wallet and packet facts available
        # before the player opens any UI that loads V_ShopCfg.  Retain the old
        # rows for display, but refresh_status keeps the budget fail-closed.
        shop_unavailable_reason = str(exc)
    observation: dict[str, Any] | None = None
    rankings_unavailable_reason = ""
    rankings_source = ""
    if prefer_runtime_rankings:
        try:
            runtime_rankings = collect_xutian_palace_rank_snapshot(
                event_date=period["start_date"],
                cross_count=activity.cross_count,
                server_day=int((activity.evidence or {}).get("server_day") or 0),
                allow_discovery=True,
            )
            observation = {
                "captured_at": runtime_rankings["captured_at"],
                "rankings": list(runtime_rankings["rankings"]),
                "evidence": dict(runtime_rankings.get("evidence") or {}),
            }
            rankings_source = "runtime_memory"
        except FanxiuRuntimeMemoryError as exc:
            # The explicit collect endpoint prefers already-loaded game state,
            # but a cold/expired manager must not hide a durable packet fact.
            rankings_unavailable_reason = f"动态榜单不可用：{exc}"
    try:
        if observation is None:
            from backend.core.fanxiu.activity.exchange_activity_registry import (
                get_exchange_activity_spec,
            )

            exchange_spec = get_exchange_activity_spec("xutian-palace")
            primary_scope_spec = next(
                scope for scope in exchange_spec.rank_scopes
                if scope.effective_role == "primary"
            )
            comparative_scopes = tuple(
                scope for scope in exchange_spec.rank_scopes
                if scope.effective_role == "comparative"
            )
            tiers = load_yunmeng_rank_reward_tiers(
                rank_activity_id=rank_activity_id,
                event_date=period["start_date"],
                server_day=int((activity.evidence or {}).get("server_day") or 0),
            )
            plane_tiers = load_yunmeng_rank_reward_tiers(
                rank_activity_id=80000 + int(activity.cross_count) * 100 + 71,
                event_date=period["start_date"],
                server_day=int((activity.evidence or {}).get("server_day") or 0),
            )
            observation = collect_standard_activity_observation(
                session,
                ActivityObservationSpec(
                    rank_activity_id=rank_activity_id,
                    currency_type=XUTIAN_PALACE_CURRENCY_TYPE,
                    related_rank_activity_ids=tuple(
                        (
                            scope.scope,
                            scope.activity_id.resolve(cross_count=activity.cross_count),
                        )
                        for scope in comparative_scopes
                    ),
                    primary_scope=primary_scope_spec.scope,
                    row_mode=primary_scope_spec.row_mode,
                ),
                reward_tiers=tiers,
            )
            observation["rankings"] = _decorate_xutian_rank_rows(
                observation["rankings"],
                reward_tiers_by_scope={
                    "personal": tiers,
                    "plane": plane_tiers,
                },
            )
            rankings_source = "standard_runtime_facts"
    except (ActivityObservationUnavailable, YunmengRankRewardConfigError) as exc:
        fallback_reason = str(exc)
        rankings_unavailable_reason = (
            f"{rankings_unavailable_reason}；标准事实不可用：{fallback_reason}"
            if rankings_unavailable_reason
            else fallback_reason
        )

    captured_at = str(
        observation["captured_at"] if observation is not None else currency["captured_at"]
    )
    evidence = dict(activity.evidence or {})
    if shop is not None:
        evidence.update(dict(shop.get("evidence") or {}))
        evidence["shop_snapshot_captured_at"] = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
    evidence.update(
        {
            "period_source": period["protocol"],
            "period_record_id": period["record_id"],
            "period_packet_id": period["packet_id"],
            "period_captured_at": period["captured_at"],
            "period_start_time": period["start_time"],
            "period_end_time": period["end_time"],
            "period_close_panel_time": period.get("close_panel_time"),
            "period_close_panel_date": str(
                period.get("close_panel_date") or period["end_date"]
            ),
            "period_validation": "Activity.endTime matched",
            "refresh_status": {
                "currency": currency_refresh_status,
                "currency_captured_at": str(currency["captured_at"]),
                "currency_reason": currency_unavailable_reason,
                "currency_stale": currency_refresh_status != "updated",
                "shop": "updated" if shop is not None else "retained",
                "shop_reason": shop_unavailable_reason,
                "rankings": "updated" if observation is not None else "retained",
                "rankings_reason": rankings_unavailable_reason,
                "rankings_source": rankings_source,
            },
        }
    )
    payload: dict[str, Any] = {
        "activity_type": "xutian-palace",
        "cross_count": activity.cross_count,
        "start_date": period["start_date"],
        "end_date": period["end_date"],
        "game_rank_activity_id": rank_activity_id,
        "game_shop_base_id": int(
            shop["shop_base_id"]
            if shop is not None
            else (activity.game_shop_base_id or XUTIAN_PALACE_SHOP_BASE_ID)
        ),
        "currency_type": XUTIAN_PALACE_CURRENCY_TYPE,
        "currency_name": activity.currency_name,
        "current_currency": int(currency["exchange_currency"]),
        "cumulative_currency": int(currency["cumulative_currency"]),
        "captured_at": captured_at,
        "source_kind": "read_only_runtime_facts",
        "resource_strategy": dict(activity.resource_strategy or {}),
        "evidence": evidence,
    }
    if shop is not None:
        payload["shop_items"] = list(shop["items"])
        payload["expected_shop_item_count"] = int(shop["active_shop_item_count"])

    persisted_activity_id = upsert_exchange_activity_snapshot(session, payload)
    if observation is not None:
        replace_exchange_rankings(
            session,
            activity_type="xutian-palace",
            activity_id=persisted_activity_id,
            rows=list(observation["rankings"]),
            captured_at=captured_at,
        )
    persisted_activity = session.get(FanxiuExchangeActivity, persisted_activity_id)
    if persisted_activity is None:
        raise RuntimeError("虚天殿活动快照写入后无法回读")
    persisted_shop_rows = session.exec(
        select(FanxiuExchangeShopItem)
        .where(FanxiuExchangeShopItem.activity_id == persisted_activity_id)
        .order_by(FanxiuExchangeShopItem.source_order)
    ).all()
    store_exchange_activity_observation(
        session,
        activity=persisted_activity,
        captured_at=captured_at,
        current_day=current_day,
        current_currency=int(currency["exchange_currency"]),
        cumulative_currency=int(currency["cumulative_currency"]),
        shop_status="updated" if shop is not None else "retained",
        rankings_status="updated" if observation is not None else "retained",
        payload={
            "period": {
                "activity_id": int(period["activity_id"]),
                "cross_count": int(period["cross_count"]),
                "start_date": period["start_date"],
                "end_date": period["end_date"],
                "close_panel_date": str(
                    period.get("close_panel_date") or period["end_date"]
                ),
            },
            "currency": dict(currency),
            "shop_snapshot_captured_at": str(
                evidence.get("shop_snapshot_captured_at") or ""
            ),
            "shop_items": [
                {
                    "goods_id": int(row.goods_id),
                    "item_id": int(row.item_id),
                    "name": row.name,
                    "token_cost": int(row.token_cost),
                    "purchase_limit": int(row.purchase_limit),
                    "purchased_count": int(row.purchased_count),
                    "source_order": int(row.source_order),
                }
                for row in persisted_shop_rows
            ],
            "rankings": (
                list(observation["rankings"]) if observation is not None else []
            ),
            "refresh_status": dict(evidence["refresh_status"]),
        },
    )
    return list_exchange_activity_snapshot(
        session,
        activity_type="xutian-palace",
        activity_id=persisted_activity_id,
    ).selected_activity
