"""Fair directory work scheduling; download units remain whole material rows.

A deeply nested package must not monopolize inventory while every download slot
is empty. A worker performs a bounded directory quantum for the returned row,
then increments its durable ``inventory_turns`` and selects again. Completion
still requires exhausting that material's entire frontier and summing all bytes.
"""
from __future__ import annotations


def is_refill_finish_candidate(row: dict, *, allow_wide: bool = False) -> bool:
    """Allow up to nine reserved directory scans to finish a short frontier.

    The worker reserves each three-directory quantum durably before scanning.
    A growing frontier cannot claim unlimited priority. Already measured bytes
    do not predict remaining directory work, so there is no byte-size cutoff.
    """
    return (row.get("directories_scanned", 0) > 0
            and len(row.get("pending", [])) > 0
            and (allow_wide or len(row["pending"]) <= 3)
            and row.get("refill_finish_reserved_dirs", 3 if "refill_finish_turn" in row else 0) < 9)


def reserve_refill_finish_turn(row: dict, *, allow_wide: bool = False) -> None:
    """Reserve a bounded finishing quantum; persist the row before provider IO."""
    if not is_refill_finish_candidate(row, allow_wide=allow_wide):
        raise ValueError("material has no remaining bounded finishing credit")
    used = row.get("refill_finish_reserved_dirs", 3 if "refill_finish_turn" in row else 0)
    row["refill_finish_reserved_dirs"] = used + 3
    row.setdefault("refill_finish_turn", row.get("inventory_turns", 0))


def select_inventory_resource(resources: list[dict], *, refill_needed: bool = False) -> dict | None:
    """Pick an unfinished material fairly; prefer smaller known bounds within a round.

    Unknown totals are never download sizes. This function only chooses the next
    inventory work item, never a download or a completed package. Old progress
    without turn counters starts at zero without discarding any measured data.
    """
    pending = [r for r in resources if r.get("status") != "size_complete" and r.get("pending")]
    if not pending:
        return None
    # Once every material has received exploratory work, a fixed short-frontier
    # cutoff can leave *all* rows ineligible and the download queue empty forever.
    # Spend the same bounded finishing credit on the nearest frontier instead.
    all_explored = all(r.get("directories_scanned", 0) > 0 for r in pending)
    def priority(row):
        turns = row.get("inventory_turns", 0)
        if refill_needed and is_refill_finish_candidate(row, allow_wide=all_explored):
            # Equal frontiers: a more deeply explored tree has stronger evidence
            # of approaching its leaves. Tiny known bytes can instead mean that
            # only its branching directories have been explored so far.
            return (-1, len(row["pending"]), -row.get("directories_scanned", 0), row["known_bytes"], 0, row["material_id"])
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
