from __future__ import annotations

"""Read-only 累计魔晶 observation for the information window.

#699（魔道入侵·自动除魔运行中）is the one page where a live game fact is more
useful than the scene number: 除魔 runs by itself there, so what the operator
watches is 活动期间累计魔晶 growing.  The value is read from the already
initialized Runtime wallet, so this reader invokes no Lua method, opens no
window, captures no frame and sends no input to the game.

The caller decides when to read: the information window only starts this loop
while the scene snapshot it already holds is #699.

The wallet entry belongs to the occurrence that is running, and 魔道入侵 keeps
two independent entries: a single-server occurrence pays #15 while a
cross-server one pays #17.  The type therefore comes from the same
``resolve_magic_invasion_shop_identity`` the activity flows use, resolved from
the Runtime #66 schedule and reused for
:data:`MAGIC_CRYSTAL_CURRENCY_TTL_SECONDS` so the overlay loop only pays that
read once per occurrence phase.
"""

from collections.abc import Iterable, Mapping
from datetime import datetime
import time
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError

MAGIC_CRYSTAL_NAME = "魔晶"
MAGIC_CRYSTAL_ACTIVITY_NAME = "魔道入侵"
MAGIC_CRYSTAL_CURRENCY_TTL_SECONDS = 300.0
# A process binding is cached for five minutes; a longer pause between reads
# must cost one rediscovery, not one wasted failed cycle.
MAGIC_CRYSTAL_CACHE_MISS_CODES = frozenset({"process_cache_miss"})
_READ_ATTEMPTS = 2


def _parse_timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _epoch_seconds(value: Any) -> float | None:
    """Accept both the Runtime's millisecond epoch and an ISO time string."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if value else None
    return _parse_timestamp(value)


def _occurrence_facts(period: Mapping[str, Any]) -> dict[str, Any] | None:
    start = _epoch_seconds(
        period.get("start_at") if period.get("start_at") else period.get("startTime")
    )
    end = _epoch_seconds(
        period.get("end_at") if period.get("end_at") else period.get("endTime")
    )
    name = str(period.get("name") or "")
    if not name or start is None or end is None:
        return None
    return {
        "name": name,
        "start": start,
        "end": end,
        "cross_count": int(period.get("cross_count") or period.get("serverCount") or 0),
        "activity_id": str(period.get("activity_id") or period.get("activityId") or ""),
        "base_id": str(period.get("base_id") or period.get("baseId") or ""),
        "little_name": str(period.get("little_name") or period.get("littleName") or ""),
    }


def magic_invasion_occurrence_rows(schedule: Mapping[str, Any]) -> list[Any]:
    """Return the schedule rows from the Runtime snapshot or a discovery file.

    The Runtime #66 read projects raw ``items`` while the activity-discovery
    artifact stores the same occurrences as ``occurrences``; both shapes carry
    the same identity fields.
    """

    for key in ("occurrences", "items"):
        rows = schedule.get(key)
        if isinstance(rows, (list, tuple)):
            return list(rows)
    return []


def active_magic_invasion_occurrence(
    occurrences: Iterable[Any] | None,
    *,
    now: float,
) -> dict[str, Any] | None:
    """Return the 魔道入侵 occurrence whose window currently contains ``now``.

    A cross-server phase and its single-server preliminary can both stay listed
    in the Runtime #66 schedule.  The running one is the widest cross count,
    which is also the occurrence whose wallet entry the auto-exorcism loop pays
    into once the cross-server phase has started.
    """

    active: list[dict[str, Any]] = []
    for period in occurrences or ():
        if not isinstance(period, Mapping):
            continue
        facts = _occurrence_facts(period)
        if facts is None or facts["name"] != MAGIC_CRYSTAL_ACTIVITY_NAME:
            continue
        if not facts["start"] <= now <= facts["end"]:
            continue
        active.append(facts)
    if not active:
        return None
    return max(active, key=lambda period: int(period.get("cross_count") or 0))


def _occurrence_projection(facts: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the occurrence identity the overlay reports as read evidence."""

    return {
        "activity_id": str(facts.get("activity_id") or ""),
        "base_id": str(facts.get("base_id") or ""),
        "little_name": str(facts.get("little_name") or ""),
        "cross_count": int(facts.get("cross_count") or 0),
        "start_at": datetime.fromtimestamp(float(facts["start"])).astimezone().isoformat(
            timespec="seconds"
        ),
        "end_at": datetime.fromtimestamp(float(facts["end"])).astimezone().isoformat(
            timespec="seconds"
        ),
    }


