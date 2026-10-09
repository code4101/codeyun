"""Node-prefix arithmetic and spend gates; no game UI is simulated."""
import pytest

from backend.core.fanxiu.activity.shequn_lianti import plan_shequn_lianti_training_batch
from backend.core.fanxiu.activity.shequn_lianti import resolve_shequn_lianti_training_binding
from backend.core.fanxiu.activity.shequn_lianti import verify_shequn_lianti_score_delta


def _plan(*, score=0, target=25, stock=20, rates=None, auto_buy=False):
    return plan_shequn_lianti_training_batch(
        goal={"available": True, "complete": True, "activity_progress": score,
              "training_binding_verified": True, "training_activity_id": 43403,
              "target_score": target, "progress_is_exact": True},
        dialog={"complete": True, "cost_path_complete": True,
                "activity_id": 43403, "auto_buy_enabled": auto_buy,
                "multiplicity_enabled": False, "base_id": 1,
                "current_node": 5, "maximum_nodes": 3, "stage": 2,
                "normal_item_id": 5030001, "breakthrough_item_id": 5030002,
                "cost_nodes": [
                    {"node_id": 6, "item_id": 5030001, "quantity": 10, "required_stage": 1},
                    {"node_id": 7, "item_id": 5030002, "quantity": 1, "required_stage": 2},
                    {"node_id": 8, "item_id": 5030001, "quantity": 10, "required_stage": 2}]},
        inventory={5030001: stock, 5030002: 1},
        score_per_item={5030001: 2, 5030002: 5} if rates is None else rates)


def test_shortest_prefix_includes_both_materials_and_stops_at_target():
    result = _plan()
    assert result["selected_nodes"] == 2
    assert result["material_costs"] == {5030001: 10, 5030002: 1}
    assert result["expected_score"] == 25


def test_reached_target_never_consumes_for_rank_position():
    assert _plan(score=25)["selected_nodes"] == 0


def test_missing_score_rule_and_purchase_toggle_fail_closed():
    with pytest.raises(ValueError):
        _plan(rates={5030001: 2})
    with pytest.raises(ValueError):
        _plan(auto_buy=True)


def test_insufficient_stock_and_unreachable_target_do_not_emit_partial_spend():
    assert _plan(stock=9)["kind"] == "insufficient_material"
    assert _plan(target=100)["kind"] == "reachable_path_below_target"


def test_child_binding_requires_explicit_follow_and_exact_native_period():
    rows = [{'id': 8043401, 'follow': [43403], 'sameActGroup': 22, 'crossGroup': 8},
            {'id': 43403, 'activityId': 4, 'subType': 10341, 'sameActGroup': 22, 'crossGroup': 8}]
    period = {'start_time_ms': 100, 'end_time_ms': 200}
    instance = {'id': 43403400004, 'activityId': 43403, 'activityType': 4,
                'startTime': 100, 'endTime': 200}
    arguments = dict(parent_id=8043401, period=period, config_rows=rows)
    assert resolve_shequn_lianti_training_binding(**arguments, instances=[instance])['training_activity_id'] == 43403
    for instances in ([], [instance, instance], [{**instance, 'endTime': 201}]):
        with pytest.raises(ValueError):
            resolve_shequn_lianti_training_binding(**arguments, instances=instances)
    rows[0]['follow'] = []
    with pytest.raises(ValueError):
        resolve_shequn_lianti_training_binding(**arguments, instances=[instance])


def test_score_delta_mismatch_does_not_invent_a_multiplier():
    before = {'progress_is_exact': True, 'occurrence_key': 'current', 'activity_progress': 0}
    arguments = dict(before_goal=before, item_id=5030001, material_before=214388,
                     material_after=214380, native_rate=100)
    mismatch = verify_shequn_lianti_score_delta(
        **arguments, after_goal={**before, 'activity_progress': 4637})
    assert not mismatch['verified']
    assert mismatch['native_expected_delta'] == 800
    assert verify_shequn_lianti_score_delta(
        **arguments, after_goal={**before, 'activity_progress': 800})['verified']
    with pytest.raises(ValueError):
        verify_shequn_lianti_score_delta(
            **arguments, after_goal={**before, 'occurrence_key': 'other', 'activity_progress': 800})
