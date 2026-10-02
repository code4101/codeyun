"""Deterministic tree identities, cycle isolation and attempt ownership contracts."""
from datetime import datetime
import json
from zoneinfo import ZoneInfo

from backend.core.fanxiu.data_annotation.subtask_execution import record_subtask_execution, subtask_node_id, subtask_log_context, current_subtask_log_context
from backend.core.fanxiu.data_annotation.subtask_tree import project_subtask_tree

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=TZ)


def project(task, **kwargs):
    return project_subtask_tree(task, now=NOW, schedule={}, theme_plan={}, ranking_rows=[], theme_rows=[], **kwargs)


def leaves(tree):
    def walk(nodes):
        for node in nodes:
            if node.kind == "subtask":
                yield node
            yield from walk(node.children)
    return list(walk(tree.nodes))


def test_newer_empty_ranking_plan_preserves_active_resource_instance(monkeypatch):
    from contextlib import nullcontext
    from backend.core.fanxiu.data_annotation import subtask_tree as trees
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control
    from backend.core.fanxiu.activity import daily_activity_sync, ranking_lifecycle_store
    raw = dict(id=32044301400004, activityId=32044301, activityType=12,
               baseId=44300, serverCount=32, name="灵装化道", avgWorldLevel=106,
               startTime=1790802005000, endTime=1790949600000,
               prepareEndTime=1790715600000, closePanelTime=1790956739000)
    monkeypatch.setattr(trees, "Session", lambda *_: nullcontext(None))
    monkeypatch.setattr(ranking_lifecycle_store, "list_ranking_checkpoint_rows", lambda *_: [])
    monkeypatch.setattr(control, "read_scheduler_tasks", lambda: [{"id": "resource-ranking"}])
    monkeypatch.setattr(control, "read_world_facts", lambda: {"discoveries": {
        "ranking_subtask_plan:resource-ranking": {"captured_at": "2026-10-01T08:00:00+08:00", "occurrences": []}}})
    monkeypatch.setattr(daily_activity_sync, "load_worldline_activity_schedule_snapshot", lambda: {
        "captured_at": "2026-10-01T07:00:00+08:00", "occurrences": [{"raw": raw}]})
    tree = trees.read_subtask_tree("resource-ranking", now=NOW)
    assert len(tree.nodes) == 1
    assert any(node.stage_id == "lingzhuang_resource_use_once" for node in leaves(tree))
    assert tree.fact_captured_at == "2026-10-01T07:00:00+08:00"


def test_stage_identity_is_qualified_by_instance_cycle_and_parent():
    assert len({subtask_node_id("theme", *identity) for identity in (
        ("instance-a", "daily", "2026-10-01"),
        ("instance-b", "daily", "2026-10-01"),
        ("instance-a", "daily", "2026-10-02"),
    )}) == 3
    assert subtask_node_id("a", "cycle", "stage") != subtask_node_id("b", "cycle", "stage")


def test_nested_log_context_is_restored_after_failure():
    import pytest
    assert current_subtask_log_context() == {}
    with subtask_log_context("parent", "attempt", "outer"):
        with pytest.raises(RuntimeError), subtask_log_context("parent", "attempt", "inner"):
            assert current_subtask_log_context()["subtask_id"] == "inner"
            raise RuntimeError("failure")
        assert current_subtask_log_context()["subtask_id"] == "outer"
    assert current_subtask_log_context() == {}


def test_log_views_preserve_subtask_and_attempt_identity():
    from backend.core.fanxiu.data_annotation.kernel_log_views import log_entries
    records = [dict(time="09:00:00", kind="action", scope="job", item_id="parent", message="same action",
                    subtask_id="node", attempt_id=attempt) for attempt in ("old", "new")]
    entries = log_entries(records)
    assert [e.attempt_id for e in entries] == ["old", "new"]
    assert all(e.subtask_id == "node" for e in entries)
    assert entries[0].id != entries[1].id


def test_formal_cell_logs_inherit_parent_and_do_not_leak_into_other_scopes():
    from types import SimpleNamespace
    from backend.core.fanxiu.data_annotation.behavior_tree_executor import BehaviorTreeExecutor
    from backend.core.fanxiu.data_annotation.subtask_execution import job_log_context
    runner = SimpleNamespace(_log_scope="", _log_item_id="", _status={})
    with job_log_context("parent", "attempt"), subtask_log_context("parent", "attempt", "node"):
        BehaviorTreeExecutor._log_locked(runner, "action", "receipt reused")
        BehaviorTreeExecutor._log_locked(runner, "action", "guard event", scope="guard", item_id="inspection")
    BehaviorTreeExecutor._log_locked(runner, "action", "ordinary cell")
    from backend.core.fanxiu.data_annotation.kernel_log_views import log_entries
    entries = log_entries(runner._status["logs"])
    assert (entries[0].scope, entries[0].item_id, entries[0].attempt_id, entries[0].subtask_id) == ("job", "parent", "attempt", "node")
    assert entries[1].scope == "guard" and not entries[1].subtask_id and not entries[1].attempt_id
    assert not entries[2].scope and not entries[2].subtask_id and not entries[2].attempt_id


