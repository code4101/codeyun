from __future__ import annotations

import os
import tempfile
from pathlib import Path

from backend.core import codeyun_watchdog_runtime


def test_codeyun_watchdog_default_paths_stay_outside_repo(monkeypatch):
    monkeypatch.delenv("CODEYUN_WATCHDOG_LOG", raising=False)
    monkeypatch.delenv("CODEYUN_WATCHDOG_LOCK", raising=False)

    log_path = codeyun_watchdog_runtime.get_codeyun_watchdog_log_path()
    lock_path = codeyun_watchdog_runtime.get_codeyun_watchdog_lock_path()
    temp_root = Path(tempfile.gettempdir()).resolve(strict=False)
    repo_root = codeyun_watchdog_runtime.ROOT_DIR.resolve(strict=False)

    assert log_path.is_relative_to(temp_root)
    assert lock_path.is_relative_to(temp_root)
    assert not log_path.is_relative_to(repo_root)
    assert not lock_path.is_relative_to(repo_root)
    assert os.fspath(log_path).endswith(os.path.join("codeyun", "codeyun-watchdog", "codeyun-watchdog.log"))
    assert os.fspath(lock_path).endswith(os.path.join("codeyun", "codeyun-watchdog", "codeyun-watchdog.pid"))
