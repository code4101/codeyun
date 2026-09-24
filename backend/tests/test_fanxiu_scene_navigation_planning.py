"""Focused pure tests for the read-only scene navigation planner.

These exercise the shared candidate provider, probability semantics and the
explicit source==target / unknown-scene / no-path / cycle truncation states.
They construct no fake device or game loop; the cycle/depth cases drive the
algorithm through the runner's own provider seam.
"""

from __future__ import annotations

import json

import pytest

from backend.core.fanxiu.data_annotation.runner import create_behavior_tree_executor
from backend.core.fanxiu.data_annotation.scene_navigation import (
    posterior_landing_probabilities,
)


def _semantics_tree() -> list[dict]:
    """Two independent progress edges from #34 into #483 via #35 / #36."""

    return [
        {
            "type": "image",
            "id": 34,
            "title": "世界",
            "shapes": [
                {"id": "a", "title": "打开下方菜单", "sceneJumpTarget": "35(165)"},
                {"id": "b", "title": "走另一条", "sceneJumpTarget": "36(3)"},
            ],
        },
        {
            "type": "image",
            "id": 35,
            "title": "菜单",
            "shapes": [{"id": "c", "title": "灵兽", "sceneJumpTarget": "483(30)"}],
        },
        {
            "type": "image",
            "id": 36,
            "title": "侧门",
            "shapes": [{"id": "d", "title": "灵兽", "sceneJumpTarget": "483(30)"}],
        },
        {"type": "image", "id": 483, "title": "灵兽主页", "shapes": []},
    ]


def _candidate(source_scene_id: int, landing_id: int, *, title: str = "下一步") -> dict:
    return {
        "edge": {
            "source_id": source_scene_id,
            "shape": {"id": f"{source_scene_id}-{landing_id}", "title": title},
            "target_ids": [landing_id],
        },
        "score": (100, -1),
        "reason": "test",
        "landing_id": landing_id,
        "downstream_len": 1,
        "weight": 1.0,
        "progress_probability": 1.0,
        "expected_reachability": 0.1,
        "landing_probabilities": {landing_id: 1.0},
        "target_counts": {landing_id: 10},
        "declared_target_ids": [landing_id],
    }


def test_plan_reports_shared_candidates_and_separate_probability_semantics():
    runner = create_behavior_tree_executor()
    tree = _semantics_tree()
    logs_before = list(runner._status.get("logs") or [])
    tree_before = json.dumps(tree, ensure_ascii=False, sort_keys=True)
    random_before = runner._navigation_random.getstate()

    result = runner.plan_scene_navigation(tree, 34, 483, limit=3, max_downstream_steps=4)

    assert result["status"] == "ok"
    assert result["planning"] == "dynamic_next_step_candidates"
    assert result["read_only"] is True
    assert result["candidate_count"] == 2
    assert result["returned_count"] == 2
    titles = [item["action_title"] for item in result["candidates"]]
    assert titles == ["打开下方菜单", "走另一条"]

    weights = [item["single_step_progress_probability"] for item in result["candidates"]]
    shares = [item["selection_probability"] for item in result["candidates"]]
    assert sum(shares) == pytest.approx(1.0)
    assert shares[0] == pytest.approx(weights[0] / sum(weights))
    assert shares[1] == pytest.approx(weights[1] / sum(weights))
    assert shares[0] > shares[1]

    for item in result["candidates"]:
        assert sum(item["landing_probabilities"].values()) <= 1.0
        assert item["expected_landing_id"] in item["landing_probabilities"]
        assert item["expected_landing_probability"] == pytest.approx(
            item["landing_probabilities"][item["expected_landing_id"]]
        )
        # Distinct by design: sampling share, single-step posterior weight and
        # the value-iteration advantage must not be conflated.
        assert item["discounted_reachability_gain"] is not None
        assert 0.0 < item["single_step_progress_probability"] <= 1.0
        assert 0.0 <= item["selection_probability"] <= 1.0

    top = result["candidates"][0]
    assert top["declared_target_ids"] == [35]
    assert top["landing_observed_counts"] == {35: 165}
    assert top["downstream"]["status"] == "reached"
    assert top["downstream"]["start_scene_id"] == 35

    # Read/write isolation: planning neither mutates the tree nor runner status.
    assert json.dumps(tree, ensure_ascii=False, sort_keys=True) == tree_before
    assert list(runner._status.get("logs") or []) == logs_before
    assert runner._navigation_random.getstate() == random_before
    json.dumps(result, ensure_ascii=False)


