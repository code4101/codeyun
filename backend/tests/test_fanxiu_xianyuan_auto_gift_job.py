from __future__ import annotations

import inspect
import threading
from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation import default_jobs
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks import resource_auto_use
from backend.core.fanxiu.data_annotation.tasks.xianyuan_auto_gift import (
    build_xianyuan_auto_gift_plan,
    execute_xianyuan_auto_gift_task,
    routed_npc_gift_items,
)
from backend.core.fanxiu.storage_bag_usage import storage_bag_analysis_fingerprint


def _consume(operation):
    try:
        while True:
            next(operation)
    except StopIteration as exc:
        return exc.value


def _storage(*rows):
    return {"complete": True, "items": list(rows)}


def _row(base_id: int, *, template: str = "npc_gift", enabled: bool = True):
    row = {
        "base_id": base_id,
        "num": 3,
        "auto_claim": enabled,
        "analysis_status": "classified",
        "operation_template": template,
        "yield_mode": "none",
        "note": "",
    }
    row["analysis_fingerprint"] = storage_bag_analysis_fingerprint(row)
    return row


def _xianyuan(item_id: int):
    return {
        "runtime_complete": True,
        "people": [
            {
                "npc_id": 3002,
                "runtime_index": 7,
                "name": "轮回殿主",
                "giftable": True,
                "hostile": False,
                "favor": 120,
                "favor_level": 2,
                "gift_options": [
                    {
                        "item_id": item_id,
                        "favorability": 5000,
                        "career_conditional": False,
                        "activity_gift": False,
                    }
                ],
            }
        ],
    }


def test_plan_consumes_only_npc_gift_route_and_keeps_native_object_identity() -> None:
    routes = routed_npc_gift_items(_storage(
        _row(7020038),
        _row(390035007, template="random_box"),
        _row(7020006, enabled=False),
    ))
    plan = build_xianyuan_auto_gift_plan(routes, _xianyuan(7020038))

    assert [route.base_id for route in routes] == [7020038]
    assert plan["source_route"] == "xianyuan_auto_gift"
    assert plan["items"][0]["quantity"] == 3
    assert plan["items"][0]["eligible_targets"] == [{
        "npc_id": 3002,
        "runtime_index": 7,
        "name": "轮回殿主",
        "favor": 120,
        "favor_level": 2,
        "gift_item_id": 7020038,
        "favorability_per_item": 5000,
        "career_conditional": False,
        "activity_gift": False,
    }]


def test_missing_runtime_object_mapping_fails_closed() -> None:
    routes = routed_npc_gift_items(_storage(_row(7020038)))
    with pytest.raises(RuntimeError, match="未映射到可送礼 Runtime 对象"):
        build_xianyuan_auto_gift_plan(routes, _xianyuan(7020006))


def test_candidate_plan_without_formal_gui_adapter_never_touches_runner() -> None:
    class NoGameRunner:
        def __getattr__(self, name):
            raise AssertionError(f"不得访问游戏 runner: {name}")

    operation = execute_xianyuan_auto_gift_task(
        NoGameRunner(),
        {},
        {},
        threading.Event(),
        storage_reader=lambda: _storage(_row(7020038)),
        xianyuan_reader=lambda: _xianyuan(7020038),
        gui_adapter=None,
    )
    with pytest.raises(RuntimeError, match="正式送礼 GUI adapter 尚未就绪"):
        _consume(operation)


def test_no_owned_route_is_a_zero_action_completion() -> None:
    result = _consume(execute_xianyuan_auto_gift_task(
        object(),
        {},
        {},
        threading.Event(),
        storage_reader=lambda: _storage(_row(390035007, template="random_box")),
        xianyuan_reader=lambda: (_ for _ in ()).throw(AssertionError("不应读取仙缘 Runtime")),
    ))
    assert result["ok"] is True
    assert result["plan"]["item_count"] == 0


def test_xianyuan_auto_gift_is_one_manual_standard_job() -> None:
    default_jobs.register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("xianyuan_auto_gift")
    assert definition is not None
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    assert definition.standard_job_id == "xianyuan-auto-gift"

    matches = [
        task
        for task in default_data_annotation_scheduler_tasks(datetime(2026, 8, 17, 2, 0))
        if task["id"] == "xianyuan-auto-gift"
    ]
    assert len(matches) == 1
    assert matches[0]["task_type"] == "xianyuan_auto_gift"
    assert matches[0]["trigger_description"] == "手动"
    assert matches[0]["next_time"] is None
    assert matches[0]["error_retry_delay_seconds"] == 0


def test_resource_auto_use_does_not_inline_xianyuan_gift_lifecycle() -> None:
    source = inspect.getsource(resource_auto_use.execute_resource_auto_use_task)
    assert "xianyuan_auto_gift" not in source
    assert "source_route" not in source
