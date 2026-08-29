from __future__ import annotations

"""Pure yield-ledger and bounded batch planning for 天地弈局."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.core.fanxiu.activity.exchange_planning import (
    ExchangeYieldScatterSample,
    fit_exchange_yield_scatter_model,
)
from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju_count import (
    TIANDI_YIJU_MAX_BATCH_ROUNDS,
)


TIANDI_YIJU_YIELD_LEDGER_KEY = "tiandi_yiju_yield_samples_v1"
TIANDI_YIJU_YIELD_LEDGER_LIMIT = 32


@dataclass(frozen=True, slots=True)
class TiandiYijuBatchPlan:
    estimated_remaining_rounds: int | None
    challenge_batch_rounds: int
    supply_target_rounds: int
    planning_mode: str


def load_tiandi_yiju_yield_samples(
    evidence: Mapping[str, Any],
    *,
    occurrence_instance_key: str,
    allowed_feature_keys: set[str],
) -> list[ExchangeYieldScatterSample]:
    """Load only occurrence-bound, internally verified ledger rows."""

    result: list[ExchangeYieldScatterSample] = []
    for raw in evidence.get(TIANDI_YIJU_YIELD_LEDGER_KEY) or ():
        if not isinstance(raw, Mapping):
            continue
        usage = raw.get("feature_item_usage")
        usage = usage if isinstance(usage, Mapping) else {}
        if (
            int(raw.get("version") or 0) != 1
            or str(raw.get("occurrence_instance_key") or "")
            != str(occurrence_instance_key)
            or not bool(raw.get("same_process_verified"))
            or int(raw.get("process_pid") or 0) <= 0
            or int(raw.get("process_start_ticks") or 0) <= 0
            or int(raw.get("rounds") or 0) <= 0
            or int(raw.get("currency_delta") or 0) <= 0
        ):
            continue
        if bool(raw.get("plain")):
            normalized_usage: tuple[tuple[str, int], ...] = ()
        elif bool(raw.get("feature_usage_known")) and set(usage).issubset(
            allowed_feature_keys
        ):
            normalized_usage = tuple(
                sorted((str(key), int(value)) for key, value in usage.items())
            )
        else:
            # Unknown boosted rows remain historical evidence but are not
            # mislabeled as plain or fitted without an authoritative spec.
            continue
        result.append(
            ExchangeYieldScatterSample(
                exchange_currency_delta=int(raw["currency_delta"]),
                attempt_count=int(raw["rounds"]),
                feature_item_usage=normalized_usage,
            )
        )
    return result


def append_tiandi_yiju_yield_evidence(
    evidence: Mapping[str, Any],
    *,
    occurrence_instance_key: str,
    rounds: int,
    currency_delta: int,
    process_identity: tuple[Any, ...],
    feature_item_usage: Mapping[str, int] | None,
) -> dict[str, Any]:
    """Append one authoritative batch while retaining large historical rows."""

    usage = (
        {str(key): int(value) for key, value in feature_item_usage.items()}
        if feature_item_usage is not None
        else None
    )
    row = {
        "version": 1,
        "occurrence_instance_key": str(occurrence_instance_key),
        "rounds": int(rounds),
        "currency_delta": int(currency_delta),
        "feature_item_usage": usage or {},
        "feature_usage_known": usage is not None,
        "plain": usage == {},
        "same_process_verified": True,
        "process_pid": int(process_identity[2]),
        "process_start_ticks": int(process_identity[3]),
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if row["rounds"] <= 0 or row["currency_delta"] <= 0:
        raise ValueError("天地弈局兑币样本必须具有正次数与正增量")
    rows = [
        dict(item)
        for item in evidence.get(TIANDI_YIJU_YIELD_LEDGER_KEY) or ()
        if isinstance(item, Mapping)
    ]
    rows.append(row)
    if len(rows) > TIANDI_YIJU_YIELD_LEDGER_LIMIT:
        newest = rows[-16:]
        largest = sorted(
            rows[:-16], key=lambda item: int(item.get("rounds") or 0)
        )[-16:]
        rows = largest + newest
    updated = dict(evidence)
    updated[TIANDI_YIJU_YIELD_LEDGER_KEY] = rows
    return updated


def plan_tiandi_yiju_batch_rounds(
    *,
    required_currency: int,
    yield_samples: Iterable[ExchangeYieldScatterSample] = (),
    feature_specs: Iterable[Any] = (),
    feature_item_fractions: Mapping[str, float] | None = None,
) -> TiandiYijuBatchPlan:
    """Choose a deterministic 10%..50% batch from all supplied scatter rows."""

    samples = tuple(yield_samples)
    specs = tuple(feature_specs)
    allowed_features = {str(spec.key) for spec in specs}
    observed_features = {
        str(key)
        for sample in samples
        for key, _count in sample.feature_item_usage
    }
    if not observed_features.issubset(allowed_features):
        missing = sorted(observed_features - allowed_features)
        raise ValueError(f"天地弈局散点缺少道具增益规格：{missing}")
    model = fit_exchange_yield_scatter_model(samples, feature_specs=specs)
    if model is None or model.plain_attempts <= 0:
        return TiandiYijuBatchPlan(
            estimated_remaining_rounds=None,
            challenge_batch_rounds=TIANDI_YIJU_MAX_BATCH_ROUNDS,
            supply_target_rounds=TIANDI_YIJU_MAX_BATCH_ROUNDS,
            planning_mode="probe",
        )

    estimated = model.estimate_attempts(
        int(required_currency),
        feature_item_fractions=feature_item_fractions,
    )
    if estimated <= TIANDI_YIJU_MAX_BATCH_ROUNDS:
        return TiandiYijuBatchPlan(
            estimated_remaining_rounds=estimated,
            challenge_batch_rounds=TIANDI_YIJU_MAX_BATCH_ROUNDS,
            supply_target_rounds=TIANDI_YIJU_MAX_BATCH_ROUNDS,
            planning_mode="tail_100",
        )
    if model.total_attempts >= 1000:
        progress_percent = 50
    elif model.total_attempts >= 200:
        progress_percent = 25
    else:
        progress_percent = 10
    challenge_batch = max(1, (estimated * progress_percent + 99) // 100)
    return TiandiYijuBatchPlan(
        estimated_remaining_rounds=estimated,
        challenge_batch_rounds=challenge_batch,
        # Supply navigation is expensive; cover the full estimate once while
        # keeping each irreversible challenge batch bounded separately.
        supply_target_rounds=estimated,
        planning_mode=f"evidence_{progress_percent}pct",
    )


__all__ = [
    "TIANDI_YIJU_YIELD_LEDGER_KEY",
    "TIANDI_YIJU_YIELD_LEDGER_LIMIT",
    "TiandiYijuBatchPlan",
    "append_tiandi_yiju_yield_evidence",
    "load_tiandi_yiju_yield_samples",
    "plan_tiandi_yiju_batch_rounds",
]
