from __future__ import annotations

import threading
from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks import wanxiang_baoge


def _consume(operation):
    try:
        while True:
            next(operation)
    except StopIteration as stop:
        return stop.value


class _OcrRuntime:
    def __init__(self, texts):
        self.texts = texts

    def ocr_tokens_in_shapes(self, _scene_id, shape_names, *, frame_data_url):
        del frame_data_url
        return [{"text": self.texts.get(shape_names[0], ""), "x": 0, "y": 0}]


def test_target_slot_uses_discount_original_price_and_exact_price_together():
    runtime = _OcrRuntime(
        {
            "商品1": "5折原价536元268元",
            "商品2": "7折原价536元388元",
            "商品3": "6折原价2146元1288元",
            "商品4": "5折原价1296元648元",
            "商品5": "0.5折原价120元6元",
        }
    )

    assert wanxiang_baoge._target_slot(runtime, "frame", [1, 2, 3, 4, 656001]) == 5


def test_target_slot_fails_closed_when_two_targets_are_rendered():
    runtime = _OcrRuntime(
        {
            "商品1": "0.5折原价120元6元",
            "商品2": "0.5折原价120元6元",
        }
    )

    with pytest.raises(RuntimeError, match="多个"):
        wanxiang_baoge._target_slot(runtime, "frame", [1, 2, 3, 4, 5])


def test_completed_purchase_is_idempotent_and_only_returns_to_world(monkeypatch):
    events = []

    class Runtime:
        def goto_view(self, scene_id):
            events.append(("goto", scene_id))
            if False:
                yield None

    class Runner:
        def _fanxiu_runtime(self, *_args, **_kwargs):
            return Runtime()

    monkeypatch.setattr(
        wanxiang_baoge,
        "load_wanxiang_refund_offer_contract",
        lambda: {"complete": True, "price_cny_fen": 600},
    )
    monkeypatch.setattr(
        wanxiang_baoge,
        "read_wanxiang_baoge_runtime",
        lambda: {"complete": True, "buy_times": 1, "refund_box_count": 0},
    )

    result = _consume(
        wanxiang_baoge.execute_wanxiang_baoge_task(
            Runner(), {}, {}, threading.Event()
        )
    )

    assert result == {"ok": True, "outcome": "already_completed", "cash_paid_fen": 0}
    assert events == [("goto", 34)]


def test_wanxiang_is_one_dormant_manual_standard_job():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "wanxiang_baoge_six_yuan"
    )
    assert definition is not None
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    assert definition.standard_job_id == "wanxiang-baoge-six-yuan"
    assert definition.standard_job_description == "手动"
    assert definition.standard_job_payload == {"max_refreshes": 100}

    tasks = default_data_annotation_scheduler_tasks(datetime(2026, 8, 22, 4, 0))
    matches = [task for task in tasks if task["id"] == "wanxiang-baoge-six-yuan"]
    assert len(matches) == 1
    assert matches[0]["task_type"] == "wanxiang_baoge_six_yuan"
    assert matches[0]["trigger_description"] == "手动"
    assert matches[0]["next_time"] is None
    assert matches[0]["error_retry_delay_seconds"] == 0