def test_plan_and_execution_share_one_candidate_provider(monkeypatch):
    runner = create_behavior_tree_executor()
    tree = _semantics_tree()
    calls: list[tuple[int, bool | None]] = []
    original = runner._scene_next_edge_candidates

    def spy(tree_arg, current_scene_id, target_scene_id, **kwargs):
        calls.append((int(current_scene_id), kwargs.get("read_only")))
        return original(tree_arg, current_scene_id, target_scene_id, **kwargs)

    monkeypatch.setattr(runner, "_scene_next_edge_candidates", spy)

    plan = runner.plan_scene_navigation(tree, 34, 483, limit=3, max_downstream_steps=2)
    assert plan["status"] == "ok"
    assert any(read_only is True for _scene, read_only in calls)

    decision = runner._select_scene_next_edge(tree, 34, 483)
    assert decision is not None
    assert any(read_only is not True for _scene, read_only in calls)
    assert decision["edge"]["shape"]["title"] in {
        item["action_title"] for item in plan["candidates"]
    }


def test_plan_source_equals_target_is_explicit():
    runner = create_behavior_tree_executor()
    result = runner.plan_scene_navigation(_semantics_tree(), 34, 34)
    assert result["status"] == "already_at_target"
    assert result["candidates"] == []


def test_plan_unknown_scenes_are_explicit():
    runner = create_behavior_tree_executor()
    tree = _semantics_tree()
    assert runner.plan_scene_navigation(tree, 999, 483)["status"] == "unknown_source_scene"
    assert runner.plan_scene_navigation(tree, 34, 999)["status"] == "unknown_target_scene"
    assert runner.plan_scene_navigation(tree, 999, 999)["status"] == "unknown_source_scene"


def test_plan_limit_preserves_full_population_sampling_probability():
    runner = create_behavior_tree_executor()
    tree = _semantics_tree()
    full = runner.plan_scene_navigation(tree, 34, 483)
    limited = runner.plan_scene_navigation(tree, 34, 483, limit=1, max_downstream_steps=0)
    assert limited["candidate_count"] == 2
    assert limited["returned_count"] == 1
    assert limited["candidates"][0]["selection_probability"] == full["candidates"][0]["selection_probability"]
    assert limited["candidates"][0]["downstream"]["status"] == "depth_limit"


def test_plan_unreachable_target_reports_no_path():
    runner = create_behavior_tree_executor()
    tree = _semantics_tree() + [{"type": "image", "id": 20, "title": "孤岛", "shapes": []}]
    result = runner.plan_scene_navigation(tree, 34, 20)
    assert result["status"] == "no_path"
    assert result["candidates"] == []


def test_plan_downstream_cycle_is_truncated(monkeypatch):
    runner = create_behavior_tree_executor()
    tree = _semantics_tree()
    sequence = {
        34: [_candidate(34, 35)],
        35: [_candidate(35, 34)],
    }

    def stub(tree_arg, current_scene_id, target_scene_id, **kwargs):
        return list(sequence.get(int(current_scene_id), []))

    monkeypatch.setattr(runner, "_scene_next_edge_candidates", stub)
    result = runner.plan_scene_navigation(tree, 34, 483, limit=1, max_downstream_steps=5)

    downstream = result["candidates"][0]["downstream"]
    assert downstream["status"] == "cycle"
    assert [step["to_scene_id"] for step in downstream["steps"]] == [34, 35]


def test_plan_downstream_depth_limit_is_explicit(monkeypatch):
    runner = create_behavior_tree_executor()
    tree = _semantics_tree() + [
        {"type": "image", "id": 37, "title": "中转一", "shapes": [{"title": "去", "sceneJumpTarget": "483(1)"}]},
        {"type": "image", "id": 38, "title": "中转二", "shapes": [{"title": "去", "sceneJumpTarget": "483(1)"}]},
    ]
    sequence = {
        34: [_candidate(34, 35)],
        35: [_candidate(35, 37)],
        37: [_candidate(37, 38)],
        38: [_candidate(38, 483)],
    }

    def stub(tree_arg, current_scene_id, target_scene_id, **kwargs):
        return list(sequence.get(int(current_scene_id), []))

    monkeypatch.setattr(runner, "_scene_next_edge_candidates", stub)
    result = runner.plan_scene_navigation(tree, 34, 483, limit=1, max_downstream_steps=2)
    downstream = result["candidates"][0]["downstream"]
    assert downstream["status"] == "depth_limit"
    assert [step["to_scene_id"] for step in downstream["steps"]] == [37, 38]


def test_plan_rejects_invalid_limit():
    runner = create_behavior_tree_executor()
    with pytest.raises(ValueError):
        runner.plan_scene_navigation(_semantics_tree(), 34, 483, limit=0)


