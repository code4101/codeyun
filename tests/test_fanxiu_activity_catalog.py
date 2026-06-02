from backend.core import fanxiu_activity_catalog as catalog


def test_filter_rows_for_activity_runtime_keeps_single_open_after_server_day_version():
    runtime_rows = [
        {
            "activityId": 1043011,
            "startTime": 1780261205000,
            "class": "RankActivityVO",
        }
    ]
    schedule = {"openServerTime": 1745098200000}
    rows = [
        {
            "row_key": "before-1",
            "rank_range": "1",
            "condition": "ActivityIdOpenBefore|1043011_20250411",
            "server_day_start": 1,
            "server_day_end": 30,
        },
        {
            "row_key": "after-young-1",
            "rank_range": "1",
            "condition": "ActivityIdOpenAfter|1043011_20250411",
            "server_day_start": 1,
            "server_day_end": 30,
        },
        {
            "row_key": "after-current-1",
            "rank_range": "1",
            "condition": "ActivityIdOpenAfter|1043011_20250411",
            "server_day_start": 31,
            "server_day_end": 9999,
        },
        {
            "row_key": "after-current-2",
            "rank_range": "2",
            "condition": "ActivityIdOpenAfter|1043011_20250411",
            "server_day_start": 31,
            "server_day_end": 9999,
        },
    ]

    filtered = catalog._filter_rows_for_activity_runtime(rows, runtime_rows, schedule)

    assert [row["row_key"] for row in filtered] == ["after-current-1", "after-current-2"]
    assert sum(1 for row in filtered if row["rank_range"] == "1") == 1
