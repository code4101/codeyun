from __future__ import annotations

import threading
from pathlib import Path

import pytest

from backend.core.fanxiu.instrumentation import xianfu
from backend.core.fanxiu.instrumentation.runtime_memory import (
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
)
from backend.core.fanxiu.behavior_tree.runtime import (
    create_behavior_tree_runtime_runner,
)
from backend.core.fanxiu.data_annotation.behavior_tree_runtime import View


def _drain_generator(result):
    try:
        while True:
            next(result)
    except StopIteration as stopped:
        return stopped.value


def test_xianfu_skill_snapshot_reads_revenue_free_time(monkeypatch):
    activity_dic = LuaRef("table", 0x1000)
    activity = LuaRef("table", 0x1100)
    play = LuaRef("table", 0x1200)
    base = LuaRef("table", 0x1300)
    next_free = LuaRef("table", 0x1400)
    monkeypatch.setattr(
        xianfu,
        "_revenue_data_fields",
        lambda _reader, _root: {"V_ActivityDic": activity_dic},
    )
    monkeypatch.setattr(
        LuaJitReader,
        "dictionary_fields",
        lambda _reader, value: (
            {670002.0: activity}
            if value == activity_dic
            else {}
        ),
    )

    def fake_fields(_reader, value):
        if value == activity:
            return {
                "activityId": 670002.0,
                "revenuePlayVO": play,
                "revenueBaseVO": base,
            }
        if value == play:
            return {
                "free": False,
                "nextFreeTime": next_free,
            }
        if value == base:
            return {"freeCD": 1440.0}
        return {}

    monkeypatch.setattr(LuaJitReader, "fields", fake_fields)
    monkeypatch.setattr(
        LuaJitReader,
        "long",
        lambda _reader, value: (
            1_700_000_010_000
            if value == next_free
            else None
        ),
    )
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )

    result = xianfu._snapshot(
        memory,
        0x2000,
        root_cache_hit=False,
        now_epoch_ms=1_700_000_000_000,
    )

    assert result["ok"] is True
    assert result["activity_id"] == 670002
    assert result["free_available"] is False
    assert result["free_cd_minutes"] == 1440
    assert result["remaining_seconds"] == 10


def test_xianfu_skill_task_skips_gui_until_runtime_free_time(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()
    scheduled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        runner,
        "_fanxiu_runtime",
        lambda *_args, **_kwargs: (
            pytest.fail("未到免费时间时不应进入 GUI")
        ),
    )
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: scheduled.append(
            (task_id, next_time)
        ),
    )
    payload = {
        "__xianfu_skill_runtime_snapshot_override": {
            "complete": True,
            "free_available": False,
            "next_free_at": "2026-07-29 14:52:50",
        }
    }
    ctx = {
        "asset_tree_path": Path("asset-tree.json"),
        "images": {176: {"shapes": []}},
    }

    result = _drain_generator(
        runner._execute_xianfu_learn_skill_task(
            ctx,
            threading.Event(),
            payload,
        )
    )

    assert result == "skipped"
    assert scheduled == [
        ("xianfu-learn-skill", "2026-07-29 14:52:50")
    ]


def test_xianfu_skill_runtime_free_state_bypasses_status_ocr(
    monkeypatch,
):
    runner = create_behavior_tree_runtime_runner()
    actions: list[str] = []
    result_popup_calls: list[dict[str, object]] = []
    scheduled: list[tuple[str, str]] = []
    snapshots = iter(
        [
            {
                "complete": True,
                "free_available": True,
            },
            {
                "complete": True,
                "free_available": False,
                "next_free_at": "2026-07-31 14:54:57",
            },
        ]
    )
    image176 = {
        "id": 176,
        "title": "绝技",
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "title": "领悟一次",
                "x": 0.5,
                "y": 0.8,
                "w": 0.2,
                "h": 0.1,
            },
        ],
    }

    class Runtime:
        def current_scene(self, *_args, **_kwargs):
            return 176, 100.0, "frame"

        def ocr_text(self, *_args, **_kwargs):
            pytest.fail("Runtime 已确认免费且场景为 #176 时不应读取页面 OCR")

        def get_view(self, scene_id):
            return View(image176) if scene_id == 176 else None

        def click_shape_center(self, _view, _shape):
            actions.append("draw")

        def cur_frame(self, **_kwargs):
            return "frame-after-tab"

    def finished_step(label: str):
        actions.append(label)
        if False:
            yield None
        return "success"

    monkeypatch.setattr(
        runner,
        "_fanxiu_runtime",
        lambda *_args, **_kwargs: Runtime(),
    )
    monkeypatch.setattr(
        runner,
        "_xianfu_skill_runtime_snapshot",
        lambda _payload: next(snapshots),
    )
    monkeypatch.setattr(
        runner,
        "_fanxiu_runtime_ocr_text_in_shapes",
        lambda *_args, **_kwargs: pytest.fail(
            "Runtime 已确认免费时不应读取状态区 OCR"
        ),
    )
    monkeypatch.setattr(
        runner,
        "_switch_xianfu_learn_skill_xianpin_tab",
        lambda _runtime: finished_step("switch"),
    )

    def handle_result(_runtime, **kwargs):
        result_popup_calls.append(kwargs)
        return finished_step("result")

    monkeypatch.setattr(
        runner,
        "_handle_xianfu_learn_skill_result_popup",
        handle_result,
    )
    monkeypatch.setattr(
        runner,
        "_return_xianfu_learn_skill_to_world",
        lambda _runtime: finished_step("return"),
    )
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: scheduled.append(
            (task_id, next_time)
        ),
    )

    result = _drain_generator(
        runner._execute_xianfu_learn_skill_task(
            {
                "asset_tree_path": Path("asset-tree.json"),
                "images": {
                    176: image176,
                    177: {"id": 177, "shapes": []},
                },
            },
            threading.Event(),
            {"refresh_scene_177_reference_once": True},
        )
    )

    assert result == "success"
    assert actions == ["switch", "draw", "result", "return"]
    assert scheduled == [
        ("xianfu-learn-skill", "2026-07-31 14:54:57")
    ]
    assert result_popup_calls == [
        {
            "refresh_reference_frame_once": True,
            "scheduler_task_id": "xianfu-learn-skill",
        }
    ]
