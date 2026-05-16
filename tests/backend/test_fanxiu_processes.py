from __future__ import annotations

from backend.core import fanxiu_processes
from backend.core import local_script_processes


def test_match_fanxiu_process_fields_recognizes_codex_continue_loader():
    reason = fanxiu_processes.match_fanxiu_process_fields(
        name="python.exe",
        command_line="python -c \"import os; exec(os.environ['CODEX_FX_CONTINUE_CODE'])\"",
    )

    assert reason == "cmd:codex-fx-env-loader"


def test_match_fanxiu_process_fields_recognizes_environment_marker():
    reason = fanxiu_processes.match_fanxiu_process_fields(
        name="python.exe",
        command_line="python -c pass",
        environ={"CODEX_FX_CONTINUE_CODE": "from xlsln.ckz2025.fx.tools import main"},
    )

    assert reason == "env-key:CODEX_FX_CONTINUE_CODE"


def test_match_fanxiu_process_fields_recognizes_fx_cwd_without_command_line():
    reason = fanxiu_processes.match_fanxiu_process_fields(
        name="python.exe",
        command_line="",
        cwd=r"C:\home\chenkunze\slns\xlproject\src\xlsln\ckz2025\fx",
    )

    assert reason == "cwd:fx-path"


def test_match_fanxiu_process_fields_does_not_treat_tool_cwd_as_fanxiu():
    reason = fanxiu_processes.match_fanxiu_process_fields(
        name="node_repl.exe",
        command_line=r"C:\Users\chen\AppData\Local\OpenAI\Codex\bin\node_repl.exe",
        cwd=r"C:\home\chenkunze\slns\xlproject\src\xlsln\ckz2025\fx",
    )

    assert reason is None


def test_match_fanxiu_process_fields_ignores_diagnostic_shell_search():
    reason = fanxiu_processes.match_fanxiu_process_fields(
        name="pwsh.exe",
        command_line="Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'CODEX_FX_CONTINUE_CODE' }",
    )

    assert reason is None


def test_match_fanxiu_process_fields_does_not_match_codeyun_dev_server():
    reason = fanxiu_processes.match_fanxiu_process_fields(
        name="python.exe",
        command_line=r"C:\home\chenkunze\slns\codeyun\.venv\Scripts\python.exe dev.py",
        cwd=r"C:\home\chenkunze\slns\codeyun",
    )

    assert reason is None


def test_match_fanxiu_process_fields_recognizes_xlproject_stdin_python():
    reason = fanxiu_processes.match_fanxiu_process_fields(
        name="python.exe",
        command_line=r"C:\home\chenkunze\slns\xlproject\.venv\Scripts\python.exe -",
        cwd=r"C:\home\chenkunze\slns\xlproject",
    )

    assert reason == "cmd+cwd:xlproject-python-stdin"


def test_match_fanxiu_process_fields_does_not_match_codeyun_stdin_python():
    reason = fanxiu_processes.match_fanxiu_process_fields(
        name="python.exe",
        command_line=r"C:\home\chenkunze\slns\codeyun\.venv\Scripts\python.exe -",
        cwd=r"C:\home\chenkunze\slns\codeyun",
    )

    assert reason is None


def test_local_script_processes_infers_python_stdin():
    inferred = local_script_processes._infer_script(
        [r"C:\home\chenkunze\slns\xlproject\.venv\Scripts\python.exe", "-"],
        r"C:\home\chenkunze\slns\xlproject",
    )

    assert inferred == ("python-stdin", "python -", None)


class FakeProcess:
    def __init__(
        self,
        pid: int,
        cmdline: list[str],
        *,
        name: str = "python.exe",
        ppid: int = 0,
        children: list["FakeProcess"] | None = None,
    ):
        self.pid = pid
        self._cmdline = cmdline
        self._name = name
        self._ppid = ppid
        self._children = children or []

    def cmdline(self):
        return self._cmdline

    def cwd(self):
        return None

    def environ(self):
        return {}

    def name(self):
        return self._name

    def ppid(self):
        return self._ppid

    def create_time(self):
        return 1_700_000_000

    def children(self, recursive: bool = False):
        return self._children


def test_list_fanxiu_processes_includes_descendants(monkeypatch):
    child = FakeProcess(102, ["python.exe", "-m", "paddle"], ppid=101)
    parent = FakeProcess(
        101,
        ["python.exe", "-c", "import os; exec(os.environ['CODEX_FX_CONTINUE_CODE'])"],
        children=[child],
    )

    monkeypatch.setattr(fanxiu_processes.os, "getpid", lambda: 999)
    monkeypatch.setattr(fanxiu_processes.psutil, "process_iter", lambda _attrs: iter([parent, child]))

    items = fanxiu_processes.list_fanxiu_processes()

    assert [item["pid"] for item in items] == [101, 102]
    assert items[0]["matched_reason"] == "cmd:codex-fx-env-loader"
    assert items[1]["matched_reason"] == "descendant-of:101"
