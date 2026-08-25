from backend.core.fanxiu.data_annotation.scene_navigation import (
    explicit_scene_jump_edges,
    posterior_landing_probabilities,
    posterior_reachable_probability,
)


def test_unobserved_single_landing_keeps_unknown_outcome_prior():
    assert posterior_reachable_probability({}, [34], [34]) == 0.5


def test_repeated_reachable_landings_raise_posterior_probability():
    probability = posterior_reachable_probability({34: 8}, [34], [34])

    assert probability == 0.9


def test_self_and_dead_end_landings_lower_reachable_probability():
    probability = posterior_reachable_probability(
        {1: 7, 34: 2, 99: 1},
        [1, 34, 99],
        [34],
    )

    assert probability == 3 / 14


def test_posterior_landing_probabilities_keep_dirty_target_observation_small():
    probabilities = posterior_landing_probabilities(
        {69: 2247, 34: 5, 20: 1},
        [69, 34, 20],
    )

    assert probabilities[20] == 2 / 2257
    assert sum(probabilities.values()) == 2256 / 2257


def test_navigation_edges_ignore_empty_return_to_physical_parent():
    child = {
        "type": "image",
        "title": "子场景",
        "filename": "0002.png",
        "layer": 2,
        "shapes": [{"id": "return", "title": "返回", "sceneJumpTarget": ""}],
    }
    tree = [{
        "type": "image",
        "title": "父场景",
        "filename": "0001.png",
        "layer": 1,
        "shapes": [],
        "children": [child],
    }]

    assert explicit_scene_jump_edges(tree) == {}


def test_navigation_edges_include_explicit_layer3_targets():
    tree = [
        {
            "type": "image",
            "title": "无标识帧",
            "filename": "0003.png",
            "layer": 3,
            "shapes": [{"id": "close", "title": "关闭", "sceneJumpTarget": "1"}],
        },
        {
            "type": "image",
            "title": "世界",
            "filename": "0001.png",
            "layer": 1,
            "shapes": [],
        },
    ]

    edges = explicit_scene_jump_edges(tree)

    assert [edge["target_ids"] for edge in edges[3]] == [[1]]
