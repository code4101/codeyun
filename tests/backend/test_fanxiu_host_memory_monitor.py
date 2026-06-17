from scripts import fanxiu_host_memory_monitor as monitor


def test_memory_monitor_does_not_auto_terminate_processes_by_default(monkeypatch):
    monkeypatch.delenv("CODEYUN_HOST_MEMORY_MONITOR_AUTO_TERMINATE", raising=False)

    def fail_collect(*args, **kwargs):
        raise AssertionError("process termination target collection should be disabled by default")

    monkeypatch.setattr(monitor, "_collect_reclaimable_process_tree_targets", fail_collect)

    result = monitor._terminate_reclaimable_processes_under_pressure(
        {
            "committed_mb": 94000,
            "commit_limit_mb": 100000,
            "commit_available_mb": 6000,
            "commit_percent": 94.0,
        }
    )

    assert result == {
        "attempted": False,
        "reason": "host_commit_pressure_auto_termination_disabled",
        "terminated": [],
        "killed_after_timeout": [],
    }
