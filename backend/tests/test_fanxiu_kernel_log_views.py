from copy import deepcopy

from backend.core.fanxiu.data_annotation.kernel_log_views import (
    cell_display_source,
    historical_cell_views,
    log_entries,
    persisted_cell_views,
)


def test_log_entries_preserve_repeated_events_and_source_evidence():
    item = {
        "time": "05:00:01", "message": "等待场景", "scope": "job",
        "action": "wait_scene", "source_file": "task.py",
        "source_path": "tasks/task.py", "source_line": 12,
        "source_expr": "wait_scene(34)", "ts": "123",
    }
    entries = log_entries([item, dict(item)])
    assert entries[0].id != entries[1].id
    assert entries == log_entries([item, dict(item)])
    assert entries[1].source_path == "tasks/task.py"
    assert entries[1].source_line == 12
    assert entries[1].source_expr == "wait_scene(34)"


def test_persisted_cells_filter_invalid_and_duplicate_rows_without_mutation():
    row = {"id": "saved", "title": "代码", "source": '{"code": " print(1) "}', "entries": []}
    status = {"cell_logs": [None, {"entries": "invalid"}, row, dict(row)]}
    before = deepcopy(status)
    cells = persisted_cell_views(status, 20)
    assert len(cells) == 1
    assert cells[0].source == "print(1)"
    assert status == before


def test_history_grouping_keeps_boundaries_and_existing_cells():
    items = [
        {"time": "00:00:01", "message": "任务启动：甲", "scope": "job"},
        {"time": "00:00:02", "message": "甲完成", "scope": "job"},
        {"time": "00:00:03", "message": "task cell 已启动", "scope": "guard"},
        {"time": "00:00:04", "message": "乙完成", "scope": "guard"},
    ]
    groups = historical_cell_views(items, [], 20)
    assert [len(cell.entries) for cell in groups] == [2, 2]
    assert groups[0].title == "任务启动：甲"
    assert groups[1].title == "守护 cell"
    assert groups[0].started_at == "00:00:01" and groups[0].ended_at == "00:00:02"
    assert "当时没有保存提交源码" in groups[0].source
    assert historical_cell_views(items, groups[:1], 2) == groups
    assert len(historical_cell_views(items, [], 1)) == 1


def test_source_display_preserves_code_and_unparseable_legacy_text():
    assert cell_display_source("print('原文')") == "print('原文')"
    assert cell_display_source("{broken") == "{broken"
    assert cell_display_source('{"cmd":"run", "value":null}') == "cell_meta = {'cmd': 'run', 'value': None}"