def test_resource_running_cycle_uses_frozen_business_time_across_midnight():
    task = {"id": "resource-auto-use", "attempt_id": "new", "last_result": "running", "started_at": "2026-10-01 23:59:00", "payload": {
        "subtask_execution": {"attempt_id": "new", "business_time": "2026-09-30T23:59:00+08:00"}}}
    tree = project(task)
    assert tree.business_date == "2026-09-30"
    assert next(n for n in leaves(tree) if n.stage_id == "danling-upgrade").cycle_key == "2026-09-30"


def test_old_resource_receipt_does_not_complete_new_day_or_new_version():
    task = {"id": "resource-auto-use", "payload": {"aggregate_progress": {
        "2026-09-30": {"danling-upgrade": {"status": "complete", "stage_version": "1"}},
        "2026-10-01": {"danling-upgrade": {"status": "complete", "stage_version": "obsolete"}},
    }}}
    tree = project(task)
    node = next(n for n in leaves(tree) if n.stage_id == "danling-upgrade")
    assert node.status == "pending"
    assert tree.counts["not_applicable"] > 0


def test_only_live_matching_attempt_can_display_running():
    node_id = subtask_node_id("resource-auto-use", "2026-10-01", "danling-upgrade")
    task = {"id": "resource-auto-use", "attempt_id": "new", "last_result": "running", "payload": {
        "subtask_execution": {"attempt_id": "old", "node_id": node_id, "status": "running"}}}
    assert project(task).current_node_id is None
    task["payload"]["subtask_execution"]["attempt_id"] = "new"
    assert project(task).current_node_id == node_id
    task["last_result"] = "interrupted"
    assert project(task).current_node_id is None


def test_same_theme_name_instances_remain_separate_and_completion_is_local():
    occurrences = [dict(member_id="zero-purchase", activity_id=1, name="零元购", instance_key=key,
        start_at="2026-10-01T00:00:00+08:00", end_at="2026-10-03T00:00:00+08:00") for key in ("a", "b")]
    completed = dict(instance_key="a", member_id="zero-purchase", stage_kind="zero_purchase_daily_0000",
        business_date="2026-10-01", status="completed", completed_at=NOW.isoformat())
    tree = project_subtask_tree({"id": "theme-collection"}, now=NOW, schedule={},
        theme_plan={"occurrences": occurrences}, ranking_rows=[], theme_rows=[completed])
    assert len(tree.nodes) == 2
    assert [n.status for n in leaves(tree)] == ["completed", "due"]


def test_same_day_missed_theme_rounds_are_superseded():
    now = NOW.replace(hour=17)
    occurrence = dict(member_id="xianyuan-banquet", activity_id=304, name="仙园游宴", instance_key="banquet",
        start_at="2026-10-01T09:00:00+08:00", end_at="2026-10-04T22:00:00+08:00")
    tree = project_subtask_tree({"id": "theme-collection"}, now=now, schedule={},
        theme_plan={"occurrences": [occurrence]}, ranking_rows=[], theme_rows=[])
    rounds = [n for n in leaves(tree) if n.stage_id.startswith("garden_banquet_") and n.cycle_key == "2026-10-01"]
    assert sum(n.status == "due" for n in rounds) == 1
    assert any(n.status == "superseded" for n in rounds)
    assert any(n.status == "scheduled" for n in rounds)


def test_observation_write_rejects_stale_and_terminal_attempt_and_preserves_siblings(tmp_path):
    path = tmp_path / "jobs.json"
    task = {"id": "aggregate", "attempt_id": "new", "last_result": "running", "payload": {"receipt": "keep"}}
    sibling = {"id": "other", "payload": {"value": 3}}
    path.write_text(json.dumps([task, sibling]), encoding="utf-8")
    before = path.read_bytes()
    assert not record_subtask_execution("aggregate", "old", "node", "label", "running", scheduler_state_path=path)
    assert path.read_bytes() == before
    assert record_subtask_execution("aggregate", "new", "node", "label", "running", scheduler_state_path=path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data[1] == sibling
    assert data[0]["payload"]["receipt"] == "keep"
    assert not record_subtask_execution("aggregate", "new", "another", "label", "finished", scheduler_state_path=path)
    data[0]["last_result"] = "success"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not record_subtask_execution("aggregate", "new", "node", "label", "running", scheduler_state_path=path)
