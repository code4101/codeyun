"""Pure policy/validation contracts; no simulated game workflow."""
import pytest

from backend.core.fanxiu.activity.cultivation_choice import (
    CultivationChoiceError, plan_single_cultivation_choice, validate_cultivation_rule,
    committed_cultivation_choice,
)


def test_committed_choice_is_respected_without_replanning():
    rewards = [{"item_id": 1}, {"item_id": 2}]
    assert committed_cultivation_choice(rewards, 2) == 2
    assert committed_cultivation_choice(rewards, 0) is None
    with pytest.raises(CultivationChoiceError):
        committed_cultivation_choice(rewards, 3)
    with pytest.raises(CultivationChoiceError):
        committed_cultivation_choice(rewards + [{"item_id": 2}], 2)


def evidence(item, levels, phase="entry_milestone", category="recurring_resources"):
    return {"item_id": item, "milestones": [{"targets": [{"key": key, "level": level}
        for key, level in levels.items()], "phase": phase, "category": category,
        "summary": "documented reward", "sources": ["note:1"]}]}


def owned(item, levels):
    return {"item_id": item, "components": [
        {"kind": key.split(":")[0], "target_id": int(key.split(":")[1]),
         "dimension": key.split(":")[2], "rank": rank} for key, rank in levels.items()]}


def test_zero_is_valid_and_value_policy_beats_panel():
    rewards = [{"item_id": 1}, {"item_id": 2}]
    states = [owned(1, {"fashion:9:level": 0}), owned(2, {"pet:9:level": 4})]
    rules = {1: evidence(1, {"fashion:9:level": 8}),
             2: evidence(2, {"pet:9:level": 5}, "other", "panel")}
    result = plan_single_cultivation_choice(rewards, states, rules)
    assert result.item_id == 1
    assert result.comparisons[0]["copies_to_target"] is None


def test_bundle_requirements_are_joint_and_not_summed():
    key1, key2 = "fashion:1:level", "fashion:2:level"
    rules = {10: evidence(10, {key1: 5, key2: 5}),
             20: evidence(20, {"pet:1:level": 10}, "new_chain")}
    rewards = [{"item_id": 10}, {"item_id": 20}]
    states = [owned(10, {key1: 5, key2: 3}), owned(20, {"pet:1:level": 6})]
    assert plan_single_cultivation_choice(rewards, states, rules).item_id == 10
    states[0] = owned(10, {key1: 5, key2: 5})
    # Replan from actual progress; last period's selected item has no privilege.
    assert plan_single_cultivation_choice(rewards, states, rules).item_id == 20


def test_nonuniform_prerequisite_before_later_node():
    key = "talisman:123:stage"
    rule = evidence(1, {key: 16}, "increase")
    rule["milestones"] += evidence(1, {key: 24}, "new_chain")["milestones"]
    result = plan_single_cultivation_choice([{"item_id": 1}], [owned(1, {key: 8})], {1: rule})
    assert result.targets[0]["level"] == 16


def test_missing_progress_does_not_mean_unowned():
    with pytest.raises(CultivationChoiceError):
        plan_single_cultivation_choice([{"item_id": 1}], [], {1: evidence(1, {"pet:1:level": 5})})


def test_exhausted_known_nodes_do_not_invent_next_tier():
    with pytest.raises(CultivationChoiceError, match="已知节点"):
        plan_single_cultivation_choice([{"item_id": 1}], [owned(1, {"pet:1:level": 20})],
                                      {1: evidence(1, {"pet:1:level": 20})})


@pytest.mark.parametrize("bad", [True, 0, -1, "5", 5.5])
def test_model_cannot_invent_or_coerce_invalid_levels(bad):
    with pytest.raises(CultivationChoiceError):
        validate_cultivation_rule(evidence(1, {"pet:1:level": bad}), item_id=1,
            allowed_keys={"pet:1:level"}, source_refs={"note:1"})


def test_model_must_cite_real_sources_and_exact_dimension():
    with pytest.raises(CultivationChoiceError):
        validate_cultivation_rule(evidence(1, {"pet:1:pin": 5}), item_id=1,
            allowed_keys={"pet:1:level"}, source_refs={"note:1"})
    with pytest.raises(CultivationChoiceError):
        validate_cultivation_rule(evidence(1, {"pet:1:level": 5}), item_id=1,
            allowed_keys={"pet:1:level"}, source_refs={"note:2"})
