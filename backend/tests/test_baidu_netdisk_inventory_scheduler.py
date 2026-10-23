from backend.core.baidu_netdisk.inventory_scheduler import select_inventory_resource


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