def test_posterior_declared_prior_without_observations_is_positive():
    # Declared-only landing: total declared mass alpha=1 over one destination,
    # one alpha reserved for the unknown.  The prior, not a count, gives 0.5.
    probabilities = posterior_landing_probabilities({}, [630])
    assert probabilities == {630: pytest.approx(0.5)}


def test_posterior_each_declared_destination_gets_one_effective_count():
    probabilities = posterior_landing_probabilities({}, [1, 2, 3])
    assert sum(probabilities.values()) == pytest.approx(3 / 4)
    for scene_id in (1, 2, 3):
        assert probabilities[scene_id] == pytest.approx(1 / 4)


def test_posterior_declared_prior_ignores_observation_error_discount():
    # confidence_z discounts observation error; a prior has no observation and
    # must not be collapsed back to zero just because several landings tie.
    probabilities = posterior_landing_probabilities({}, [1, 2], confidence_z=1.0)
    assert all(value > 0 for value in probabilities.values())
    assert sum(probabilities.values()) == pytest.approx(2 / 3)


def test_posterior_with_observations_keeps_count_posterior():
    probabilities = posterior_landing_probabilities({35: 165}, [35, 36])
    assert probabilities[35] == pytest.approx(165 / 167)
    assert probabilities[36] == pytest.approx(1 / 167)


def test_posterior_observed_landing_elsewhere_preserves_declared_prior():
    probabilities = posterior_landing_probabilities({69: 451}, [69, 20])
    assert probabilities[69] > 0
    assert probabilities[20] == pytest.approx(1 / 453)


def test_posterior_without_declared_or_observation_is_empty():
    assert posterior_landing_probabilities({}, []) == {}


def test_posterior_does_not_mutate_inputs():
    observed = {630: 0}
    declared = [630, 631]
    observed_before = dict(observed)
    declared_before = list(declared)
    posterior_landing_probabilities(observed, declared)
    assert observed == observed_before
    assert declared == declared_before


def _declared_only_direct_edge_tree() -> list[dict]:
    """A brand-new direct edge #34 -> #630 with no landing history at all."""

    return [
        {
            "type": "image",
            "id": 34,
            "title": "世界",
            "shapes": [
                {"id": "daily", "title": "旧日程", "sceneJumpTarget": "35(10)"},
                {"id": "xianyan", "title": "仙园游宴", "sceneJumpTarget": "630"},
            ],
        },
        {
            "type": "image",
            "id": 35,
            "title": "日程",
            "shapes": [
                {"id": "to630", "title": "去仙园", "sceneJumpTarget": "630(5)"}
            ],
        },
        {"type": "image", "id": 630, "title": "仙园游宴场景", "shapes": []},
    ]


def test_plan_selects_declared_only_direct_edge():
    runner = create_behavior_tree_executor()
    tree = _declared_only_direct_edge_tree()

    result = runner.plan_scene_navigation(tree, 34, 630, limit=3, max_downstream_steps=2)

    assert result["status"] == "ok"
    titles = [item["action_title"] for item in result["candidates"]]
    assert "仙园游宴" in titles
    direct = next(item for item in result["candidates"] if item["action_title"] == "仙园游宴")
    assert direct["declared_target_ids"] == [630]
    assert direct["expected_landing_id"] == 630
    assert direct["single_step_progress_probability"] > 0
    assert direct["selection_probability"] > 0
    assert result["candidates"][0]["action_title"] == "仙园游宴"


def test_posterior_generator_input_matches_list_with_observations():
    observed = {35: 165}
    declared = [35, 36]
    from_list = posterior_landing_probabilities(observed, declared)
    # A one-shot generator must be materialized once, not exhausted before the
    # observation branch re-reads it.
    from_generator = posterior_landing_probabilities(observed, iter(declared))
    assert from_generator == from_list
    assert from_generator[35] == pytest.approx(165 / 167)
    assert from_generator[36] == pytest.approx(1 / 167)


def test_posterior_generator_input_matches_list_without_observations():
    declared = [1, 2, 3]
    from_list = posterior_landing_probabilities({}, declared)
    from_generator = posterior_landing_probabilities({}, (value for value in declared))
    assert from_generator == from_list
    assert sum(from_generator.values()) == pytest.approx(3 / 4)


