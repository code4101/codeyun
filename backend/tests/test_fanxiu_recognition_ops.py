from backend.core.fanxiu.data_annotation.recognition_ops import build_recognition_ops_report


def test_recognition_ops_report_classifies_match_graph_issues():
    matrix = {
        "scene_ids": [1, 2, 3, 4, 5, 6],
        "matches": [
            {"s": 1, "x": 2, "score": 90, "threshold": 80, "matched": True},
            {"s": 2, "x": 1, "score": 91, "threshold": 80, "matched": True},
            {"s": 3, "x": 2, "score": 88, "threshold": 80, "matched": True},
            {"s": 3, "x": 4, "score": 88, "threshold": 80, "matched": True},
            {"s": 4, "x": 5, "score": 88, "threshold": 80, "matched": True},
            {"s": 5, "x": 3, "score": 88, "threshold": 80, "matched": True},
            {"s": 6, "x": 6, "score": 99, "threshold": 80, "matched": True},
        ],
        "cache_hit": True,
    }
    images = {
        1: {"title": "a", "filename": "0001.png"},
        2: {"title": "b", "filename": "0002.png"},
        3: {"title": "c", "filename": "0003.png"},
        4: {"title": "d", "filename": "0004.png"},
        5: {"title": "e", "filename": "0005.png"},
        6: {"title": "f", "filename": "0006.png"},
    }

    report = build_recognition_ops_report(matrix, images)

    issues_by_category = {}
    for issue in report["issues"]:
        issues_by_category.setdefault(issue["category"], []).append(issue)

    assert report["matrix"]["ignored_self_loop_count"] == 1
    assert {tuple(issue["node_ids"]) for issue in issues_by_category["mutual_match"]} == {(1, 2)}
    assert any(issue["node_ids"][0] == 2 and set(issue["node_ids"][1:]) == {1, 3} for issue in issues_by_category["multi_parent"])
    assert any(set(issue["node_ids"]) == {3, 4, 5} for issue in issues_by_category["cycle_group"])
    assert "isolated" not in issues_by_category
    assert all(category["id"] != "isolated" for category in report["categories"])


def test_recognition_ops_report_does_not_treat_unmatched_nodes_as_issues():
    report = build_recognition_ops_report(
        {
            "scene_ids": [1, 2],
            "matches": [],
            "cache_missing": True,
        },
        {
            1: {"title": "a", "filename": "0001.png"},
            2: {"title": "b", "filename": "0002.png"},
        },
    )

    assert report["matrix"]["cache_missing"] is True
    assert report["issues"] == []
