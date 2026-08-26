from __future__ import annotations

"""Read-only Runtime facts for the current 仙缘夺魁 occurrence."""

from datetime import datetime
import time
from typing import Any

from backend.core.fanxiu.activity.yunmeng_rank_reward import (
    load_activity_rank_reward_tiers,
)
from backend.core.fanxiu.instrumentation.activity_rank_runtime import (
    resolve_activity_rank_root,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    LuaJitReader,
    MumuProcessMemory,
    resolve_manager_root,
)
from backend.core.fanxiu.instrumentation.wallet import (
    WALLET_MARKER,
    WALLET_METHODS,
    wallet_currency_data,
)
from backend.core.fanxiu.instrumentation.yunmeng_trial import _rank_data


def read_xianyuan_duokui_status_snapshot(
    *,
    rank_activity_id: int = 46003,
    currency_type: int = 23002,
    event_date: str,
) -> dict[str, Any]:
    """Read exact rank and wallet state without starting a discovery scan."""

    started_at = time.perf_counter()
    memory = MumuProcessMemory.discover_cached(fallback_to_discovery=False)
    reader = LuaJitReader(memory)
    wallet_root, wallet_cache_hit = resolve_manager_root(
        memory,
        manager_key="wallet",
        marker=WALLET_MARKER,
        required_methods=WALLET_METHODS,
        validate=lambda current_reader, address: wallet_currency_data(
            current_reader,
            address,
            int(currency_type),
            missing_as_zero=False,
        ),
        allow_discovery=False,
    )
    # A missing WalletVO is not an authoritative zero: the redemption panel
    # can still render a non-zero activity-local balance.  Fail closed and let
    # the exact open-panel reader provide that fact instead of fabricating 0.
    wallet = wallet_currency_data(
        reader,
        wallet_root,
        int(currency_type),
        missing_as_zero=False,
    )
    rank_root, rank_cache_hit = resolve_activity_rank_root(
        memory,
        allow_discovery=False,
    )
    tiers = load_activity_rank_reward_tiers(
        rank_activity_id=int(rank_activity_id),
        event_date=event_date,
    )
    rank = _rank_data(
        reader,
        rank_root,
        int(rank_activity_id),
        reward_tiers=tiers,
    )
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "runtime_memory",
        "rank_activity_id": int(rank_activity_id),
        "currency_type": int(currency_type),
        "captured_at": captured_at,
        "reward_tiers": tiers,
        **rank,
        **wallet,
        "elapsed_seconds": time.perf_counter() - started_at,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "rank_root_address": f"0x{rank_root:x}",
            "wallet_root_address": f"0x{wallet_root:x}",
            "rank_root_cache_hit": rank_cache_hit,
            "wallet_root_cache_hit": wallet_cache_hit,
            "discovery_allowed": False,
        },
    }


__all__ = ["read_xianyuan_duokui_status_snapshot"]