def resolve_magic_crystal_currency_type(
    *,
    now: float | None = None,
    allow_discovery: bool = False,
) -> tuple[int, dict[str, Any]]:
    """Resolve the running occurrence's wallet currency type from the Runtime."""

    from backend.core.fanxiu.activity.magic_invasion import (
        resolve_magic_invasion_shop_identity,
    )
    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )

    timestamp = float(now if now is not None else time.time())
    schedule = read_fanxiu_activity_runtime_schedule(
        allow_discovery=bool(allow_discovery)
    )
    occurrence = active_magic_invasion_occurrence(
        magic_invasion_occurrence_rows(schedule), now=timestamp
    )
    if occurrence is None:
        raise RuntimeError("魔道入侵 Runtime 日程没有进行中的 occurrence")
    _base_id, currency_type, _cross_count = resolve_magic_invasion_shop_identity(
        cross_count=int(occurrence.get("cross_count") or 0)
    )
    return int(currency_type), _occurrence_projection(occurrence)


class MagicCrystalReader:
    """Loop-side reader: one wallet value plus a cached currency identity."""

    def __init__(
        self,
        *,
        currency_ttl_seconds: float = MAGIC_CRYSTAL_CURRENCY_TTL_SECONDS,
    ) -> None:
        self._currency_ttl_seconds = max(0.0, float(currency_ttl_seconds))
        self._currency_type: int | None = None
        self._occurrence: dict[str, Any] = {}
        self._resolved_at = 0.0
        self._runtime_warm = False

    def read(self, *, now: float | None = None) -> dict[str, Any]:
        """Return the current balance, or a failed observation with its reason.

        Failures are values, not exceptions: rendering must fall back to the
        scene number instead of turning an unavailable wallet into a broken
        overlay.
        """

        timestamp = float(now if now is not None else time.time())
        started = time.perf_counter()
        try:
            currency_type, snapshot = self._read_with_rediscovery(now=timestamp)
            from backend.core.fanxiu.instrumentation.magic_invasion_auto_running import read_magic_invasion_counters
            counters = read_magic_invasion_counters()
            in_auto = bool(counters.get("is_in_auto"))
        except Exception as exc:
            # The failure may be a cold/expired process cache; retry discovery
            # on the next cycle instead of trusting this process binding.
            self._runtime_warm = False
            return {
                "ok": False,
                "name": MAGIC_CRYSTAL_NAME,
                "reason": f"{type(exc).__name__}: {exc}",
                "captured_at": timestamp,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        self._runtime_warm = True
        captured_at = _parse_timestamp(snapshot.get("captured_at"))
        return {
            "ok": True,
            "name": MAGIC_CRYSTAL_NAME,
            "current": int(snapshot.get("exchange_currency") or 0),
            "is_in_auto": in_auto,
            "cumulative": int(snapshot.get("cumulative_currency") or 0),
            "currency_type": int(currency_type),
            "occurrence": dict(self._occurrence),
            "captured_at": captured_at if captured_at is not None else timestamp,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "source": "runtime_memory",
            "read_only": True,
        }

    def _read_with_rediscovery(
        self, *, now: float
    ) -> tuple[int, dict[str, Any]]:
        """Read once, rediscovering the process binding when its cache expired."""

        for attempt in range(_READ_ATTEMPTS):
            try:
                currency_type = self._currency_type_for(now=now)
                return currency_type, self._read_wallet(currency_type)
            except FanxiuRuntimeMemoryError as exc:
                if attempt or exc.code not in MAGIC_CRYSTAL_CACHE_MISS_CODES:
                    raise
                self._runtime_warm = False
        raise AssertionError("unreachable")

    def _currency_type_for(self, *, now: float) -> int:
        if (
            self._currency_type is not None
            and now - self._resolved_at < self._currency_ttl_seconds
        ):
            return int(self._currency_type)
        currency_type, occurrence = resolve_magic_crystal_currency_type(
            now=now,
            allow_discovery=not self._runtime_warm,
        )
        self._currency_type = int(currency_type)
        self._occurrence = occurrence
        self._resolved_at = now
        return int(currency_type)

    def _read_wallet(self, currency_type: int) -> dict[str, Any]:
        from backend.core.fanxiu.instrumentation.wallet import (
            read_wallet_currency_snapshot,
        )

        return read_wallet_currency_snapshot(
            int(currency_type),
            allow_discovery=not self._runtime_warm,
            # WalletData.GetCurrencyByType returns zero for a currency the
            # dictionary does not hold yet; the overlay shows that same zero.
            missing_as_zero=True,
        )


__all__ = [
    "MAGIC_CRYSTAL_ACTIVITY_NAME",
    "MAGIC_CRYSTAL_CURRENCY_TTL_SECONDS",
    "MAGIC_CRYSTAL_NAME",
    "MagicCrystalReader",
    "active_magic_invasion_occurrence",
    "magic_invasion_occurrence_rows",
    "resolve_magic_crystal_currency_type",
]
