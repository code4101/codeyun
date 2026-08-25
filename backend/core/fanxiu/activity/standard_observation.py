from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlmodel import Session, col, select

from backend.models import FanxiuPacketBusinessRecord


class ActivityObservationUnavailable(RuntimeError):
    """Raised when a declared standard fact has not reached the database yet."""


@dataclass(frozen=True)
class ActivityObservationSpec:
    """Data declaration shared by activity-specific presentation modules."""

    rank_activity_id: int
    currency_type: int
    progress_protocols: tuple[str, ...] = ()
    related_rank_activity_ids: tuple[tuple[str, int], ...] = ()
    primary_scope: str = "personal"
    row_mode: Literal["key_points", "full_observed"] = "key_points"

    def __post_init__(self) -> None:
        if not self.primary_scope.strip():
            raise ValueError("主榜 scope 不能为空")


def _full_observed_rank_rows(
    rank: dict[str, Any],
    *,
    scope: str,
    reward_tiers: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Keep every real row present in a standard fact, plus a missing self row.

    ``rank_list_size`` is a server-declared total, not proof that the packet
    contains that many player records.  Completeness metadata therefore stays
    explicit and no synthetic last-player row is created.
    """

    items = [dict(item) for item in rank.get("items") or [] if isinstance(item, dict)]
    declared_count = int(rank.get("rank_list_size") or 0)
    loaded_count = len(items)
    scope_complete = declared_count > 0 and loaded_count >= declared_count
    rank_activity_id = int(rank["rank_activity_id"])
    self_rank = int(rank["rank"])
    self_key = str(rank.get("role_key") or "")
    tiers = [dict(tier) for tier in reward_tiers or []]

    rows: dict[tuple[int, str], dict[str, Any]] = {}
    for item in items:
        item_rank = int(item.get("rank") or 0)
        role_key = str(
            item.get("key")
            or item.get("id")
            or item.get("server_id")
            or f"observed-rank:{item_rank}"
        )
        # A rank packet has one entity per positive rank.  Some protocol VOs
        # encode ``personal_item.key`` differently from list-item ``key``;
        # rank identity is therefore the stable self join within one snapshot.
        is_self = item_rank == self_rank
        tier = next(
            (
                value for value in tiers
                if int(value["rank_start"]) <= item_rank <= int(value["rank_end"])
            ),
            None,
        )
        is_guard = any(int(value["rank_end"]) == item_rank for value in tiers)
        rows[(item_rank, role_key)] = {
            "ranking_scope": scope,
            "rank": item_rank,
            "score": int(item.get("score") or 0),
            "role_key": role_key,
            "name": str(item.get("name") or ""),
            "server_id": item.get("server_id"),
            "server_name": str(item.get("server_name") or ""),
            "club_name": str(item.get("club_name") or ""),
            "is_self": is_self,
            "is_reward_guard": is_guard,
            "is_last_player": declared_count > 0 and item_rank == declared_count,
            "has_player": True,
            "reward_rank_start": int(tier["rank_start"]) if tier and is_self else None,
            "reward_rank_end": int(tier["rank_end"]) if tier and is_self else None,
            "raw_data": {
                "rank_activity_id": rank_activity_id,
                "reported_rank_list_size": declared_count,
                "loaded_player_count": loaded_count,
                "scope_complete": scope_complete,
                "row_source": "rank_items",
            },
        }

    if not any(row["is_self"] for row in rows.values()):
        fallback_key = self_key or f"self-rank:{self_rank}"
        tier = next(
            (
                value for value in tiers
                if int(value["rank_start"]) <= self_rank <= int(value["rank_end"])
            ),
            None,
        )
        rows[(self_rank, fallback_key)] = {
            "ranking_scope": scope,
            "rank": self_rank,
            "score": int(rank.get("score") or 0),
            "role_key": fallback_key,
            "name": str(rank.get("name") or ""),
            "server_id": rank.get("server_id"),
            "server_name": str(rank.get("server_name") or ""),
            "club_name": str(rank.get("club_name") or ""),
            "is_self": True,
            "is_reward_guard": any(
                int(value["rank_end"]) == self_rank for value in tiers
            ),
            "is_last_player": declared_count > 0 and self_rank == declared_count,
            "has_player": True,
            "reward_rank_start": int(tier["rank_start"]) if tier else None,
            "reward_rank_end": int(tier["rank_end"]) if tier else None,
            "raw_data": {
                "rank_activity_id": rank_activity_id,
                "reported_rank_list_size": declared_count,
                "loaded_player_count": loaded_count,
                "scope_complete": scope_complete,
                "row_source": "personal_item_fallback",
            },
        }
    return sorted(rows.values(), key=lambda item: (int(item["rank"]), item["role_key"]))


def _latest_business_fact(
    session: Session,
    *,
    domain: str,
    record_key: str | None = None,
    entity_id: int | str | None = None,
) -> FanxiuPacketBusinessRecord | None:
    query = select(FanxiuPacketBusinessRecord).where(
        FanxiuPacketBusinessRecord.domain == domain
    )
    if record_key is not None:
        query = query.where(FanxiuPacketBusinessRecord.record_key == record_key)
    if entity_id is not None:
        query = query.where(FanxiuPacketBusinessRecord.entity_id == str(entity_id))
    return session.exec(
        query.order_by(
            col(FanxiuPacketBusinessRecord.captured_at).desc(),
            col(FanxiuPacketBusinessRecord.updated_at).desc(),
        )
    ).first()


def read_currency_fact(session: Session, currency_type: int) -> dict[str, Any]:
    """Read one normalized currency fact, regardless of the producing activity."""

    row = _latest_business_fact(
        session,
        domain="resource_state",
        record_key=f"currency:{int(currency_type)}",
    )
    if row is None:
        raise ActivityObservationUnavailable(
            f"资源类型 {int(currency_type)} 尚无标准运行态事实"
        )
    payload = dict(row.payload or {})
    amount = payload.get("amount")
    history = payload.get("history")
    borrow = payload.get("borrow") or 0
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in (amount, history, borrow)):
        raise ActivityObservationUnavailable(
            f"资源类型 {int(currency_type)} 的标准运行态事实不完整"
        )
    return {
        "currency_type": int(currency_type),
        "exchange_currency": int(amount) - int(borrow),
        "currency_amount": int(amount),
        "currency_borrow": int(borrow),
        "cumulative_currency": int(history),
        "captured_at": row.captured_at,
        "protocol": row.protocol,
        "evidence": dict(row.evidence or {}),
    }


def store_runtime_currency_fact(
    session: Session,
    snapshot: dict[str, Any],
) -> dict[str, int]:
    """Persist one externally read Wallet snapshot as the standard absolute fact."""

    from backend.core.fanxiu.business_data import (
        upsert_fanxiu_business_records,
    )

    currency_type = int(snapshot.get("currency_type") or 0)
    amount = snapshot.get("currency_amount")
    borrow = snapshot.get("currency_borrow")
    history = snapshot.get("cumulative_currency")
    # Packet facts use a space separator while runtime snapshots use ISO ``T``.
    # Normalize before the business-store lexical event-order guard compares
    # them, otherwise a later packet on the same day could look older than a
    # runtime snapshot solely because ``" " < "T"``.
    captured_at = str(snapshot.get("captured_at") or "").replace("T", " ", 1)
    if currency_type <= 0 or not captured_at:
        raise ValueError("Runtime 钱包事实缺少币种或采集时间")
    if not all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (amount, borrow, history)
    ):
        raise ValueError("Runtime 钱包事实缺少绝对 amount/borrow/history")
    evidence = {
        **dict(snapshot.get("evidence") or {}),
        "captured_at": captured_at,
        "protocol": "runtime_memory_wallet",
        "source_kind": "read_only_runtime_memory",
    }
    payload = {
        "resource_type": currency_type,
        "type": currency_type,
        "amount": int(amount),
        "history": int(history),
        "borrow": int(borrow),
        "available": int(amount) - int(borrow),
        "captured_at": captured_at,
        "protocol": "runtime_memory_wallet",
        "currency_derivation": "wallet_amount_minus_borrow_and_history",
        "evidence": evidence,
    }
    process_ticks = str(evidence.get("process_start_ticks") or "unknown")
    return upsert_fanxiu_business_records(
        session,
        [{
            "domain": "resource_state",
            "record_key": f"currency:{currency_type}",
            "protocol": "runtime_memory_wallet",
            "packet_id": f"runtime-wallet:{process_ticks}:{captured_at}",
            "source_kind": "read_only_runtime_memory",
            "entity_id": currency_type,
            "entity_name": f"资源类型 {currency_type}",
            "captured_at": captured_at,
            "payload": payload,
            "evidence": evidence,
        }],
    )


def store_runtime_activity_rank_fact(
    session: Session,
    snapshot: dict[str, Any],
    *,
    occurrence_runtime_id: str,
) -> dict[str, int]:
    """Persist an already-loaded rank projection bound to one occurrence."""

    from backend.core.fanxiu.business_data import upsert_fanxiu_business_records

    activity_id = int(snapshot.get("rank_activity_id") or 0)
    captured_at = str(snapshot.get("captured_at") or "").replace("T", " ", 1)
    personal = dict(snapshot.get("self_ranking") or {})
    rows = [
        dict(item)
        for item in snapshot.get("rankings") or []
        if isinstance(item, dict)
    ]
    if (
        not snapshot.get("ok")
        or not snapshot.get("complete")
        or activity_id <= 0
        or not captured_at
        or not occurrence_runtime_id
        or not isinstance(personal.get("rank"), int)
        or not isinstance(personal.get("score"), int | float)
    ):
        raise ValueError("Runtime 活动榜事实不完整或缺少 occurrence 绑定")

    def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "rank": int(row.get("rank") or 0),
            "score": int(row.get("score") or 0),
            "key": str(row.get("role_key") or row.get("key") or ""),
            "name": str(row.get("name") or ""),
            "server_id": row.get("server_id"),
            "server_name": str(row.get("server_name") or ""),
            "club_name": str(row.get("club_name") or ""),
        }

    evidence = {
        **dict(snapshot.get("evidence") or {}),
        "occurrence_runtime_id": str(occurrence_runtime_id),
        "captured_at": captured_at,
        "source_kind": "read_only_runtime_memory",
    }
    normalized_snapshot = {
        "rank_activity_id": activity_id,
        "rank_vo_type": "runtime_memory_activity_rank",
        "rank_list_size": int(snapshot.get("rank_list_size") or 0),
        "personal_item": normalize_row(personal),
        "items": [normalize_row(row) for row in rows],
    }
    process_ticks = str(evidence.get("process_start_ticks") or "unknown")
    return upsert_fanxiu_business_records(
        session,
        [{
            "domain": "activity_rank",
            "record_key": f"activity-rank:{activity_id}",
            "protocol": "runtime_memory_activity_rank",
            "packet_id": (
                f"runtime-rank:{process_ticks}:{activity_id}:"
                f"{occurrence_runtime_id}:{captured_at}"
            ),
            "source_kind": "read_only_runtime_memory",
            "entity_id": activity_id,
            "entity_name": f"活动榜 {activity_id}",
            "captured_at": captured_at,
            "payload": {"snapshot": normalized_snapshot},
            "evidence": evidence,
        }],
    )


def read_activity_rank_fact(
    session: Session,
    rank_activity_id: int,
) -> dict[str, Any]:
    """Read the latest normalized personal/activity-rank fact by game ID."""

    row = _latest_business_fact(
        session,
        domain="activity_rank",
        entity_id=rank_activity_id,
    )
    if row is None:
        raise ActivityObservationUnavailable(
            f"活动榜 {int(rank_activity_id)} 尚无标准运行态事实"
        )
    payload = dict(row.payload or {})
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else payload
    personal = snapshot.get("personal_item")
    if not isinstance(personal, dict):
        raise ActivityObservationUnavailable(
            f"活动榜 {int(rank_activity_id)} 尚无个人排名事实"
        )
    score = personal.get("score")
    rank = personal.get("rank")
    if not isinstance(score, int | float) or isinstance(score, bool) or not isinstance(rank, int):
        raise ActivityObservationUnavailable(
            f"活动榜 {int(rank_activity_id)} 的个人排名事实不完整"
        )
    rank_vo_type = str(snapshot.get("rank_vo_type") or "")
    is_cross_server = "crossserver" in rank_vo_type.lower()
    items = [dict(item) for item in snapshot.get("items") or [] if isinstance(item, dict)]
    if is_cross_server:
        for item in items:
            if item.get("server_id") is None:
                item["server_id"] = item.get("id")
            if not item.get("server_name"):
                item["server_name"] = item.get("name") or ""
        if personal.get("server_id") is None:
            personal["server_id"] = personal.get("id")
    return {
        "rank_activity_id": int(rank_activity_id),
        "score": int(score),
        "rank": int(rank),
        "role_key": str(personal.get("key") or personal.get("id") or ""),
        "name": str(personal.get("name") or ""),
        "server_id": personal.get("server_id"),
        "server_name": str(personal.get("server_name") or ""),
        "club_name": str(personal.get("club_name") or ""),
        "rank_list_size": int(snapshot.get("rank_list_size") or 0),
        "items": items,
        "rank_vo_type": rank_vo_type,
        "captured_at": row.captured_at,
        "protocol": row.protocol,
        "evidence": dict(row.evidence or {}),
    }


def discover_related_activity_rank_facts(
    session: Session,
    primary_rank_activity_id: int,
) -> dict[str, dict[str, Any]]:
    """Discover structurally typed companion rankings in one activity family.

    The game declares the ranking VO type in every standard rank packet.  We
    use that declaration instead of assuming that a particular numeric ID is
    always the plane ranking.
    """

    family = int(primary_rank_activity_id) // 100
    rows = session.exec(
        select(FanxiuPacketBusinessRecord)
        .where(FanxiuPacketBusinessRecord.domain == "activity_rank")
        .order_by(
            col(FanxiuPacketBusinessRecord.captured_at).desc(),
            col(FanxiuPacketBusinessRecord.updated_at).desc(),
        )
    ).all()
    seen_ids: set[int] = set()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            activity_id = int(row.entity_id)
        except (TypeError, ValueError):
            continue
        if activity_id == int(primary_rank_activity_id) or activity_id // 100 != family:
            continue
        if activity_id in seen_ids:
            continue
        seen_ids.add(activity_id)
        payload = dict(row.payload or {})
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else payload
        rank_vo_type = str(snapshot.get("rank_vo_type") or "")
        if "crossserver" not in rank_vo_type.lower():
            continue
        try:
            result["plane"] = read_activity_rank_fact(session, activity_id)
        except ActivityObservationUnavailable:
            continue
        break
    return result


def collect_standard_activity_observation(
    session: Session,
    spec: ActivityObservationSpec,
    *,
    reward_tiers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compose a DB-backed activity observation from generic runtime facts."""

    currency = read_currency_fact(session, spec.currency_type)
    rank = read_activity_rank_fact(session, spec.rank_activity_id)
    rows_by_rank = {
        int(item["rank"]): item
        for item in rank["items"]
        if isinstance(item.get("rank"), int)
    }
    guard_rows: list[dict[str, Any]] = []
    for tier in reward_tiers or []:
        guard_rank = int(tier["rank_end"])
        item = rows_by_rank.get(guard_rank)
        if item is None:
            continue
        guard_rows.append(
            {
                "rank": guard_rank,
                "score": int(item.get("score") or 0),
                "role_key": str(item.get("key") or item.get("id") or ""),
                "name": str(item.get("name") or ""),
                "server_id": item.get("server_id"),
                "server_name": str(item.get("server_name") or ""),
                "club_name": str(item.get("club_name") or ""),
                "is_self": False,
                "is_reward_guard": True,
                "reward_rank_start": int(tier["rank_start"]),
                "reward_rank_end": guard_rank,
            }
        )
    self_rank = int(rank["rank"])
    self_reward_tier = next(
        (
            tier
            for tier in reward_tiers or []
            if int(tier["rank_start"]) <= self_rank <= int(tier["rank_end"])
        ),
        None,
    )
    self_row = {
        "rank": self_rank,
        "score": rank["score"],
        "role_key": rank["role_key"],
        "name": rank["name"],
        "server_id": rank["server_id"],
        "server_name": rank["server_name"],
        "club_name": rank["club_name"],
        "is_self": True,
        "is_reward_guard": any(row["rank"] == rank["rank"] for row in guard_rows),
        "reward_rank_start": (
            int(self_reward_tier["rank_start"])
            if self_reward_tier is not None
            else None
        ),
        "reward_rank_end": (
            int(self_reward_tier["rank_end"])
            if self_reward_tier is not None
            else None
        ),
    }
    if not any(
        row["rank"] == self_row["rank"] and row["role_key"] == self_row["role_key"]
        for row in guard_rows
    ):
        guard_rows.append(self_row)
    else:
        for row in guard_rows:
            if row["rank"] == self_row["rank"] and row["role_key"] == self_row["role_key"]:
                row["is_self"] = True
    last_rank = int(rank["rank_list_size"] or 0)
    if last_rank and not any(row["rank"] == last_rank for row in guard_rows):
        item = rows_by_rank.get(last_rank)
        guard_rows.append(
            {
                "ranking_scope": "personal",
                "rank": last_rank,
                "score": int(item.get("score") or 0) if item else 0,
                "role_key": str(item.get("key") or item.get("id") or "") if item else f"last-player:{last_rank}",
                "name": str(item.get("name") or "") if item else "",
                "server_id": item.get("server_id") if item else None,
                "server_name": str(item.get("server_name") or "") if item else "",
                "club_name": str(item.get("club_name") or "") if item else "",
                "is_self": False,
                "is_reward_guard": False,
                "is_last_player": True,
                "has_player": item is not None,
                "rank_list_size": last_rank,
                "reward_rank_start": None,
                "reward_rank_end": None,
            }
        )

    for row in guard_rows:
        row.setdefault("ranking_scope", "personal")
        row.setdefault("is_last_player", row["rank"] == last_rank and last_rank > 0)
        row.setdefault("has_player", True)
        row.setdefault("rank_list_size", last_rank)

    if spec.row_mode == "full_observed":
        guard_rows = _full_observed_rank_rows(
            rank,
            scope=spec.primary_scope,
            reward_tiers=reward_tiers,
        )

    related_ranks: dict[str, dict[str, Any]] = {}
    if spec.related_rank_activity_ids:
        for scope, activity_id in spec.related_rank_activity_ids:
            try:
                related_ranks[str(scope)] = read_activity_rank_fact(
                    session,
                    int(activity_id),
                )
            except ActivityObservationUnavailable:
                # Companion rankings are populated on demand by the game.  A
                # missing plane packet must not suppress an otherwise current
                # personal ranking, but once it arrives we bind the declared
                # game ID instead of guessing from a neighbouring activity.
                continue
    else:
        related_ranks = discover_related_activity_rank_facts(
            session,
            spec.rank_activity_id,
        )
    captured_values = [currency["captured_at"], rank["captured_at"]]
    for scope, related in related_ranks.items():
        captured_values.append(related["captured_at"])
        if spec.row_mode == "full_observed":
            guard_rows.extend(
                _full_observed_rank_rows(related, scope=scope)
            )
            continue
        scope_size = int(related.get("rank_list_size") or 0)
        for item in related["items"]:
            item_rank = int(item.get("rank") or 0)
            server_id = item.get("server_id") or item.get("id")
            guard_rows.append(
                {
                    "ranking_scope": scope,
                    "rank": item_rank,
                    "score": int(item.get("score") or 0),
                    "role_key": str(item.get("key") or item.get("id") or server_id or item_rank),
                    "name": str(item.get("name") or ""),
                    "server_id": server_id,
                    "server_name": str(item.get("server_name") or ""),
                    "club_name": str(item.get("club_name") or ""),
                    "is_self": item_rank == int(related["rank"]),
                    "is_reward_guard": False,
                    "is_last_player": item_rank == scope_size and scope_size > 0,
                    "has_player": True,
                    "rank_list_size": scope_size,
                    "scope_complete": len(related["items"]) >= scope_size > 0,
                    "reward_rank_start": None,
                    "reward_rank_end": None,
                }
            )
    # A comparative server/team fact commonly carries the authoritative display
    # name while the larger personal fact only carries the stable server id.
    # Join those sibling facts here, before activity-specific persistence, so
    # every consumer sees the same enriched observation.
    server_names = {
        int(row["server_id"]): str(row["server_name"])
        for row in guard_rows
        if row.get("server_id") is not None and str(row.get("server_name") or "")
    }
    for row in guard_rows:
        server_id = row.get("server_id")
        if not row.get("server_name") and server_id is not None:
            row["server_name"] = server_names.get(int(server_id), "")
    captured_at = max(captured_values)
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "standard_runtime_facts",
        "rank_activity_id": spec.rank_activity_id,
        "currency_type": spec.currency_type,
        "captured_at": captured_at,
        "score": rank["score"],
        "rank": rank["rank"],
        "role_key": rank["role_key"],
        "name": rank["name"],
        "server_id": rank["server_id"],
        "club_name": rank["club_name"],
        "rank_list_size": rank["rank_list_size"],
        "rankings": sorted(
            guard_rows,
            key=lambda item: (
                str(item.get("ranking_scope") or spec.primary_scope),
                int(item["rank"]),
                str(item.get("role_key") or ""),
            ),
        ),
        "exchange_currency": currency["exchange_currency"],
        "currency_amount": currency["currency_amount"],
        "currency_borrow": currency["currency_borrow"],
        "cumulative_currency": currency["cumulative_currency"],
        "currency_derivation": "standard_resource_state",
        "evidence": {
            "rank": rank["evidence"],
            "currency": currency["evidence"],
            "rank_captured_at": rank["captured_at"],
            "currency_captured_at": currency["captured_at"],
            "rank_protocol": rank["protocol"],
            "currency_protocol": currency["protocol"],
        },
    }
