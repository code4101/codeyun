from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation import (
    XianzangPageResult,
)
from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_tasks import (
    complete_xianzang_tasks,
    parse_xianzang_task_progress,
)


def _tokens(text: str) -> list[dict]:
    return [
        {
            "text": character,
            "x": 100 + index * 20,
            "y": 300,
            "w": 18,
            "h": 28,
            "parent_line_id": "progress",
            "line_order": 1,
            "order": index,
        }
        for index, character in enumerate(text)
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1/1", (1, 1)), ("10丨10", (10, 10)), ("5 10", (5, 10))],
)
def test_progress_parser_ignores_separator(text, expected):
    result = parse_xianzang_task_progress(_tokens(text))
    assert result is not None
    assert (result.numerator, result.denominator) == expected


@pytest.mark.parametrize("text", ["已完成", "1", "第5个 1/1", "2/1", "0/0"])
def test_progress_parser_rejects_unsafe_evidence(text):
    assert parse_xianzang_task_progress(_tokens(text)) is None


@dataclass
class _FakeRuntime:
    def __post_init__(self):
        self.clicks: list[tuple[int, str, str]] = []
        self.frames = 0

    def current_scene(self, scene_ids, *, update):
        assert scene_ids == [450]
        assert update is True
        self.frames += 1
        return 450, 100.0, f"frame-{self.frames}"

    def click_shape(self, scene_id, title, *, frame_data_url):
        self.clicks.append((scene_id, title, frame_data_url))


def _page(_runtime, tab, **_kwargs):
    if tab == "任务":
        return XianzangPageResult("任务", 450, 100.0, "炼宝试炼")
    return XianzangPageResult("蓬莱仙藏", 447, 100.0, "蓬莱仙藏")


def _task_snapshot(*states):
    tasks = [
        {"task_id": 10208 + index, "state": state}
        for index, state in enumerate(states)
    ]
    return {
        "complete": True,
        "tasks": tasks,
        "claimable": [item for item in tasks if item["state"] == "claimable"],
    }


def test_claimable_tasks_are_clicked_and_dynamically_confirmed(monkeypatch):
    runtime = _FakeRuntime()
    snapshots = iter(
        [
            _task_snapshot("claimable", "claimable"),
            _task_snapshot("claimed", "claimable"),
            _task_snapshot("claimed", "claimed"),
        ]
    )
    sleeps = []
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_tasks.time.sleep",
        sleeps.append,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_tasks.open_xianzang_tab",
        _page,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_tasks.read_bothdraw_task_runtime",
        lambda: next(snapshots),
    )

    result = complete_xianzang_tasks(runtime)

    assert result.clicked_count == 2
    assert result.stop_reason == "all_claimed"
    assert runtime.clicks == [
        (450, "进度", "frame-1"),
        (450, "进度", "frame-2"),
    ]
    assert result.final_page.page == "蓬莱仙藏"


def test_no_claimable_task_never_clicks(monkeypatch):
    runtime = _FakeRuntime()
    opened = []
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_tasks.open_xianzang_tab",
        lambda runtime, tab, **kwargs: opened.append(tab) or _page(runtime, tab, **kwargs),
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_tasks.read_bothdraw_task_runtime",
        lambda: _task_snapshot("claimed", "receiving"),
    )

    result = complete_xianzang_tasks(runtime)

    assert result.clicked_count == 0
    assert result.stop_reason == "all_claimed"
    assert runtime.clicks == []
    assert opened == ["蓬莱仙藏"]


def test_missing_runtime_state_is_failure_not_all_claimed(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_tasks.open_xianzang_tab",
        _page,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_tasks.read_bothdraw_task_runtime",
        lambda: {"complete": False, "reason": "runtime unavailable"},
    )

    with pytest.raises(RuntimeError, match="runtime unavailable"):
        complete_xianzang_tasks(runtime)
    assert runtime.clicks == []
