"""Fair directory work scheduling; download units remain whole material rows.

A deeply nested package must not monopolize inventory while every download slot
is empty. A worker performs a bounded directory quantum for the returned row,
then increments its durable ``inventory_turns`` and selects again. Completion
still requires exhausting that material's entire frontier and summing all bytes.
"""
from __future__ import annotations


def select_inventory_resource(resources: list[dict]) -> dict | None:
    """Pick an unfinished material fairly; prefer smaller known bounds within a round.

    Unknown totals are never download sizes. This function only chooses the next
    inventory work item, never a download or a completed package. Old progress
    without turn counters starts at zero without discarding any measured data.
    """
    pending = [r for r in resources if r.get("status") != "size_complete" and r.get("pending")]
    if not pending:
        return None
    def priority(row):
        turns = row.get("inventory_turns", 0)
        # One extra bounded turn may finish an already explored short frontier.
        # Without this credit, a nearly finished small package waits behind
        # every untouched root and all download slots can remain empty.
        finish_credit = (turns == 1 and row.get("directories_scanned", 0) > 0
                         and len(row["pending"]) <= 2 and row["known_bytes"] <= 1024**3)
        estimate = row.get("estimate_mb")
        estimate = estimate if type(estimate) in {int, float} and estimate > 0 else float("inf")
        return (0 if finish_credit else turns, 0 if finish_credit else 1, row["known_bytes"], estimate,
                len(row["pending"]), row["material_id"])
    return min(pending, key=priority)
