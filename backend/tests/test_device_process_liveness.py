import psutil

from backend.core.devices import device as device_module


class _FakeProc:
    def __init__(self, *, running: bool, status_value: str | None = None, status_error: Exception | None = None):
        self._running = running
        self._status_value = status_value
        self._status_error = status_error
        self.status_calls = 0

    def is_running(self):
        return self._running

    def status(self):
        self.status_calls += 1
        if self._status_error is not None:
            raise self._status_error
        return self._status_value


def test_is_live_process_skips_status_probe_on_windows(monkeypatch):
    proc = _FakeProc(running=True, status_error=AssertionError("status() should be skipped on Windows"))
    monkeypatch.setattr(device_module, "WINDOWS_SKIP_PSUTIL_STATUS_CHECK", True)

    assert device_module._is_live_process(proc) is True
    assert proc.status_calls == 0


def test_is_live_process_keeps_zombie_check_off_windows(monkeypatch):
    proc = _FakeProc(running=True, status_value=psutil.STATUS_ZOMBIE)
    monkeypatch.setattr(device_module, "WINDOWS_SKIP_PSUTIL_STATUS_CHECK", False)

    assert device_module._is_live_process(proc) is False
    assert proc.status_calls == 1
