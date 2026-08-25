import sys

from backend.core.attendance import behavior_tree_service as attendance_service
from backend.core import attendance_behavior_tree_service as attendance_service_impl
from backend.core.runtime import management as runtime_management


def test_attendance_behavior_tree_defaults_to_mf_host(monkeypatch):
    monkeypatch.delenv("KQ_BEHAVIOR_TREE_SERVICE_ENABLED", raising=False)
    monkeypatch.delenv("KQ_BEHAVIOR_TREE_SERVICE_HOSTS", raising=False)

    monkeypatch.setattr(attendance_service_impl, "get_attendance_hostname", lambda: "codepc_mf")
    assert attendance_service_impl.is_attendance_behavior_tree_service_enabled() is True

    monkeypatch.setattr(attendance_service_impl, "get_attendance_hostname", lambda: "codepc_mi15")
    assert attendance_service_impl.is_attendance_behavior_tree_service_enabled() is False


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
        attendance_service_impl,
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
        attendance_service_impl,
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
        attendance_service_impl,
        "_read_json_file",
        lambda _path: {"nodes": {"daily": {"next_run_at": "2026-05-21 21:00:00"}}},
    )

    status = attendance_service_impl.get_attendance_behavior_tree_status()

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


def test_matches_attendance_script_ignores_maintenance_cli_arguments():
    proc = _FakeProcess(
        3001,
        100,
        [sys.executable, r"C:\home\chenkunze\slns\xlproject\src\xlsln\kq5034\kqmain.py", "show_schedule", "--limit=5"],
    )

    matched = attendance_service_impl._matches_attendance_script(
        proc,
        attendance_service_impl.Path(r"C:\home\chenkunze\slns\xlproject\src\xlsln\kq5034\kqmain.py"),
        assume_python_process=True,
    )

    assert matched is None


def test_matches_attendance_script_accepts_bare_service_entry():
    proc = _FakeProcess(
        3002,
        100,
        [sys.executable, r"C:\home\chenkunze\slns\xlproject\src\xlsln\kq5034\kqmain.py"],
    )

    matched = attendance_service_impl._matches_attendance_script(
        proc,
        attendance_service_impl.Path(r"C:\home\chenkunze\slns\xlproject\src\xlsln\kq5034\kqmain.py"),
        assume_python_process=True,
    )

    assert matched == "cmd:attendance-script"


def test_attendance_status_surfaces_state_last_error(monkeypatch):
    root = _FakeProcess(2756, 100, [sys.executable, "kqmain.py"])

    monkeypatch.setattr(
        attendance_service_impl,
        "_collect_attendance_process_targets",
        lambda: (
            {2756: (root, "cmd:attendance-script")},
            {2756: (root, "cmd:attendance-script")},
        ),
    )
    monkeypatch.setattr(
        attendance_service_impl,
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
        attendance_service_impl,
        "_read_json_file",
        lambda _path: {
            "last_error": "每日早晨课程任务：2 个课程入口失败",
            "nodes": {"daily": {"next_run_at": "2026-05-21 21:00:00"}},
        },
    )

    status = attendance_service_impl.get_attendance_behavior_tree_status()

    assert status["state_last_error"] == "每日早晨课程任务：2 个课程入口失败"
    assert status["last_error"] == "每日早晨课程任务：2 个课程入口失败"


def test_show_schedule_uses_script_command_and_returns_service_status(monkeypatch):
    monkeypatch.setattr(
        attendance_service_impl,
        "_run_attendance_script_command",
        lambda *args, **kwargs: {"stdout": "schedule ok", "stderr": "", "returncode": 0},
    )
    monkeypatch.setattr(
        attendance_service_impl,
        "get_attendance_behavior_tree_status",
        lambda: {"key": "attendance-behavior-tree", "running": True},
    )

    payload = attendance_service.show_attendance_behavior_tree_schedule(limit=5)

    assert payload == {
        "status": "ok",
        "stdout": "schedule ok",
        "stderr": "",
        "service": {"key": "attendance-behavior-tree", "running": True},
    }


def test_reset_state_uses_script_command_and_returns_service_status(monkeypatch):
    monkeypatch.setattr(
        attendance_service_impl,
        "_run_attendance_script_command",
        lambda *args, **kwargs: {"stdout": "reset ok", "stderr": "", "returncode": 0},
    )
    monkeypatch.setattr(
        attendance_service_impl,
        "get_attendance_behavior_tree_status",
        lambda: {"key": "attendance-behavior-tree", "running": False},
    )

    payload = attendance_service.reset_attendance_behavior_tree_state()

    assert payload == {
        "status": "ok",
        "stdout": "reset ok",
        "stderr": "",
        "service": {"key": "attendance-behavior-tree", "running": False},
    }


