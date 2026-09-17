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
