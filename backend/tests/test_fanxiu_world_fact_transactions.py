"""真实文件锁与 JSON 事务检查；不构造游戏状态或执行 Runtime。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import time

import pytest

from backend.core.fanxiu.data_annotation.state import (
    edit_data_annotation_world_facts,
    persist_kernel_scheduler_status,
    read_data_annotation_world_facts,
    record_data_annotation_discovery,
    record_kernel_scheduler_task_fact,
)


def test_concurrent_edits_and_status_writes_preserve_independent_facts(tmp_path):
    facts_path = tmp_path / "facts.json"
    barrier = Barrier(3)

    def increment(key):
        barrier.wait()
        for _ in range(8):
            with edit_data_annotation_world_facts(facts_path) as facts:
                discoveries = facts["discoveries"]
                value = discoveries.get(key, 0)
                time.sleep(0.002)
                discoveries[key] = value + 1

    def telemetry():
        barrier.wait()
        for index in range(8):
            persist_kernel_scheduler_status(
                tmp_path / "execution.json", facts_path,
                {"current_scene": 34, "message": str(index)},
            )
            record_kernel_scheduler_task_fact(facts_path, {"id": str(index)}, "success")

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(increment, "a"), pool.submit(increment, "b"), pool.submit(telemetry)]
        for future in futures:
            future.result(timeout=15)
    facts = read_data_annotation_world_facts(facts_path)
    assert facts["discoveries"]["a"] == facts["discoveries"]["b"] == 8
    assert set(facts["discoveries"]["task"]) == {str(i) for i in range(8)}
    assert len([event for event in facts["events"] if event.get("kind") == "scheduler_task"]) == 8
    assert facts["context"]["message"] == "7"


def test_discovery_replacement_and_aborted_transaction(tmp_path):
    path = tmp_path / "facts.json"
    value = {"rows": [{"done": True}]}
    record_data_annotation_discovery(path, "daily_audit", value)
    value["rows"][0]["done"] = False
    record_data_annotation_discovery(path, "assistant", {"business_date": "2026-09-26"})
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="abort"):
        with edit_data_annotation_world_facts(path) as facts:
            facts["discoveries"].clear()
            raise RuntimeError("abort")
    assert path.read_bytes() == before
    facts = read_data_annotation_world_facts(path)
    assert facts["discoveries"]["daily_audit"]["rows"][0]["done"] is True
    assert facts["discoveries"]["assistant"]["business_date"] == "2026-09-26"


def test_no_change_transaction_does_not_create_or_rewrite_state(tmp_path):
    path = tmp_path / "facts.json"
    with edit_data_annotation_world_facts(path):
        pass
    assert not path.exists()
    record_data_annotation_discovery(path, "audit", {"done": True})
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    with edit_data_annotation_world_facts(path):
        pass
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_reset_deletes_only_selected_fact_from_latest_snapshot(tmp_path, monkeypatch):
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control

    facts_path = tmp_path / "facts.json"
    tasks_path = tmp_path / "tasks.json"
    selected = "legacy-daily-assistant"
    record_kernel_scheduler_task_fact(facts_path, {"id": selected}, "success")
    write_tasks = control.write_scheduler_tasks

    def write_and_interleave(tasks, **kwargs):
        result = write_tasks(tasks, **kwargs)
        if selected in (kwargs.get("execution_update_ids") or set()):
            # 另一写入发生在重置读取备份之后、删除事实之前。
            record_data_annotation_discovery(facts_path, "new_audit", {"done": True})
            record_kernel_scheduler_task_fact(facts_path, {"id": "other-task"}, "success")
        return result

    monkeypatch.setattr(control, "write_scheduler_tasks", write_and_interleave)
    result = control.reset_scheduler_task_runs(
        task_ids=[selected], scheduler_state_path=tasks_path, world_facts_path=facts_path,
    )
    facts = read_data_annotation_world_facts(facts_path)
    assert result["reset_ids"] == [selected]
    assert selected not in facts["discoveries"]["task"]
    assert facts["discoveries"]["task"]["other-task"]["last_result"] == "success"
    assert facts["discoveries"]["new_audit"] == {"done": True}


def test_bubble_checkpoints_survive_concurrent_claims_and_roll_over_by_week(tmp_path):
    from datetime import datetime
    from backend.core.fanxiu.data_annotation.tasks.bubble_lifecycle import (
        bubble_claimed_item_ids,
        record_bubble_claim_item,
        record_bubble_hidden,
        read_bubble_lifecycle_fact,
    )

    path = tmp_path / "new-directory" / "facts.json"
    now = datetime(2026, 9, 26, 2)
    barrier = Barrier(3)

    def claim(item_id):
        barrier.wait()
        record_bubble_claim_item(path, now=now, item_id=item_id)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(claim, item) for item in ("a", "b", "a")]
        for future in futures:
            future.result(timeout=10)
    record_bubble_hidden(path, now=now)
    assert bubble_claimed_item_ids(path, now=now) == {"a", "b"}
    assert read_bubble_lifecycle_fact(path)["partial_claim_count"] == 2
    following_week = datetime(2026, 9, 28, 1)
    assert bubble_claimed_item_ids(path, now=following_week) == set()
    record_bubble_claim_item(path, now=following_week, item_id="c")
    assert bubble_claimed_item_ids(path, now=following_week) == {"c"}
