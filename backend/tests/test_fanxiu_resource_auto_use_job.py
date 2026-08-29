from __future__ import annotations

import threading
from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation import default_jobs
from backend.core.fanxiu.data_annotation import behavior_tree_control
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    consolidate_arena_scheduler_instances,
    default_data_annotation_scheduler_tasks,
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


def _selected_storage_success(*_args, **_kwargs):
    yield None
    return {"ok": True, "outcome": "complete", "executed_count": 0}


def _selected_storage_preflight_success(*_args, **_kwargs):
    yield None
    return {"ok": True, "outcome": "ready", "action_count": 0}


def test_aggregate_runs_storage_then_zero_ui_talisman_and_pet(monkeypatch):
    order = []
    monkeypatch.setattr(
        resource_auto_use,
        "execute_storage_bag_operation_task",
        _storage_success(order),
    )

    def talisman_reader():
        order.append("法宝")
        return _empty_talisman()

    def pet_reader():
        order.append("灵兽")
        return _empty_pet()

    def selected_preflight(*_args, **_kwargs):
        order.append("勾选预检")
        yield None
        return {"ok": True, "outcome": "ready"}

    def selected_executor(*_args, **_kwargs):
        order.append("勾选执行")
        yield None
        return {"ok": True, "outcome": "complete", "executed_count": 0}

    result = _consume(resource_auto_use.execute_resource_auto_use_task(
        object(),
        {},
        {},
        threading.Event(),
        talisman_reader=talisman_reader,
        pet_reader=pet_reader,
        storage_auto_claim_preflight=selected_preflight,
        storage_auto_claim_executor=selected_executor,
    ))

    assert result["ok"] is True
    assert order == ["勾选预检", "储物袋", "勾选执行", "法宝", "灵兽"]
    assert [item["outcome"] for item in result["domains"][1:]] == [
        "complete",
        "complete",
    ]


def test_incomplete_domain_snapshot_fails_closed_before_later_domain(monkeypatch):
    order = []
    monkeypatch.setattr(
        resource_auto_use,
        "execute_storage_bag_operation_task",
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
            storage_auto_claim_preflight=_selected_storage_preflight_success,
            storage_auto_claim_executor=_selected_storage_success,
        ))

    assert order == ["储物袋"]


def test_executable_domain_without_formal_adapter_fails_closed(monkeypatch):
    order = []
    monkeypatch.setattr(
        resource_auto_use,
        "execute_storage_bag_operation_task",
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
            storage_auto_claim_preflight=_selected_storage_preflight_success,
            storage_auto_claim_executor=_selected_storage_success,
        ))


def test_formal_adapter_must_reobserve_a_complete_terminal_snapshot(monkeypatch):
    order = []
    monkeypatch.setattr(
        resource_auto_use,
        "execute_storage_bag_operation_task",
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

    result = _consume(resource_auto_use.execute_resource_auto_use_task(
        object(),
        {},
        {},
        threading.Event(),
        talisman_reader=lambda: next(observations),
        pet_reader=_empty_pet,
        talisman_adapter=adapter,
        storage_auto_claim_preflight=_selected_storage_preflight_success,
        storage_auto_claim_executor=_selected_storage_success,
    ))

    assert result["ok"] is True
    assert order == ["储物袋", "法宝动作"]
    assert result["domains"][1]["outcome"] == "complete"
    assert result["domains"][1]["action_result"] == {"ok": True}


def test_pet_formal_adapter_is_enabled_by_default(monkeypatch):
    order = []
    monkeypatch.setattr(
        resource_auto_use,
        "execute_storage_bag_operation_task",
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
        storage_auto_claim_preflight=_selected_storage_preflight_success,
        storage_auto_claim_executor=_selected_storage_success,
    ))

    assert result["ok"] is True
    assert order == ["储物袋", "灵兽动作"]


