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
    assert any(issue["node_ids"][0] == 2 and set(issue["node_ids"][1:]) == {1, 3} for issue in issues_by_category["multi_source"])
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


def test_recognition_ops_fallback_nodes_ignore_assets_without_scene_identity():
    report = build_recognition_ops_report(
        {"scene_ids": [], "matches": [], "cache_missing": True},
        {
            900: {
                "title": "场景",
                "filename": "0900.png",
                "shapes": [{"id": "identity", "sceneIdentityRole": "required"}],
            },
            901: {
                "title": "动作素材",
                "filename": "0901.png",
                "layer": 2,
                "shapes": [{"id": "return", "title": "返回", "sceneIdentityRole": "off"}],
            },
        },
    )

    assert report["matrix"]["node_count"] == 1
    assert report["summary"]["node_count"] == 1


def test_recognition_ops_includes_persisted_navigation_stalls_when_matrix_is_missing():
    report = build_recognition_ops_report(
        {"scene_ids": [], "matches": [], "cache_missing": True},
        {
            34: {"title": "世界", "filename": "0034.png"},
            54: {"title": "退出道场", "filename": "0054.png"},
        },
        navigation_incidents=[
            {
                "id": "nav-1",
                "status": "recovered_with_fallback",
                "review_status": "pending",
                "created_at": "2026-07-28T12:00:00",
                "target_scene_id": 34,
                "current_scene_id": 54,
                "fallback_used": True,
                "trigger": {"type": "stable_self_loop", "label": "稳定自环"},
                "timeline": [
                    {
                        "source_scene_id": 54,
                        "landing_scene_id": 54,
                        "landing_score": 86,
                    },
                    {
                        "source_scene_id": 54,
                        "landing_scene_id": 34,
                        "landing_score": 100,
                    },
                ],
                "resolution": {"scene_id": 34, "score": 100},
            }
        ],
    )

    issue = next(item for item in report["issues"] if item["category"] == "navigation_stall")
    assert issue["incident"]["id"] == "nav-1"
    assert issue["incident"]["review_status"] == "pending"
    assert issue["node_ids"] == [54, 34]
    assert {(edge["source_id"], edge["target_id"]) for edge in issue["edges"]} == {(54, 54), (54, 34)}
    assert next(category for category in report["categories"] if category["id"] == "navigation_stall")["count"] == 1


def test_recognition_ops_includes_runtime_identity_ambiguity_aggregate():
    report = build_recognition_ops_report(
        {"scene_ids": [], "matches": [], "cache_missing": True},
        {
            3: {"title": "甲", "filename": "0003.png"},
            9: {"title": "乙", "filename": "0009.png"},
        },
        recognition_ambiguities=[
            {
                "id": "ambiguity:abc",
                "signature": "abc",
                "layer": 2,
                "tied_scene_ids": [3, 9],
                "occurrence_count": 7,
                "distinct_frame_count": 2,
                "selected_scene_counts": {"9": 6, "unresolved": 1},
                "first_seen_at": "2026-08-26T10:00:00+08:00",
                "last_seen_at": "2026-08-26T11:00:00+08:00",
            }
        ],
    )

    issue = next(item for item in report["issues"] if item["category"] == "identity_ambiguity")
    assert issue["node_ids"] == [3, 9]
    assert issue["severity"] == "error"
    assert issue["ambiguity"]["occurrence_count"] == 7
    assert next(category for category in report["categories"] if category["id"] == "identity_ambiguity")["count"] == 1
