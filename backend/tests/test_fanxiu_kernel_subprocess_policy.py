from __future__ import annotations

import os
import subprocess

from backend.core.fanxiu.behavior_tree import jupyter_kernel


def test_fanxiu_kernel_child_env_enforces_no_window_policy() -> None:
    env = jupyter_kernel.fanxiu_kernel_child_env()

    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["OMP_NUM_THREADS"] == "4"
    assert env["MKL_NUM_THREADS"] == "4"
    assert env["OPENBLAS_NUM_THREADS"] == "4"
    assert env["NUMEXPR_NUM_THREADS"] == "4"
    assert env["OPENCV_FOR_THREADS_NUM"] == "4"
    assert env["PYTHONUTF8"] == "1"
    if os.name == "nt":
        assert env["CODEYUN_NO_WINDOW_SUBPROCESS_DEFAULT"] == "1"
        assert "no_window_sitecustomize" in env["PYTHONPATH"]


def test_kernel_service_installs_process_wide_no_window_policy(monkeypatch) -> None:
    installed: list[bool] = []

    monkeypatch.setattr(
        jupyter_kernel,
        "install_child_process_no_window_default",
        lambda: installed.append(True) or True,
    )

    class StopAfterImport(RuntimeError):
        pass

    original_import = __import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "jupyter_client":
            raise StopAfterImport
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", guarded_import)

    try:
        jupyter_kernel.run_fanxiu_jupyter_kernel_service(entry_id="1")
    except StopAfterImport:
        pass

    assert installed == [True]


def test_installed_policy_adds_create_no_window_to_bare_popen(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_original_init(self, *args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(subprocess.Popen, "_codeyun_no_window_default", False, raising=False)
    monkeypatch.setattr(subprocess.Popen, "__init__", fake_original_init)

    assert jupyter_kernel.install_child_process_no_window_default() is (os.name == "nt")
    if os.name == "nt":
        subprocess.Popen(["adb.exe", "devices"])
        assert int(captured["creationflags"]) & subprocess.CREATE_NO_WINDOW
        assert captured["startupinfo"] is not None
