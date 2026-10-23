from backend.core.baidu_netdisk.inventory_scheduler import is_refill_finish_candidate, reserve_refill_finish_turn, select_inventory_resource


def row(mid, size=0, frontier=1):
    return {"material_id": mid, "known_bytes": size, "pending": list(range(frontier)), "status": "scanning"}


def test_large_existing_package_does_not_block_unstarted_small_materials():
    large = row("large", 59_000_000_000, 58)
    resources = [large, row("b"), row("a")]
    first = select_inventory_resource(resources)
    assert first["material_id"] == "a"
    first["inventory_turns"] = 1
    assert select_inventory_resource(resources)["material_id"] == "b"


def test_small_growing_tree_cannot_starve_the_other_materials():
    resources = [row("small"), row("large", 100)]
    picked = []
    for _ in range(6):
        selected = select_inventory_resource(resources)
        picked.append(selected["material_id"])
        selected["inventory_turns"] = selected.get("inventory_turns", 0) + 1
        selected["pending"].append(1)  # nested tree keeps expanding
    assert picked == ["small", "large"] * 3


def test_complete_and_exhausted_materials_never_reenter_inventory():
    complete = row("done")
    complete["status"] = "size_complete"
    assert select_inventory_resource([complete, row("empty", frontier=0)]) is None


def test_selection_preserves_frontiers_and_incomplete_status():
    resource = row("unknown", 10, 7)
    before = dict(resource)
    assert select_inventory_resource([resource]) is resource
    assert resource == before
    assert resource["status"] == "scanning"


def test_estimate_only_breaks_inventory_ties_and_never_overrules_measured_bound():
    small = {**row("z"), "estimate_mb": 820}
    huge = {**row("a"), "estimate_mb": 5000}
    misleading = {**row("old", 59_000_000_000), "estimate_mb": 500}
    assert select_inventory_resource([huge, misleading, small]) is small
    small["inventory_turns"] = 1
    assert select_inventory_resource([huge, misleading, small]) is huge


def test_short_explored_frontier_gets_one_extra_turn_but_cannot_monopolize():
    almost = {**row("almost", 2_000_000), "directories_scanned": 3, "inventory_turns": 1}
    untouched = row("untouched")
    assert select_inventory_resource([untouched, almost]) is almost
    almost["inventory_turns"] += 1  # frontier is still one: no unlimited credit
    assert select_inventory_resource([untouched, almost]) is untouched


def test_large_or_expanding_frontier_does_not_receive_finish_credit():
    untouched = row("new")
    for candidate in [{**row("huge", 59_000_000_000), "directories_scanned": 250, "inventory_turns": 1},
                      {**row("branches", 100, 10), "directories_scanned": 3, "inventory_turns": 1}]:
        assert select_inventory_resource([candidate, untouched]) is untouched


def test_empty_slots_finish_explored_material_after_multiple_fair_turns():
    almost = {**row("almost", 1_300_000_000), "directories_scanned": 6, "inventory_turns": 2}
    untouched = row("new")
    assert select_inventory_resource([untouched, almost], refill_needed=True) is almost
    # A nested one-directory frontier can continue, but only for nine scans.
    for _ in range(3):
        reserve_refill_finish_turn(almost)
        almost["inventory_turns"] += 1
    assert not is_refill_finish_candidate(almost)
    assert select_inventory_resource([untouched, almost], refill_needed=True) is untouched


def test_occupied_slots_and_wide_frontiers_keep_fair_order():
    new = row("new")
    almost = {**row("almost", 100), "directories_scanned": 6, "inventory_turns": 2}
    assert select_inventory_resource([almost, new]) is new
    for candidate in [dict(almost, pending=[1, 2, 3, 4])]:
        assert select_inventory_resource([candidate, new], refill_needed=True) is new


def test_large_bytes_do_not_hide_a_nearly_exhausted_directory_frontier():
    nearest = {**row("nearest", 4 * 1024**3), "directories_scanned": 3, "inventory_turns": 1}
    other = {**row("other", 100, 2), "directories_scanned": 3, "inventory_turns": 1}
    assert select_inventory_resource([other, nearest], refill_needed=True) is nearest


def test_legacy_finishing_turn_retains_spent_credit_and_cannot_loop_forever():
    legacy = {**row("legacy"), "directories_scanned": 6, "inventory_turns": 2, "refill_finish_turn": 1}
    reserve_refill_finish_turn(legacy)
    assert legacy["refill_finish_reserved_dirs"] == 6
    reserve_refill_finish_turn(legacy)
    assert not is_refill_finish_candidate(legacy)
    import pytest
    with pytest.raises(ValueError):
        reserve_refill_finish_turn(legacy)


def test_all_explored_materials_finish_nearest_frontier_without_fixed_cutoff():
    nearest = {**row("near", 100, 4), "directories_scanned": 3, "inventory_turns": 2}
    wide = {**row("wide", 1, 25), "directories_scanned": 3, "inventory_turns": 1}
    assert select_inventory_resource([wide, nearest], refill_needed=True) is nearest
    for _ in range(3):
        reserve_refill_finish_turn(nearest, allow_wide=True)
    assert not is_refill_finish_candidate(nearest, allow_wide=True)
    assert select_inventory_resource([wide, nearest], refill_needed=True) is wide
