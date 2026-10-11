from backend.core.fanxiu.data_annotation.tasks.daily_task_result import project_daily_task_terminal
from backend.core.fanxiu.data_annotation.kernel_scheduler_results import (
    record_scheduler_cell_result, read_scheduler_cell_result,
)


def test_domain_evidence_survives_exact_attempt_receipt(tmp_path):
    evidence = {"current_scene": 34, "domains": [{"domain": "lundao",
        "status": "claimed", "claimed_task_ids": [11160109, 11160110],
        "cover_rebind_observations": [{"expected_scene_id": 296,
                                      "actual_scene_id": 549}]}]}
    terminal = project_daily_task_terminal(evidence, message="三域幂等完成")
    record_scheduler_cell_result("daily-task-rewards", "a" * 32,
        cell_result={"status": "ok"}, business_result=terminal,
        started_at="start", finished_at="end", scheduler_result="success",
        directory=tmp_path)
    receipt = read_scheduler_cell_result("daily-task-rewards", "a" * 32,
                                         directory=tmp_path)
    assert receipt["business_result"] == {**evidence, "result": "success",
                                          "message": "三域幂等完成"}
    assert "result" not in evidence  # Projection never mutates the flow evidence.


def test_framework_terminal_and_resolved_message_remain_authoritative():
    result = project_daily_task_terminal({"result": "partial", "message": "old",
        "domains": [{"status": "nothing_claimable"}]}, message="resolved")
    assert result == {"result": "success", "message": "resolved",
                      "domains": [{"status": "nothing_claimable"}]}


def test_legacy_non_mapping_flow_has_unchanged_terminal_contract():
    for value in (None, "success", True):
        assert project_daily_task_terminal(value, message="done") == {
            "result": "success", "message": "done"}
