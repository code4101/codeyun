from backend.core.fanxiu.resource_target_planner import (
    ResourceActionPoint,
    ResourceOption,
    ResourceTargetContext,
    estimate_resource_gain,
    plan_next_resource_action,
)


CONTEXT = ResourceTargetContext("event", 1701, (1, 2, 3, 4))


def _option(
    resource_id=9,
    name="兽神饲灵丸",
    available=10,
    gift_gains=((4, 100),),
    **kwargs,
):
    return ResourceOption(resource_id, name, available, gift_gains, **kwargs)


def _pair(*, action="a", resource=9, before=100, after=200, inventory=10):
    return (
        ResourceActionPoint(
            "event", 1701, (1, 2, 3, 4), action, "before_action", resource,
            1, before, inventory, (10, 20, 30, 40, 50),
        ),
        ResourceActionPoint(
            "event", 1701, (1, 2, 3, 4), action, "after_action", resource,
            1, after, inventory - 1, (10, 20, 30, 140, 50),
        ),
    )


def test_configured_gain_sums_all_gifts_applicable_to_selected_pet():
    option = _option(gift_gains=((1, 4), (2, 4), (3, 2), (5, 999)))
    assert option.configured_gain_for(CONTEXT) == 10


def test_effect_value_key_is_not_treated_as_pet_type():
    another_pet = ResourceTargetContext("event", 9999, (4,))
    option = _option(gift_gains=((1, 4), (2, 4), (3, 2), (4, 100)))
    assert option.configured_gain_for(another_pet) == 100


def test_special_resource_is_sampled_before_batching():
    plan = plan_next_resource_action(
        context=CONTEXT,
        target=1000,
        current=100,
        resources=[_option(priority=100, calibration_required=True)],
    )
    assert (plan.kind, plan.mode, plan.resource_id, plan.quantity) == (
        "act", "calibrate", 9, 1,
    )


def test_sampled_high_priority_resource_forms_bounded_batch():
    plan = plan_next_resource_action(
        context=CONTEXT,
        target=1000,
        current=100,
        resources=[_option(priority=100, calibration_required=True)],
        observations=_pair(),
        max_batch_size=5,
    )
    assert (plan.resource_id, plan.quantity, plan.expected_delta_max) == (9, 5, 500)


def test_multi_sample_calibration_remains_single_step():
    plan = plan_next_resource_action(
        context=CONTEXT,
        target=1000,
        current=200,
        resources=[_option(priority=100, calibration_required=True, minimum_samples=3)],
        observations=_pair(action="first") + _pair(action="second"),
    )
    assert (plan.mode, plan.quantity) == ("calibrate", 1)


def test_exact_tail_beats_preferred_large_resource():
    plan = plan_next_resource_action(
        context=CONTEXT,
        target=235,
        current=200,
        resources=[
            _option(priority=100),
            _option(2, "普通丸", 100, ((1, 2), (2, 2), (3, 1))),
        ],
    )
    assert (plan.resource_id, plan.quantity, plan.expected_overflow_max) == (2, 7, 0)


def test_smallest_provable_overshoot_is_final_tail():
    plan = plan_next_resource_action(
        context=CONTEXT,
        target=211,
        current=200,
        resources=[
            _option(priority=100),
            _option(2, "绝品丸", 10, ((1, 10), (2, 10))),
        ],
    )
    assert (plan.resource_id, plan.quantity, plan.mode) == (2, 1, "tail")
    assert plan.expected_overflow_max == 9


def test_config_drift_forces_single_step_replanning():
    observations = _pair(before=100, after=220)
    option = _option(priority=100)
    estimate = estimate_resource_gain(option, observations, context=CONTEXT)
    assert estimate is not None and estimate.config_drift is True
    plan = plan_next_resource_action(
        context=CONTEXT,
        target=1000,
        current=220,
        resources=[option],
        observations=observations,
    )
    assert plan.quantity == 1


def test_unclosed_before_point_blocks_idempotent_repeat():
    pending = _pair()[0]
    plan = plan_next_resource_action(
        context=CONTEXT,
        target=1000,
        current=100,
        resources=[_option()],
        observations=[pending],
    )
    assert plan.kind == "blocked"
    assert "拒绝重复消耗" in plan.reason


def test_target_reached_is_idempotent_complete():
    plan = plan_next_resource_action(
        context=CONTEXT,
        target=1400,
        current=1400,
        resources=[_option()],
    )
    assert (plan.kind, plan.quantity) == ("complete", 0)


def test_pill_without_applicable_gift_is_never_used():
    plan = plan_next_resource_action(
        context=ResourceTargetContext("event", 1701, (1, 2, 3)),
        target=200,
        current=100,
        resources=[_option(gift_gains=((4, 100),))],
    )
    assert plan.kind == "blocked"


def test_pending_from_another_selected_pet_does_not_block():
    unrelated = ResourceActionPoint(
        "event", 9999, (4,), "other", "before_action", 9, 1, 100, 10,
        (10, 20, 30, 40, 50),
    )
    plan = plan_next_resource_action(
        context=CONTEXT,
        target=200,
        current=100,
        resources=[_option()],
        observations=[unrelated],
    )
    assert plan.kind == "act"
