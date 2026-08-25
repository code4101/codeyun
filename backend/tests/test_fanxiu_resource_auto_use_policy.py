from backend.core.fanxiu.data_annotation.tasks.resource_auto_use_policy import (
    EXPECTED_STORAGE_SETTINGS,
    STORAGE_TERMINAL_TOAST,
    decide_storage_bag_round,
    plan_pet_quick_swallow,
    plan_talisman_quick_upgrade,
    verify_pet_quick_swallow_effect,
    verify_progress_effect,
)


def _storage(**overrides):
    payload = {
        "complete": True,
        "quick_settings": dict(EXPECTED_STORAGE_SETTINGS),
        "completed_rounds": 2,
        "max_rounds": 3,
    }
    payload.update(overrides)
    return payload


def test_storage_does_not_treat_two_rounds_as_complete():
    assert decide_storage_bag_round(_storage()).action == "execute"


def test_storage_accepts_exact_empty_toast():
    decision = decide_storage_bag_round(
        _storage(toast=STORAGE_TERMINAL_TOAST)
    )
    assert decision.action == "complete"


def test_storage_fixed_point_requires_same_signature_and_quiet_window():
    assert decide_storage_bag_round(_storage(
        panel_signature_before="526:settings:v1",
        panel_signature_after="526:settings:v1",
        stable_seconds=10,
        stable_polls=3,
        result_transition_seen=False,
        transition_pending=False,
    )).action == "complete"
    assert decide_storage_bag_round(_storage(
        panel_signature_before="526:settings:v1",
        panel_signature_after="526:settings:v2",
        stable_seconds=20,
        stable_polls=5,
    )).action == "execute"


def test_storage_rejects_open_box_or_budget_exhaustion():
    settings = dict(EXPECTED_STORAGE_SETTINGS)
    settings["1"] = 1
    assert decide_storage_bag_round(_storage(quick_settings=settings)).action == "fail"
    assert decide_storage_bag_round(_storage(completed_rounds=3)).action == "fail"


def test_pet_quick_swallow_allows_only_ordinary_owned_pet_items():
    decision = plan_pet_quick_swallow({
        "complete": True,
        "source": "PetData.CheckPetCardUpCount",
        "candidates": [{
            "pet_id": 101,
            "therion_type": 0,
            "owned": True,
            "upgrade_count": 3,
            "resources": [{
                "kind": "ordinary_pet_upgrade_item",
                "item_id": 9001,
                "quantity": 7,
            }],
        }],
    })
    assert decision.action == "execute"
    assert decision.expected_units == 3


def test_pet_quick_swallow_rejects_therion_and_self_select_resources():
    base = {
        "complete": True,
        "source": "PetData.CheckPetCardUpCount",
        "candidates": [{
            "pet_id": 101,
            "therion_type": 1,
            "owned": True,
            "upgrade_count": 1,
            "resources": [{
                "kind": "ordinary_pet_upgrade_item",
                "item_id": 9001,
                "quantity": 1,
            }],
        }],
    }
    assert plan_pet_quick_swallow(base).action == "fail"
    base["candidates"][0]["therion_type"] = 0
    base["candidates"][0]["resources"][0]["kind"] = "self_select"
    assert plan_pet_quick_swallow(base).action == "fail"


def test_talisman_quick_upgrade_allows_native_bounded_material_batch():
    decision = plan_talisman_quick_upgrade({
        "complete": True,
        "source": "TalismanModel.GetAllUpgradeableTalismanList",
        "candidates": [{
            "talisman_id": 501,
            "category": "后天古宝",
            "owned": True,
            "active": True,
            "upgrade_count": 12,
            "resources": [{
                "kind": "talisman_upgrade_material",
                "item_id": 7001,
                "quantity": 48,
            }],
        }],
    })
    assert decision.action == "execute"
    assert decision.expected_units == 12