def test_probe_attendance_subprocess_utf8_uses_utf8_env(monkeypatch):
    captured = {}

    class _Result:
        returncode = 0
        stdout = "utf-8\n考勤中文探针\n"
        stderr = "utf-8\n"

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        captured["encoding"] = kwargs["encoding"]
        captured["errors"] = kwargs["errors"]
        return _Result()

    monkeypatch.setattr(
        attendance_service_impl,
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
    monkeypatch.setattr(attendance_service_impl, "run_quiet", fake_run)

    result = attendance_service_impl.probe_attendance_subprocess_utf8()

    assert result["returncode"] == 0
    assert "考勤中文探针" in result["stdout"]
    assert captured["command"][1] == "-c"
    assert captured["env"]["PYTHONUTF8"] == "1"
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"


def test_log_lines_include_status_and_log_timestamps(monkeypatch):
    monkeypatch.setattr(
        attendance_service_impl,
        "get_attendance_behavior_tree_status",
        lambda: {
            "title": "考勤行为树",
            "state_label": "运行中",
            "pid": 123,
            "process_count": 1,
            "child_process_count": 2,
            "total_process_count": 3,
            "started_at": "2026-07-01 07:10:00",
            "next_run_at": "2026-07-01 21:00:00",
            "state_updated_at": "2026-07-01 12:31:13",
            "behavior_tree_log_updated_at": "2026-07-01 12:31:14",
            "service_log_updated_at": "2026-07-01 12:31:15",
            "state_path": "state.json",
            "lock_path": "lock",
            "behavior_tree_log_path": "bt.log",
            "service_log_path": "service.log",
            "script_path": "kqmain.py",
            "processes": [
                {
                    "pid": 123,
                    "created_at": "2026-07-01 07:10:00",
                    "command_line": "python kqmain.py",
                    "matched_reason": "cmd:attendance-script",
                },
                {
                    "pid": 124,
                    "created_at": "2026-07-01 07:10:01",
                    "command_line": "python kqmain.py",
                    "matched_reason": "descendant-of:123",
                },
            ],
            "state_last_error": "课程入口失败：d260701第42届念住.main",
            "last_error": "",
        },
    )
    monkeypatch.setattr(attendance_service_impl, "_tail_text", lambda _path, **kwargs: ["tail"])

    lines = attendance_service_impl.build_attendance_behavior_tree_log_lines()

    assert "状态更新时间：2026-07-01 12:31:13" in lines
    assert "行为树日志更新时间：2026-07-01 12:31:14" in lines
    assert "服务日志更新时间：2026-07-01 12:31:15" in lines
    assert any("动作语义：trigger=确保唯一调度器" in line for line in lines)
    assert "行为树根进程数：1" in lines
    assert "子孙进程数：2" in lines
    assert any("不要把 descendant 误判成第二棵行为树" in line for line in lines)
    assert any("- root · PID 123" in line for line in lines)
    assert any("- descendant-of:123 · PID 124" in line for line in lines)


def test_log_lines_show_last_error_prompt(monkeypatch):
    monkeypatch.setattr(
        attendance_service_impl,
        "get_attendance_behavior_tree_status",
        lambda: {
            "title": "考勤行为树",
            "state_label": "运行中",
            "pid": 123,
            "process_count": 1,
            "child_process_count": 0,
            "total_process_count": 1,
            "started_at": "2026-07-01 07:10:00",
            "next_run_at": "2026-07-01 21:00:00",
            "state_updated_at": "2026-07-01 12:31:13",
            "behavior_tree_log_updated_at": "2026-07-01 12:31:14",
            "service_log_updated_at": "2026-07-01 12:31:15",
            "state_path": "state.json",
            "lock_path": "lock",
            "behavior_tree_log_path": "bt.log",
            "service_log_path": "service.log",
            "script_path": "kqmain.py",
            "processes": [],
            "state_last_error": "每日早晨课程任务：2 个课程入口失败",
            "last_error": "每日早晨课程任务：2 个课程入口失败",
        },
    )
    monkeypatch.setattr(attendance_service_impl, "_tail_text", lambda _path, **kwargs: ["tail"])

    lines = attendance_service_impl.build_attendance_behavior_tree_log_lines()

    assert "提示：每日早晨课程任务：2 个课程入口失败" in lines


def test_codeyun_startup_ensures_attendance_service_on_mf(monkeypatch):
    called = []
    monkeypatch.setattr(runtime_management, "is_attendance_behavior_tree_service_enabled", lambda: True)
    monkeypatch.setattr(
        runtime_management,
        "ensure_attendance_behavior_tree_service",
        lambda: called.append(True) or {"status": "already_running", "pid": 123},
    )
    monkeypatch.setattr(runtime_management, "start_codeyun_watchdog", lambda: {})
    monkeypatch.setattr(runtime_management, "start_proxy_traffic_audit", lambda: {})
    monkeypatch.setattr(runtime_management, "_local_builtin_service_autostart_enabled", lambda *_args, **_kwargs: False)

    result = runtime_management.ensure_local_builtin_services_on_startup()

    assert called == [True]
    assert result["attendance-behavior-tree"] == {"status": "already_running", "pid": 123}
