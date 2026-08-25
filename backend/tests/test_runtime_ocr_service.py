from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.runtime import ocr_service


class _CompletingStartLock:
    """Test lock that simulates another process finishing while we wait."""

    def __init__(self, _path: str, *, timeout: float, on_enter) -> None:
        self.timeout = timeout
        self._on_enter = on_enter

    def __enter__(self):
        self._on_enter()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return None


def test_start_ocr_service_reuses_competing_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = {
        "running": True,
        "device": "gpu",
        "pids": [101],
    }

    monkeypatch.setattr(ocr_service, "get_ocr_service_status", lambda: dict(status))
    monkeypatch.setattr(ocr_service, "get_ocr_service_start_lock_path", lambda: tmp_path / "ocr.lock")

    def complete_competing_replacement() -> None:
        status.update(running=True, device="cpu", pids=[202])

    monkeypatch.setattr(
        ocr_service,
        "FileLock",
        lambda path, timeout: _CompletingStartLock(path, timeout=timeout, on_enter=complete_competing_replacement),
    )
    monkeypatch.setattr(
        ocr_service,
        "stop_ocr_service",
        lambda: pytest.fail("the competing replacement must not be stopped"),
    )
    monkeypatch.setattr(
        ocr_service,
        "_start_ocr_service_unlocked",
        lambda **_kwargs: pytest.fail("a second daemon must not be started"),
    )

    result = ocr_service.start_ocr_service(
        replace_existing=True,
        env_overrides={"CODEYUN_OCR_DEVICE": "cpu"},
    )

    assert result["service"]["pids"] == [202]
    assert result["service"]["device"] == "cpu"


def test_start_ocr_service_replaces_running_daemon_when_device_mismatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = {
        "running": True,
        "device": "cpu",
        "pids": [101],
    }
    stopped: list[bool] = []
    started: list[dict] = []

    monkeypatch.setattr(ocr_service, "get_ocr_service_status", lambda: dict(status))
    monkeypatch.setattr(ocr_service, "get_ocr_service_start_lock_path", lambda: tmp_path / "ocr.lock")
    monkeypatch.setattr(ocr_service, "get_settings", lambda: SimpleNamespace(ocr_device="gpu"))

    def fake_stop():
        stopped.append(True)
        status.update(running=False, pids=[])
        return {"status": "stopped", "service": dict(status)}

    def fake_start(**kwargs):
        started.append(kwargs)
        return {"status": "started", "service": {"running": True, "device": "gpu", "pids": [202]}}

    monkeypatch.setattr(ocr_service, "stop_ocr_service", fake_stop)
    monkeypatch.setattr(ocr_service, "_start_ocr_service_unlocked", fake_start)

    result = ocr_service.start_ocr_service(replace_existing=False)

    assert stopped == [True]
    assert started == [{"wait_seconds": 20.0, "env_overrides": None}]
    assert result["service"]["device"] == "gpu"
