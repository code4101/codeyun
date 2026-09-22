from __future__ import annotations

import threading
from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation import default_jobs
from backend.core.fanxiu.data_annotation import kernel_scheduler_control
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.kernel_scheduler_defaults import (
    consolidate_arena_scheduler_instances,
    default_kernel_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks import resource_auto_use


def _consume(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def _empty_talisman():
    return {
        "complete": True,
        "state": "Loaded",
        "source": "TalismanModel.GetAllUpgradeableTalismanList",
        "candidates": [],
    }


def _empty_pet():
    return {
        "complete": True,
        "state": "Loaded",
        "source": "PetData.CheckPetCardUpCount",
        "candidates": [],
    }


def _storage_success(order):
    def execute(*_args, **_kwargs):
        order.append("储物袋")
        yield None
        return {"ok": True, "outcome": "complete"}

    return execute


def test_aggregate_keeps_talisman_zero_ui_and_visits_pet_for_prayer(monkeypatch):
    order = []
    monkeypatch.setattr(
        resource_auto_use,
        "execute_storage_bag_quick_operation_task",
        _storage_success(order),
    )

    def talisman_reader():
        order.append("法宝")
        return _empty_talisman()

    def pet_reader():
        order.append("灵兽")
        return _empty_pet()

    def pet_adapter(*_args):
        order.append("灵兽动作")
        yield None
        return {"ok": True, "verified": True}

    result = _consume(resource_auto_use.execute_resource_auto_use_task(
        object(),
        {},
        {},
        threading.Event(),
        talisman_reader=talisman_reader,
        pet_reader=pet_reader,
        pet_adapter=pet_adapter,
    ))

    assert result["ok"] is True
    # 法宝：原生快照证明无可升级法宝，零界面完成；灵兽：即使吞噬候选为空也必须进页面
    # 消费祈灵材料（祈灵没有等价 Runtime 投影），动作后仍需复验完整终态。
    assert order == ["储物袋", "法宝", "灵兽", "灵兽动作", "灵兽"]
    assert [item["outcome"] for item in result["domains"][1:]] == [
        "complete",
        "complete",
    ]


def test_incomplete_domain_snapshot_fails_closed_before_later_domain(monkeypatch):
    order = []
    monkeypatch.setattr(
        resource_auto_use,
        "execute_storage_bag_quick_operation_task",
        _storage_success(order),
    )

    with pytest.raises(RuntimeError, match="法宝.*快照不完整"):
        _consume(resource_auto_use.execute_resource_auto_use_task(
            object(),
            {},
            {},
            threading.Event(),
            talisman_reader=lambda: {
                "complete": False,
                "source": "TalismanModel.GetAllUpgradeableTalismanList",
                "candidates": [],
            },
            pet_reader=lambda: order.append("不应读取") or _empty_pet(),
        ))

    assert order == ["储物袋"]


def test_executable_domain_without_formal_adapter_fails_closed(monkeypatch):
    order = []
    monkeypatch.setattr(
        resource_auto_use,
        "execute_storage_bag_quick_operation_task",
        _storage_success(order),
    )
    snapshot = {
        "complete": True,
        "source": "TalismanModel.GetAllUpgradeableTalismanList",
        "candidates": [{
            "talisman_id": 501,
            "category": "法宝",
            "owned": True,
            "active": True,
            "upgrade_count": 1,
            "resources": [{
                "kind": "talisman_upgrade_material",
                "item_id": 7001,
                "quantity": 1,
            }],
        }],
    }

    with pytest.raises(RuntimeError, match="正式资产/动作适配器尚未就绪"):
        _consume(resource_auto_use.execute_resource_auto_use_task(
            object(),
            {},
            {},
            threading.Event(),
            talisman_reader=lambda: snapshot,
            pet_reader=_empty_pet,
        ))


def test_formal_adapter_must_reobserve_a_complete_terminal_snapshot(monkeypatch):
    order = []
    monkeypatch.setattr(
        resource_auto_use,
        "execute_storage_bag_quick_operation_task",
        _storage_success(order),
    )
    executable = {
        "complete": True,
        "source": "TalismanModel.GetAllUpgradeableTalismanList",
        "candidates": [{
            "talisman_id": 501,
            "category": "法宝",
            "owned": True,
            "active": True,
            "upgrade_count": 1,
            "resources": [{
                "kind": "talisman_upgrade_material",
                "item_id": 7001,
                "quantity": 1,
            }],
        }],
    }
    observations = iter((executable, _empty_talisman()))

    def adapter(*_args):
        order.append("法宝动作")
        yield None
        return {"ok": True}

    def pet_adapter(*_args):
        yield None
        return {"ok": True, "verified": True}

    result = _consume(resource_auto_use.execute_resource_auto_use_task(
        object(),
        {},
        {},
        threading.Event(),
        talisman_reader=lambda: next(observations),
        pet_reader=_empty_pet,
        talisman_adapter=adapter,
        pet_adapter=pet_adapter,
    ))

    assert result["ok"] is True
    assert order == ["储物袋", "法宝动作"]
    assert result["domains"][1]["outcome"] == "complete"
    assert result["domains"][1]["action_result"] == {"ok": True}


def test_pet_formal_adapter_is_enabled_by_default(monkeypatch):
    order = []
    monkeypatch.setattr(
        resource_auto_use,
        "execute_storage_bag_quick_operation_task",
        _storage_success(order),
    )
    executable = {
        "complete": True,
        "source": "PetData.CheckPetCardUpCount",
        "candidates": [{
            "pet_id": 7101,
            "therion_type": 0,
            "owned": True,
            "current_level": 94,
            "upgrade_count": 1,
            "target_level": 95,
            "resources": [{
                "kind": "ordinary_pet_upgrade_item",
                "item_id": 8017101,
                "quantity": 1,
                "available": 1,
            }],
        }],
    }
    empty = _empty_pet()
    observations = iter((executable, empty))

    def adapter(*_args):
        order.append("灵兽动作")
        yield None
        return {"ok": True, "verified": True}

    result = _consume(resource_auto_use.execute_resource_auto_use_task(
        object(),
        {},
        {},
        threading.Event(),
        talisman_reader=_empty_talisman,
        pet_reader=lambda: next(observations),
        pet_adapter=adapter,
    ))

    assert result["ok"] is True
    assert order == ["储物袋", "灵兽动作"]


def test_resource_auto_use_is_single_daily_standard_job():
    default_jobs.register_fanxiu_default_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "resource_auto_use"
    )
    assert definition is not None
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    assert definition.standard_job_id == "resource-auto-use"
    assert definition.standard_job_description == "每日"

    tasks = default_kernel_scheduler_tasks(datetime(2026, 8, 14, 2, 0))
    matches = [task for task in tasks if task["id"] == "resource-auto-use"]
    assert len(matches) == 1
    assert matches[0]["task_type"] == "resource_auto_use"
    assert matches[0]["trigger_description"] == "每日"
    assert matches[0]["next_time"] == "2026-08-15 00:00:00"
    assert matches[0]["error_retry_delay_seconds"] == 600
    assert matches[0]["payload"] == {"max_rounds": 3, "max_execution_seconds": 10800}
    assert len([task for task in tasks if task["id"] == "storage-bag-operation"]) == 1


def test_resource_auto_use_failure_does_not_install_an_automatic_retry():
    task = next(
        item
        for item in default_kernel_scheduler_tasks()
        if item["id"] == "resource-auto-use"
    )
    task["next_time"] = "2026-08-14 03:30:00"

    kernel_scheduler_control.schedule_failed_task_retry(
        task,
        datetime(2026, 8, 14, 3, 31, 0),
        job_group_enabled=False,
    )

    assert task["next_time"] == "2026-08-14 03:30:00"


def test_scheduler_migration_preserves_independent_storage_bag_instance():
    migrated, changed = consolidate_arena_scheduler_instances([
        {
            "id": "storage-bag-operation",
            "task_type": "storage_bag_operation",
            "next_time": "2026-08-14 01:00:00",
        },
        {
            "id": "resource-auto-use",
            "task_type": "resource_auto_use",
            "next_time": None,
        },
    ])

    assert changed is True
    assert [task["id"] for task in migrated] == [
        "storage-bag-operation",
        "resource-auto-use",
    ]


def test_independent_storage_dispatches_selected_items_without_daily_quick_operation(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import storage_bag_operation, storage_bag_auto_claim_execution
    calls = []

    def selected(*args):
        calls.append(args)
        yield None
        return {"ok": True, "outcome": "complete", "executed_count": 0}

    def forbidden(*args):
        raise AssertionError("独立任务不得调用每日快捷操作")

    monkeypatch.setattr(storage_bag_auto_claim_execution, "execute_storage_bag_auto_claim_task", selected)
    monkeypatch.setattr(storage_bag_operation, "execute_storage_bag_quick_operation_task", forbidden)
    args = (object(), {}, {}, threading.Event())
    result = _consume(storage_bag_operation.execute_storage_bag_operation_task(*args))
    assert calls == [args]
    assert result["executed_count"] == 0
