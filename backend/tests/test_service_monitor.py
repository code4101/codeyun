from __future__ import annotations

import threading

from backend.core.services.monitor import ServiceMonitor


def test_service_monitor_checks_services_without_job_executor():
    calls: list[str] = []
    monitor = ServiceMonitor(lambda: calls.append("checked") or {"status": "ok"}, interval_seconds=60)

    assert monitor.check_now() == {"status": "ok"}
    assert calls == ["checked"]


def test_service_monitor_start_and_shutdown_own_thread():
    called = threading.Event()
    monitor = ServiceMonitor(lambda: called.set() or {"status": "ok"}, interval_seconds=0.01)
    monitor._interval_seconds = 0.01

    monitor.start()
    assert called.wait(1)
    monitor.shutdown()

    assert monitor._thread is None

