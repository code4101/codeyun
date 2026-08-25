"""Pure, fail-closed projections for the Xianyuan Banquet activity family.

The live game calls this family ``DoupoParty``.  The banquet main page is
owned by ``DoupoPartyMgr`` while the wish-tree draw, tasks and exchange shop
reuse ``RevenueMgr``/``QuestMgr``.  This module deliberately does not attach
to a process or execute Lua: callers must supply naturally loaded read-only
Runtime snapshots.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
import time
from typing import Any

from backend.core.fanxiu.instrumentation.activity_runtime import (
    ACTIVITY_MANAGER_METHODS,
    _resolve_activity_manager_runtime,
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
    resolve_manager_root,
)


SPIRIT_STONE_CURRENCY_TYPE = 1
XIANYUAN_MAIN_BASE_ID = 118000
XIANYUAN_GIFT_BASE_ID = 118020
XIANYUAN_REVENUE_BASE_IDS = frozenset({118010, 118011, 118012, 118013})
_XIANYUAN_BASE_IDS = frozenset(
    {XIANYUAN_MAIN_BASE_ID, XIANYUAN_GIFT_BASE_ID, *XIANYUAN_REVENUE_BASE_IDS}
)
_DOUPO_PARTY_MARKER = b"LuaDoupoPartyMgr"
_DOUPO_PARTY_METHODS = frozenset({"LuaDoupoPartyMgr", "Inst_get"})


def _integer(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value) if isinstance(value, LuaRef) and value.kind == "table" else {}


def _long_or_int(reader: LuaJitReader, value: Any) -> int | None:
    return reader.long(value) if isinstance(value, LuaRef) else as_int(value)


def _doupo_party_data_fields(reader: LuaJitReader, manager_root: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, manager_root, _DOUPO_PARTY_METHODS)
    instance = _fields(reader, manager.get("inst"))
    if not instance:
        raise FanxiuRuntimeMemoryError("DoupoPartyMgr 实例尚未加载", code="data_not_loaded")
    model = _fields(reader, instance.get("Model"))
    data = _fields(reader, model.get("DoupoPartyData"))
    if not data:
        raise FanxiuRuntimeMemoryError(
            "DoupoPartyData 仍为空构造态",
            code="data_not_loaded",
        )
    if not {"V_DoupoPartyList", "V_DoupoPartyDic"}.issubset(data):
        raise FanxiuRuntimeMemoryError(
            "DoupoPartyData 服务器列表尚未物化",
            code="data_not_loaded",
        )
    return data


def _resolve_doupo_party_manager(
    memory: MumuProcessMemory,
    *,
    allow_discovery: bool,
    force_refresh: bool,
) -> tuple[int, bool, str]:
    validate = lambda reader, address: manager_index_fields(  # noqa: E731
        reader, address, _DOUPO_PARTY_METHODS
    )
    try:
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="xianyuan-banquet",
            state_address=int(_lua_addresses(memory)["state"], 16),
            global_name="DoupoPartyMgr",
            required_methods=_DOUPO_PARTY_METHODS,
            validate=validate,
            force_refresh=force_refresh,
        )
        return root, cache_hit, "lua_global"
    except FanxiuRuntimeMemoryError:
        root, cache_hit = resolve_manager_root(
            memory,
            manager_key="xianyuan-banquet",
            marker=_DOUPO_PARTY_MARKER,
            required_methods=_DOUPO_PARTY_METHODS,
            validate=validate,
            allow_discovery=allow_discovery,
            force_refresh=force_refresh,
        )
        return root, cache_hit, "constructor_marker"


def build_xianyuan_banquet_runtime_snapshot(
    *,
    occurrences: Iterable[Mapping[str, Any]],
    party_rows: Iterable[Mapping[str, Any]] | None,
    party_declared_count: int | None,
) -> dict[str, Any]:
    """Validate one coherent occurrence/party observation.

    ``party_rows=None`` is deliberately different from an empty iterable: the
    former is the pre-open/constructor state, while the latter is a loaded
    server fact proving that the player currently owns no banquet.
    """

    rows = [dict(row) for row in occurrences]
    main = [row for row in rows if _integer(row.get("base_id")) == XIANYUAN_MAIN_BASE_ID]
    gifts = [row for row in rows if _integer(row.get("base_id")) == XIANYUAN_GIFT_BASE_ID]
    occurrence_reasons: list[str] = []
    if len(main) != 1:
        occurrence_reasons.append("main_occurrence_not_unique")
    if len(gifts) > 1:
        occurrence_reasons.append("gift_occurrence_not_unique")
    for row in rows:
        if (
            _integer(row.get("activity_id")) <= 0
            or _integer(row.get("base_id")) not in _XIANYUAN_BASE_IDS
            or _integer(row.get("state"), default=-1) < 0
        ):
            occurrence_reasons.append("invalid_occurrence")
            break

    if occurrence_reasons:
        return {
            "complete": False,
            "status": "snapshot_incomplete",
            "reason": ",".join(occurrence_reasons),
            "occurrences": rows,
            "parties": [],
        }
    if party_rows is None:
        return {
            "complete": False,
            "status": "data_not_loaded",
            "reason": "DoupoPartyData 服务器状态尚未物化",
            "occurrences": rows,
            "main_activity_id": _integer(main[0].get("activity_id")),
            "gift_activity_id": _integer(gifts[0].get("activity_id")) if gifts else None,
            "parties": [],
        }

    parties = [dict(row) for row in party_rows]
    if party_declared_count is None or party_declared_count != len(parties):
        return {
            "complete": False,
            "status": "snapshot_incomplete",
            "reason": "party_count_mismatch",
            "occurrences": rows,
            "parties": [],
        }
    party_ids = [_integer(row.get("party_id"), default=-1) for row in parties]
    if any(value <= 0 for value in party_ids) or len(set(party_ids)) != len(party_ids):
        return {
            "complete": False,
            "status": "snapshot_incomplete",
            "reason": "invalid_or_duplicate_party_id",
            "occurrences": rows,
            "parties": [],
        }
    completed = [row for row in parties if row.get("is_completed") is True]
    active = [row for row in parties if row.get("is_completed") is not True]
    return {
        "complete": True,
        "status": "reward_claimable" if completed else "active" if active else "loaded_empty",
        "reason": "",
        "occurrences": rows,
        "main_activity_id": _integer(main[0].get("activity_id")),
        "gift_activity_id": _integer(gifts[0].get("activity_id")) if gifts else None,
        "parties": parties,
        "completed_party_ids": [_integer(row.get("party_id")) for row in completed],
        "active_party_ids": [_integer(row.get("party_id")) for row in active],
    }


def read_xianyuan_banquet_runtime(
    *,
    allow_discovery: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Read naturally loaded ActivityMgr + DoupoPartyMgr state, strictly read-only."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    occurrences: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {}
    try:
        memory = MumuProcessMemory.discover_cached(fallback_to_discovery=True)
        activity_root, activity_cache_hit, activity_resolver = _resolve_activity_manager_runtime(
            memory,
            allow_discovery=allow_discovery,
            force_refresh=force_refresh,
        )
        reader = LuaJitReader(memory)
        activity_manager = manager_index_fields(reader, activity_root, ACTIVITY_MANAGER_METHODS)
        activity_data = _fields(
            reader,
            _fields(reader, _fields(reader, activity_manager.get("inst")).get("Model")).get(
                "ActivityData"
            ),
        )
        active = reader.dictionary_fields(activity_data.get("V_ActivationActivityDic"))
        for value in active.values():
            fields = _fields(reader, value)
            base_id = as_int(fields.get("baseId"))
            if base_id not in _XIANYUAN_BASE_IDS:
                continue
            occurrences.append(
                {
                    "activity_id": as_int(fields.get("activityId")),
                    "base_id": base_id,
                    "state": as_int(fields.get("state")),
                    "start_time_ms": _long_or_int(reader, fields.get("startTime")),
                    "end_time_ms": _long_or_int(reader, fields.get("endTime")),
                    "close_panel_time_ms": _long_or_int(reader, fields.get("closePanelTime")),
                }
            )
        occurrences.sort(key=lambda row: (_integer(row.get("base_id")), _integer(row.get("activity_id"))))
        evidence.update(
            {
                "activity_manager_root": f"0x{activity_root:x}",
                "activity_manager_cache_hit": activity_cache_hit,
                "activity_manager_resolver": activity_resolver,
            }
        )

        doupo_root, doupo_cache_hit, doupo_resolver = _resolve_doupo_party_manager(
            memory,
            allow_discovery=allow_discovery,
            force_refresh=force_refresh,
        )
        evidence.update(
            {
                "doupo_manager_root": f"0x{doupo_root:x}",
                "doupo_manager_cache_hit": doupo_cache_hit,
                "doupo_manager_resolver": doupo_resolver,
            }
        )
        try:
            data = _doupo_party_data_fields(reader, doupo_root)
        except FanxiuRuntimeMemoryError as exc:
            if exc.code != "data_not_loaded":
                raise
            normalized = build_xianyuan_banquet_runtime_snapshot(
                occurrences=occurrences,
                party_rows=None,
                party_declared_count=None,
            )
        else:
            party_ids, party_declared_count = reader.list_items(data["V_DoupoPartyList"])
            dictionary = reader.dictionary_fields(data["V_DoupoPartyDic"])
            parties: list[dict[str, Any]] = []
            for value in dictionary.values():
                fields = _fields(reader, value)
                party_id = _long_or_int(reader, fields.get("partyId"))
                helper_values, helper_count = reader.list_items(fields.get("helperVOList"))
                fu_values, fu_count = reader.list_items(fields.get("fuIds"))
                parties.append(
                    {
                        "party_id": party_id,
                        "res_id": as_int(fields.get("resId")),
                        "end_time_ms": _long_or_int(reader, fields.get("endTime")),
                        "is_completed": fields.get("isCompleted") is True,
                        "helper_count": helper_count if helper_count is not None else len(helper_values),
                        "fu_ids": sorted(
                            value
                            for raw in fu_values
                            if (value := as_int(raw)) is not None
                        ),
                        "fu_declared_count": fu_count,
                    }
                )
            if party_declared_count != len(party_ids) or len(parties) != len(party_ids):
                raise FanxiuRuntimeMemoryError(
                    "DoupoParty 列表与字典计数不一致",
                    code="snapshot_incomplete",
                )
            normalized = build_xianyuan_banquet_runtime_snapshot(
                occurrences=occurrences,
                party_rows=parties,
                party_declared_count=party_declared_count,
            )

        return {
            "ok": normalized["complete"],
            "available": True,
            "source": "runtime_memory",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **normalized,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "read_only": True,
                **evidence,
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "status": "unavailable",
            "reason": str(exc),
            "occurrences": occurrences,
            "parties": [],
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
                "read_only": True,
                **evidence,
            },
        }


def classify_banquet_parties(
    own_parties: Iterable[Mapping[str, Any]] | None,
    *,
    chest_inventory: Mapping[int, int] | None,
) -> dict[str, Any]:
    """Project the authoritative ``DoupoPartyData`` state.

    ``None`` means the manager data has not loaded.  An empty iterable is a
    valid, loaded state.  Completed parties are always claimed before a new
    banquet may be launched; active parties are never relaunched.
    """

    if own_parties is None or chest_inventory is None:
        return {
            "state": "data_not_loaded",
            "authorized_action": None,
            "reason": "DoupoPartyData 或宴盒库存尚未自然加载",
        }

    rows = [dict(row) for row in own_parties]
    completed = [row for row in rows if row.get("isCompleted") is True]
    active = [row for row in rows if row.get("isCompleted") is not True]
    if completed:
        return {
            "state": "reward_claimable",
            "authorized_action": "claim_party_reward",
            "party_ids": [row.get("partyId") for row in completed],
        }
    if active:
        return {
            "state": "banquet_in_progress",
            "authorized_action": None,
            "party_ids": [row.get("partyId") for row in active],
        }

    available_chests = sorted(
        (int(item_id), _integer(count))
        for item_id, count in chest_inventory.items()
        if _integer(count) > 0
    )
    if available_chests:
        return {
            "state": "launchable",
            "authorized_action": "open_launch_picker",
            "available_chests": available_chests,
        }
    return {
        "state": "no_chest",
        "authorized_action": None,
        "available_chests": [],
    }


def classify_quest_rows(rows: Iterable[Mapping[str, Any]] | None) -> dict[str, Any]:
    """Classify Revenue/banquet quests from their server progress.

    The client treats ``turn > rewardTime`` as claimable.  Rows carrying the
    generic normalized states ``claimable``/``claimed`` are accepted too so
    this helper can consume the existing Revenue task observation layer.
    """

    if rows is None:
        return {
            "state": "data_not_loaded",
            "claimable_task_ids": [],
            "pending_task_ids": [],
        }

    claimable: list[int] = []
    pending: list[int] = []
    claimed: list[int] = []
    unknown: list[int] = []
    for raw in rows:
        row = dict(raw)
        task_id = _integer(row.get("task_id", row.get("id")))
        state = row.get("state")
        server_data = row.get("serverData")
        if isinstance(server_data, Mapping):
            turn = server_data.get("turn")
            reward_time = server_data.get("rewardTime")
        else:
            turn = row.get("turn")
            reward_time = row.get("rewardTime")
        if state == "claimable" or (
            state is None
            and turn is not None
            and reward_time is not None
            and _integer(turn) > _integer(reward_time)
        ):
            claimable.append(task_id)
        elif state == "claimed":
            claimed.append(task_id)
        elif state in {"pending", "locked", "receiving"}:
            pending.append(task_id)
        else:
            unknown.append(task_id)

    overall = "claimable" if claimable else "pending" if pending else "complete"
    if unknown:
        overall = "snapshot_incomplete"
    return {
        "state": overall,
        "claimable_task_ids": claimable,
        "pending_task_ids": pending,
        "claimed_task_ids": claimed,
        "unknown_task_ids": unknown,
    }


def select_spirit_stone_goods(
    shop_configs: Iterable[Mapping[str, Any]] | None,
    *,
    purchase_counts: Mapping[int, int] | None,
    wallet_balance: int | None,
    spend_budget: int | None,
) -> dict[str, Any]:
    """Select affordable spirit-stone goods without inventing spend authority.

    ``shop_configs`` must be the current occurrence's naturally loaded
    ``RevenueVO.shopConfigs.itemList``.  ``purchase_counts`` comes from
    ``RevenueModel.V_RevenueExchange.V_ShopInfo``.  A budget is mandatory:
    even currency type 1 is a paid-resource action and must not silently drain
    the wallet.  The generic ``RevenueStore``/``ChargeMgr`` gift page has a
    different schema and is handled by :func:`select_spirit_stone_store_offers`.
    """

    if shop_configs is None or purchase_counts is None or wallet_balance is None:
        return {"state": "data_not_loaded", "selected": [], "total_cost": 0}
    if spend_budget is None or spend_budget < 0:
        return {
            "state": "spend_budget_required",
            "selected": [],
            "total_cost": 0,
        }

    remaining_budget = min(_integer(wallet_balance), _integer(spend_budget))
    selected: list[dict[str, int]] = []
    candidates = sorted(
        (dict(row) for row in shop_configs),
        key=lambda row: (_integer(row.get("position", row.get("sort"))), _integer(row.get("goodsId"))),
    )
    for row in candidates:
        if _integer(row.get("currencyType"), default=-1) != SPIRIT_STONE_CURRENCY_TYPE:
            continue
        if row.get("canShow") is False or row.get("canBuy") is False:
            continue
        goods_id = _integer(row.get("goodsId"))
        price = _integer(row.get("buyPrice"), default=-1)
        if goods_id <= 0 or price <= 0:
            continue
        bought = _integer(purchase_counts.get(goods_id))
        limit_buy = _integer(row.get("limitBuy"))
        limit_times = _integer(row.get("limitTimes"))
        remaining = (
            max(0, limit_times - bought)
            if limit_buy != 0
            else remaining_budget // price
        )
        quantity = min(remaining, remaining_budget // price)
        if quantity <= 0:
            continue
        selected.append({"goods_id": goods_id, "quantity": quantity, "unit_price": price})
        remaining_budget -= quantity * price

    total_cost = min(_integer(wallet_balance), _integer(spend_budget)) - remaining_budget
    return {
        "state": "selected" if selected else "nothing_affordable",
        "selected": selected,
        "total_cost": total_cost,
        "remaining_budget": remaining_budget,
    }


def _single_spirit_stone_cost(costs: Any) -> int | None:
    if isinstance(costs, str):
        values = [costs]
    elif isinstance(costs, Iterable) and not isinstance(costs, Mapping):
        values = list(costs)
    else:
        return None
    if len(values) != 1 or not isinstance(values[0], str):
        return None
    prefix, separator, payload = values[0].partition("|")
    item_id, amount_separator, amount = payload.partition("_")
    if prefix != "Item" or not separator or not amount_separator:
        return None
    if _integer(item_id, default=-1) != SPIRIT_STONE_CURRENCY_TYPE:
        return None
    value = _integer(amount, default=-1)
    return value if value >= 0 else None


def select_spirit_stone_store_offers(
    offers: Iterable[Mapping[str, Any]] | None,
    *,
    purchase_counts: Mapping[int, int] | None,
    wallet_balance: int | None,
    spend_budget: int | None,
) -> dict[str, Any]:
    """Select virtual-currency offers from the RevenueStore gift schema.

    A non-empty ``payId`` is a real-payment boundary and is always rejected by
    this helper.  Only an exact single ``Item|1_N`` cost is accepted as a
    spirit-stone offer.  Ambiguous/mixed costs fail closed.
    """

    if offers is None or purchase_counts is None or wallet_balance is None:
        return {"state": "data_not_loaded", "selected": [], "rejected_paid_ids": []}
    if spend_budget is None or spend_budget < 0:
        return {
            "state": "spend_budget_required",
            "selected": [],
            "rejected_paid_ids": [],
        }

    remaining_budget = min(_integer(wallet_balance), _integer(spend_budget))
    selected: list[dict[str, int]] = []
    rejected_paid_ids: list[int] = []
    unknown_ids: list[int] = []
    for raw in sorted(
        (dict(row) for row in offers),
        key=lambda row: (-_integer(row.get("sort")), _integer(row.get("id"))),
    ):
        offer_id = _integer(raw.get("id"))
        if raw.get("payId") not in {None, "", 0, "0"}:
            rejected_paid_ids.append(offer_id)
            continue
        unit_cost = _single_spirit_stone_cost(raw.get("costs"))
        if unit_cost is None or unit_cost <= 0:
            unknown_ids.append(offer_id)
            continue
        bought = _integer(purchase_counts.get(offer_id))
        limit = _integer(raw.get("times"))
        remaining = max(0, limit - bought)
        quantity = min(remaining, remaining_budget // unit_cost)
        if quantity <= 0:
            continue
        selected.append(
            {"offer_id": offer_id, "quantity": quantity, "unit_cost": unit_cost}
        )
        remaining_budget -= quantity * unit_cost

    return {
        "state": "selected" if selected else "nothing_affordable",
        "selected": selected,
        "total_cost": min(_integer(wallet_balance), _integer(spend_budget))
        - remaining_budget,
        "remaining_budget": remaining_budget,
        "rejected_paid_ids": rejected_paid_ids,
        "unknown_ids": unknown_ids,
    }


def classify_wish_tree_draw(
    revenue_vo: Mapping[str, Any] | None,
    *,
    wallet_balance: int | None,
) -> dict[str, Any]:
    """Classify one- or ten-draw availability from the current Revenue VO."""

    if revenue_vo is None or wallet_balance is None:
        return {"state": "data_not_loaded", "authorized_draw_count": 0}
    cfg = revenue_vo.get("serverCfg") or revenue_vo
    cost_type = _integer(cfg.get("costType"), default=-1)
    cost_value = _integer(cfg.get("costValue"), default=-1)
    if cost_type < 0 or cost_value <= 0:
        return {"state": "snapshot_incomplete", "authorized_draw_count": 0}
    balance = max(0, _integer(wallet_balance))
    available = balance // cost_value
    return {
        "state": "drawable" if available > 0 else "no_draw_currency",
        "authorized_draw_count": 10 if available >= 10 else 1 if available >= 1 else 0,
        "cost_type": cost_type,
        "cost_value": cost_value,
        "available_draws": available,
    }
