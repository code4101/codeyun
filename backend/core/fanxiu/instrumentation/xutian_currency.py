from __future__ import annotations

"""Read-only 累计纳元晶 observation for the #835 information window.

The challenge page proves which activity is on screen. Xutian uses the fixed
wallet currency type 12, shared with its exchange activity adapter. The reader
only observes an already initialized WalletData; it never opens a game page,
invokes Lua, or sends input. A failed read stays unavailable in the overlay.
"""

from datetime import datetime
import time
from typing import Any

from backend.core.fanxiu.activity.xutian_palace_instrumentation import (
    XUTIAN_PALACE_CURRENCY_TYPE,
)
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
from backend.core.fanxiu.instrumentation.wallet import read_wallet_currency_snapshot


class XutianCurrencyReader:
    """Read fresh wallet values while retaining only the process fast-path hint.

    A process-cache miss invalidates the hint and permits one rediscovery. A
    new game process, unloaded wallet, or changed currency model still needs
    live validation by the wallet provider. Page exit/re-entry was verified in
    two live #835 batches; process restart still needs live validation.
    """

    def __init__(self) -> None:
        self._runtime_warm = False

    def read(self, *, now: float | None = None) -> dict[str, Any]:
        timestamp = float(now if now is not None else time.time())
        started = time.perf_counter()
        try:
            for attempt in range(2):
                try:
                    snapshot = read_wallet_currency_snapshot(
                        XUTIAN_PALACE_CURRENCY_TYPE,
                        allow_discovery=not self._runtime_warm,
                    )
                    break
                except FanxiuRuntimeMemoryError as exc:
                    if attempt or exc.code != "process_cache_miss":
                        raise
                    self._runtime_warm = False
        except Exception as exc:
            self._runtime_warm = False
            return {
                "ok": False,
                "name": "纳元晶",
                "reason": f"{type(exc).__name__}: {exc}",
                "captured_at": timestamp,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        try:
            current = int(snapshot["exchange_currency"])
            cumulative = int(snapshot["cumulative_currency"])
            captured_at = snapshot.get("captured_at")
            try:
                captured_timestamp = datetime.fromisoformat(str(captured_at)).timestamp()
            except (TypeError, ValueError):
                captured_timestamp = timestamp
        except (KeyError, TypeError, ValueError) as exc:
            self._runtime_warm = False
            return {
                "ok": False,
                "name": "纳元晶",
                "reason": f"invalid_wallet_snapshot: {exc}",
                "captured_at": timestamp,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        self._runtime_warm = True
        return {
            "ok": True,
            "name": "纳元晶",
            "current": current,
            "cumulative": cumulative,
            "currency_type": XUTIAN_PALACE_CURRENCY_TYPE,
            "captured_at": captured_timestamp,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "source": "runtime_memory",
            "read_only": True,
        }


__all__ = ["XutianCurrencyReader"]
