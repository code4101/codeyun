"""Resource-to-ranking estimates from observed batches; no game operations."""

from math import isfinite
from typing import Iterable


def estimate_pet_rank_inventory(resources: Iterable[dict], samples: Iterable[dict]) -> dict:
    """Estimate each resource independently; report unmeasured inventory explicitly.

    A sample's denominator is its submitted quantity. Inventory refunds and
    bonuses remain part of the observed yield rather than an assumed constant.
    """
    totals = {}
    base_rates = []
    for sample in samples:
        quantity = int(sample.get("quantity") or 0)
        delta = sample.get("rank_delta")
        # Zero is an observed yield, not a missing sample. Dropping zero-yield
        # batches systematically overestimates resources near aptitude caps.
        if quantity <= 0 or delta is None or not isfinite(float(delta)) or float(delta) < 0:
            continue
        key = int(sample["item_id"])
        prior_quantity, prior_delta = totals.get(key, (0, 0.0))
        totals[key] = prior_quantity + quantity, prior_delta + float(delta)
        base_total = float(sample.get("base_total") or 0)
        if isfinite(base_total) and base_total > 0:
            base_rates.append(float(delta) / base_total)
    measured_gain = 0.0
    unknown = []
    rows = []
    for resource in resources:
        count = int(resource["count"])
        if count <= 0:
            continue
        item_id = int(resource["item_id"])
        base_gain = float(resource.get("base_gain") or 0)
        if not isfinite(base_gain) or base_gain < 0:
            raise ValueError("Resource base gain must be finite and nonnegative")
        identity = {"item_id": item_id, "item_name": resource.get("item_name"),
                    "count": count, "base_gain": base_gain}
        if "base_gain" in resource and base_gain == 0:
            rows.append({**identity, "source": "capacity_exhausted",
                         "mean_rank_gain": 0.0, "estimated_gain": 0.0, "sample_quantity": 0})
            continue
        source = "same_item_sample"
        if item_id in totals:
            quantity, delta = totals[item_id]
            mean = delta / quantity
        elif base_rates and base_gain > 0:
            # An explicit estimate for unmeasured items, not a guaranteed lower
            # bound: use the lower observed points/base ratio to avoid treating
            # a special high-bonus batch as the default for every resource.
            quantity = 0
            mean = base_gain * min(base_rates)
            source = "pooled_base_estimate"
            unknown.append(item_id)
        else:
            unknown.append(item_id)
            rows.append({**identity, "source": "unestimated"})
            continue
        measured_gain += count * mean
        rows.append({**identity, "mean_rank_gain": mean,
                     "estimated_gain": count * mean, "sample_quantity": quantity, "source": source})
    return {"complete": all(r["source"] != "unestimated" for r in rows), "estimated_gain": measured_gain,
            "is_lower_bound": False,
            "unmeasured_item_ids": unknown, "resources": rows}