def test_talisman_quick_upgrade_rejects_unknown_currency_and_over_50():
    candidate = {
        "talisman_id": 501,
        "category": "法宝",
        "owned": True,
        "active": True,
        "upgrade_count": 51,
        "resources": [{"kind": "unknown", "item_id": 7, "quantity": 1}],
    }
    snapshot = {
        "complete": True,
        "source": "TalismanModel.GetAllUpgradeableTalismanList",
        "candidates": [candidate],
    }
    assert plan_talisman_quick_upgrade(snapshot).action == "fail"
    candidate["upgrade_count"] = 1
    assert plan_talisman_quick_upgrade(snapshot).action == "fail"


def test_progress_verification_is_monotonic_and_scoped():
    assert verify_progress_effect(
        {101: 3, 102: 8}, {101: 4, 102: 8}, expected_ids={101}
    )
    assert not verify_progress_effect(
        {101: 3, 102: 8}, {101: 4, 102: 9}, expected_ids={101}
    )
    assert not verify_progress_effect(
        {101: 3}, {101: 2}, expected_ids={101}
    )


def _pet_effect_snapshots():
    before = {
        "complete": True,
        "source": "PetData.CheckPetCardUpCount",
        "candidate_count": 1,
        "candidates": [{
            "pet_id": 101,
            "therion_type": 0,
            "owned": True,
            "current_level": 1,
            "target_level": 3,
            "upgrade_count": 2,
            "resources": [{
                "kind": "ordinary_pet_upgrade_item",
                "item_id": 9001,
                "quantity": 6,
            }],
        }],
        "entity_progress": {101: 1, 202: 8},
        "material_totals": {9001: 6},
        "inventory_counts": {9001: 6, 9002: 7},
    }
    after = {
        "complete": True,
        "source": "PetData.CheckPetCardUpCount",
        "candidate_count": 0,
        "candidates": [],
        "entity_progress": {101: 3, 202: 8},
        "material_totals": {},
        "inventory_counts": {9001: 0, 9002: 7},
    }
    return before, after


def test_pet_effect_requires_exact_level_inventory_and_candidate_convergence():
    before, after = _pet_effect_snapshots()
    assert verify_pet_quick_swallow_effect(before, after)

    after["entity_progress"][101] = 2
    assert not verify_pet_quick_swallow_effect(before, after)
    after["entity_progress"][101] = 3

    after["inventory_counts"][9001] = 1
    assert not verify_pet_quick_swallow_effect(before, after)
    after["inventory_counts"][9001] = 0

    after["candidates"] = [{"pet_id": 101}]
    after["candidate_count"] = 1
    assert not verify_pet_quick_swallow_effect(before, after)


def test_pet_effect_rejects_disappearance_without_progress_or_scoped_stability():
    before, after = _pet_effect_snapshots()
    after["entity_progress"][101] = 1
    assert not verify_pet_quick_swallow_effect(before, after)

    before, after = _pet_effect_snapshots()
    after["entity_progress"][202] = 9
    assert not verify_pet_quick_swallow_effect(before, after)


def test_pet_effect_accepts_json_object_string_keys_but_rejects_identity_drift():
    before, after = _pet_effect_snapshots()
    before["entity_progress"] = {"101": 1, "202": 8}
    before["material_totals"] = {"9001": 6}
    before["inventory_counts"] = {"9001": 6, "9002": 7}
    after["entity_progress"] = {"101": 3, "202": 8}
    after["inventory_counts"] = {"9001": 0, "9002": 7}
    assert verify_pet_quick_swallow_effect(before, after)

    after["entity_progress"].pop("202")
    assert not verify_pet_quick_swallow_effect(before, after)


def test_progress_verification_can_require_exact_targets():
    assert verify_progress_effect(
        {101: 3, 102: 8},
        {101: 5, 102: 8},
        expected_ids={101},
        expected_targets={101: 5},
    )
    assert not verify_progress_effect(
        {101: 3, 102: 8},
        {101: 6, 102: 8},
        expected_ids={101},
        expected_targets={101: 5},
    )
