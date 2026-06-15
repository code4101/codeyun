import sys

from backend.core.attendance import behavior_tree_service as attendance_service


class _FakeProcess:
    def __init__(self, pid: int, parent_pid: int | None, command: list[str]):
        self.pid = pid
        self._parent_pid = parent_pid
        self._command = command

    def name(self):
        return "python.exe"

    def cmdline(self):
        return self._command

    def ppid(self):
        return self._parent_pid

    def create_time(self):
        return 1_700_000_000 + self.pid


def test_attendance_status_counts_root_processes_separately_from_children(monkeypatch):
    root = _FakeProcess(2756, 100, [sys.executable, "kqmain.py"])
    child_a = _FakeProcess(2888, 2756, [sys.executable, "-c", "worker"])
    child_b = _FakeProcess(2999, 2756, [sys.executable, "-c", "helper"])

    monkeypatch.setattr(
        attendance_service,
        "_collect_attendance_process_targets",
        lambda: (
            {2756: (root, "cmd:attendance-script")},
            {
                2756: (root, "cmd:attendance-script"),
                2888: (child_a, "descendant-of:2756"),
                2999: (child_b, "descendant-of:2756"),
            },
        ),
    )
    monkeypatch.setattr(
        attendance_service,
        "_status_paths",
        lambda: {
            "root": ".",
            "scheduler_path": ".",
            "state_path": ".",
            "lock_path": ".",
            "behavior_tree_log_path": ".",
            "service_log_path": ".",
            "script_path": __file__,
            "python_path": sys.executable,
            "cwd": ".",
        },
    )
    monkeypatch.setattr(
        attendance_service,
        "_read_json_file",
        lambda _path: {"nodes": {"daily": {"next_run_at": "2026-05-21 21:00:00"}}},
    )

    status = attendance_service.get_attendance_behavior_tree_status()

    assert status["state_label"] == "运行中"
    assert status["pid"] == 2756
    assert status["process_count"] == 1
    assert status["child_process_count"] == 2
    assert status["total_process_count"] == 3
    assert status["last_error"] == ""
    assert [item["pid"] for item in status["root_processes"]] == [2756]
    assert [item["matched_reason"] for item in status["processes"]] == [
        "cmd:attendance-script",
        "descendant-of:2756",
        "descendant-of:2756",
    ]
