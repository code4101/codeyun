from __future__ import annotations

import subprocess

from backend.core.runtime import process_launcher


def test_run_quiet_delegates_to_hidden_runner(monkeypatch):
    calls = []

    def fake_run_hidden(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    monkeypatch.setattr(process_launcher, "run_hidden", fake_run_hidden)

    result = process_launcher.run_quiet(["tool"], text=True)

    assert result.stdout == "ok"
    assert calls == [(["tool"], {"text": True})]


def test_check_call_quiet_enforces_check(monkeypatch):
    calls = []

    def fake_run_hidden(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(process_launcher, "run_hidden", fake_run_hidden)

    process_launcher.check_call_quiet(["tool"])

    assert calls == [(["tool"], {"check": True})]


def test_check_output_quiet_uses_pipe_and_check(monkeypatch):
    calls = []

    def fake_run_hidden(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=b"data")

    monkeypatch.setattr(process_launcher, "run_hidden", fake_run_hidden)

    assert process_launcher.check_output_quiet(["tool"]) == b"data"
    assert calls == [(["tool"], {"stdout": subprocess.PIPE, "check": True})]


def test_popen_service_delegates_to_background_runner(monkeypatch):
    calls = []

    class FakePopen:
        pass

    def fake_popen_background(command, **kwargs):
        calls.append((command, kwargs))
        return FakePopen()

    monkeypatch.setattr(process_launcher, "popen_background", fake_popen_background)

    proc = process_launcher.popen_service(["tool"], cwd="repo")

    assert isinstance(proc, FakePopen)
    assert calls == [(["tool"], {"cwd": "repo"})]


def test_apply_managed_child_env_delegates_to_runtime_policy(monkeypatch):
    calls = []

    def fake_managed_child_env(env, *, root_dir=None):
        calls.append((env, root_dir))
        return {"patched": "1"}

    monkeypatch.setattr(process_launcher, "managed_child_env", fake_managed_child_env)

    assert process_launcher.apply_managed_child_env({"A": "B"}, root_dir="D:/repo") == {"patched": "1"}
    assert calls == [({"A": "B"}, "D:/repo")]
