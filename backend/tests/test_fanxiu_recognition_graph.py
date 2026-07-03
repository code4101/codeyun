from backend.core.fanxiu.data_annotation.recognition_graph import (
    SceneGraphCandidate,
    choose_scene_from_graph,
    graph_specific_scene_ids,
    normalize_match_edge,
)


def test_normalize_match_edge_uses_match_s_x_direction():
    assert normalize_match_edge({"s": 34, "x": 266}) == (34, 266)
    assert normalize_match_edge({"reference": "34", "frame": "#266"}) == (34, 266)
    assert normalize_match_edge({"y": 34, "x": 266}) == (34, 266)


def test_graph_specific_scene_keeps_deeper_match_candidate():
    assert graph_specific_scene_ids([34, 266], [(34, 266)]) == (266,)
    assert graph_specific_scene_ids([34, 265, 266], [(34, 265), (265, 266)]) == (266,)


def test_graph_specific_scene_preserves_bidirectional_ambiguity():
    assert graph_specific_scene_ids([47, 28], [(47, 28), (28, 47)]) == ()


def test_choose_scene_from_graph_uses_graph_before_similarity():
    result = choose_scene_from_graph(
        [
            SceneGraphCandidate(scene_id=34, score=99.0, matched=True),
            SceneGraphCandidate(scene_id=266, score=88.0, matched=True),
        ],
        [(34, 266)],
    )

    assert result.scene_id == 266
    assert result.status == "graph_specific"


def test_choose_scene_from_graph_falls_back_to_similarity_for_tie():
    result = choose_scene_from_graph(
        [
            SceneGraphCandidate(scene_id=47, score=87.0, matched=True),
            SceneGraphCandidate(scene_id=28, score=92.0, matched=True),
        ],
        [(47, 28), (28, 47)],
    )

    assert result.scene_id == 28
    assert result.status == "similarity_tiebreak"
    assert result.unresolved_candidates == (47, 28)


def test_choose_scene_from_graph_returns_unknown_below_similarity_floor():
    result = choose_scene_from_graph(
        [
            SceneGraphCandidate(scene_id=34, score=4.9, matched=False),
            SceneGraphCandidate(scene_id=266, score=3.0, matched=False),
        ],
        [],
        unknown_similarity_threshold=5.0,
    )

    assert result.scene_id is None
    assert result.status == "unknown"
    assert result.best_similarity_scene_id == 34