def test_selected_batch_preflight_blocks_before_quick_operation(monkeypatch):
    quick_operation_calls = []

    def quick_operation(*_args, **_kwargs):
        quick_operation_calls.append(True)
        yield None
        return {"ok": True}

    def blocked_preflight(*_args, **_kwargs):
        if False:
            yield None
        raise RuntimeError("unsupported selected template")

    monkeypatch.setattr(
        resource_auto_use,
        "execute_storage_bag_operation_task",
        quick_operation,
    )
    with pytest.raises(RuntimeError, match="unsupported selected template"):
        _consume(resource_auto_use.execute_resource_auto_use_task(
            object(),
            {},
            {},
            threading.Event(),
            talisman_reader=_empty_talisman,
            pet_reader=_empty_pet,
            storage_auto_claim_preflight=blocked_preflight,
            storage_auto_claim_executor=_selected_storage_success,
        ))

    assert quick_operation_calls == []


def test_unadapted_selected_items_skip_broad_quick_operation_but_run_safe_executor(
    monkeypatch,
):
    order = []

    def quick_operation(*_args, **_kwargs):
        order.append("不应宽泛快捷操作")
        yield None
        return {"ok": True}

    def preflight(*_args, **_kwargs):
        order.append("勾选预检")
        yield None
        return {
            "ok": True,
            "outcome": "ready",
            "quick_operation_allowed": False,
            "quick_operation_blockers": [{
                "base_id": 1010,
                "name": "VIP经验",
                "template": "direct_use",
                "quantity": 2,
            }],
        }

    def selected_executor(*_args, **_kwargs):
        order.append("安全精确适配器")
        yield None
        return {
            "ok": True,
            "outcome": "complete",
            "executed_count": 36,
            "deferred_count": 4,
        }

    monkeypatch.setattr(
        resource_auto_use,
        "execute_storage_bag_operation_task",
        quick_operation,
    )
    result = _consume(resource_auto_use.execute_resource_auto_use_task(
        object(),
        {},
        {},
        threading.Event(),
        talisman_reader=_empty_talisman,
        pet_reader=_empty_pet,
        storage_auto_claim_preflight=preflight,
        storage_auto_claim_executor=selected_executor,
    ))

    assert order == ["勾选预检", "安全精确适配器"]
    assert result["ok"] is True
    assert result["outcome"] == "partial_safe"
    assert result["domains"][0]["outcome"] == "partial_safe"
    assert (
        result["domains"][0]["quick_operation"]["outcome"]
        == "skipped_fail_closed"
    )


def test_resource_auto_use_is_single_manual_standard_job():
    default_jobs.register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "resource_auto_use"
    )
    assert definition is not None
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    assert definition.standard_job_id == "resource-auto-use"
    assert definition.standard_job_description == "手动"

    tasks = default_data_annotation_scheduler_tasks(datetime(2026, 8, 14, 2, 0))
    matches = [task for task in tasks if task["id"] == "resource-auto-use"]
    assert len(matches) == 1
    assert matches[0]["task_type"] == "resource_auto_use"
    assert matches[0]["trigger_description"] == "手动"
    assert matches[0]["next_time"] is None
    assert matches[0]["error_retry_delay_seconds"] == 0
    assert matches[0]["payload"] == {"max_rounds": 3}
    assert len([task for task in tasks if task["id"] == "storage-bag-operation"]) == 1


def test_resource_auto_use_failure_does_not_install_an_automatic_retry():
    task = next(
        item
        for item in default_data_annotation_scheduler_tasks()
        if item["id"] == "resource-auto-use"
    )
    task["next_time"] = "2026-08-14 03:30:00"

    behavior_tree_control.schedule_failed_task_retry(
        task,
        datetime(2026, 8, 14, 3, 31, 0),
    )

    assert task["next_time"] is None


def test_successful_manual_job_explicitly_restores_none_next_time(monkeypatch):
    default_jobs.register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "resource_auto_use"
    )
    writes = []

    class Runner:
        def _persist_scheduler_task_next_time(self, task_id, next_time):
            writes.append((task_id, next_time))

    def execute(*_args, **_kwargs):
        yield None
        return {"ok": True, "outcome": "complete"}

    monkeypatch.setattr(resource_auto_use, "execute_resource_auto_use_task", execute)
    result = _consume(definition.handler(
        Runner(),
        {},
        {},
        threading.Event(),
    ))

    assert result["ok"] is True
    assert writes == [("resource-auto-use", None)]


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

    assert changed is False
    assert [task["id"] for task in migrated] == [
        "storage-bag-operation",
        "resource-auto-use",
    ]