def test_select_next_edge_unreachable_source_returns_none_without_keyerror():
    runner = create_behavior_tree_executor()
    tree = [
        {
            "type": "image",
            "id": 34,
            "title": "世界",
            "shapes": [{"title": "日常", "sceneJumpTarget": "69(451)"}],
        },
        {
            "type": "image",
            "id": 69,
            "title": "日常",
            "shapes": [{"title": "退出", "sceneJumpTarget": "34(52)"}],
        },
        {"type": "image", "id": 20, "title": "绿瓶", "shapes": []},
    ]

    # #20 has no inbound edge, so #69 cannot reach it.  The live selector must
    # return None instead of raising while reading the missing posterior.
    assert runner._select_scene_next_edge(tree, 69, 20) is None


def test_return_history_does_not_turn_a_round_trip_into_a_route():
    runner = create_behavior_tree_executor()
    tree = [
        {
            "type": "image", "id": 34, "title": "世界",
            "shapes": [{"id": "daily", "title": "日常", "sceneJumpTarget": "69(100)"}],
        },
        {
            "type": "image", "id": 69, "title": "日常",
            "shapes": [{"id": "exit", "title": "退出", "sceneJumpTarget": "34(100),340(1)"}],
        },
        {
            "type": "image", "id": 340, "title": "通用返回页",
            "shapes": [{"id": "return", "title": "返回", "sceneJumpTarget": "34(100),279(1)"}],
        },
        {"type": "image", "id": 279, "title": "目标", "shapes": []},
    ]

    assert runner.plan_scene_navigation(tree, 34, 279)["status"] == "no_path"
    assert runner._select_scene_next_edge(tree, 69, 279) is None


def test_data_declared_scroll_list_edge_joins_the_generic_scene_graph():
    runner = create_behavior_tree_executor()
    for source, list_scene, target in ((34, 69, 279), (10, 11, 12)):
        tree = [
            {"type": "image", "id": source, "title": "source", "shapes": [
                {"id": "open", "title": "打开列表", "sceneJumpTarget": str(list_scene)},
            ]},
            {"type": "image", "id": list_scene, "title": "list", "shapes": [
                {"id": "row", "title": "动态任务入口", "sceneJumpTarget": str(target),
                 "navigationAction": "scroll_list_entry",
                 "navigationListShape": "滚动窗口",
                 "navigationTitlePattern": "任务标题"},
            ]},
            {"type": "image", "id": target, "title": "target", "shapes": []},
        ]
        plan = runner.plan_scene_navigation(tree, source, target)
        assert plan["status"] == "ok"
        final_step = plan["candidates"][0]["downstream"]["steps"][-1]
        assert (final_step["from_scene_id"], final_step["to_scene_id"]) == (list_scene, target)
        assert final_step["action_title"] == "动态任务入口"


def test_navigation_does_not_use_recast_candidate_decision_as_exit():
    runner = create_behavior_tree_executor()
    tree = [
        {"type": "image", "id": 819, "title": "重铸候选", "shapes": [
            {"title": "保留新属性", "sceneJumpTarget": "818(10)"},
        ]},
        {"type": "image", "id": 818, "title": "重铸", "shapes": [
            {"title": "返回", "sceneJumpTarget": "34(10)"},
        ]},
        {"type": "image", "id": 34, "title": "世界", "shapes": []},
    ]

    assert runner.plan_scene_navigation(tree, 819, 34)["status"] == "no_path"
    assert runner._select_scene_next_edge(tree, 819, 34) is None


def test_data_declared_safe_tab_can_exit_without_permitting_unmarked_equipment_actions():
    runner = create_behavior_tree_executor()
    tree = [
        {"type": "image", "id": 717, "title": "升阶", "shapes": [
            {"id": "tab", "title": "装配", "navigationRole": "safe_tab", "sceneJumpTarget": "667(10)"},
        ]},
        {"type": "image", "id": 667, "title": "灵器", "shapes": [
            {"id": "back", "title": "返回", "sceneJumpTarget": "34(10)"},
        ]},
        {"type": "image", "id": 34, "title": "世界", "shapes": []},
    ]
    assert runner.plan_scene_navigation(tree, 717, 34)["status"] == "ok"
    tree[0]["shapes"][0].pop("navigationRole")
    assert create_behavior_tree_executor().plan_scene_navigation(tree, 717, 34)["status"] == "no_path"


@pytest.mark.parametrize("confidence_z", [0.0, 1.0, 2.0])
def test_shengzu_unobserved_declared_destination_remains_reachable(confidence_z):
    observed = {338: 4, 661: 2, 385: 0}
    probabilities = posterior_landing_probabilities(
        observed, [338, 661, 385], confidence_z=confidence_z,
    )
    assert probabilities[385] == pytest.approx(1 / 8)
    assert observed == {338: 4, 661: 2, 385: 0}
    assert sum(probabilities.values()) <= 1.0
