"""Dispatch mutex and error-latch contracts independent of game behavior."""
from datetime import datetime, timedelta, timezone
import threading

import pytest

from backend.core.fanxiu.remote.job_worker import JobWorker, parse_stop_at
from backend.core.fanxiu.remote.sessions import RemoteError


def future():
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


def test_single_background_dispatch():
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def operation(worker):
        calls.append(worker)
        entered.set()
        release.wait(2)
        finished.set()
        return {"status": "idle"}
    jobs = JobWorker(operation=operation)
    assert jobs.next("w", future())["status"] == "busy"
    assert entered.wait(1)
    assert jobs.next("w", future())["status"] == "busy"
    assert calls == ["w"]
    release.set()
    assert finished.wait(1)


def test_error_latches_until_explicit_resume():
    jobs = JobWorker(operation=lambda worker: {"status": "error", "message": "failure"})
    jobs._run("w")
    assert jobs.next("w", future())["status"] == "error"
    assert jobs.resume("w")["status"] == "idle"


def test_deadline_and_timezone():
    jobs = JobWorker()
    assert jobs.next("w", "2020-01-01T00:00:00+08:00")["status"] == "completed"
    with pytest.raises(RemoteError):
        parse_stop_at("2026-09-13T12:00:00")
    with pytest.raises(RemoteError):
        parse_stop_at((datetime.now(timezone.utc) + timedelta(days=3)).isoformat())


def test_prepared_flag_requires_explicit_completed_bootstrap():
    jobs = JobWorker(operation=lambda worker: {"status": "busy"})
    jobs._run("w")
    assert "w" not in jobs.prepared
    jobs.operation = lambda worker: {"status": "idle", "bootstrap_completed": True}
    jobs._run("w")
    assert "w" in jobs.prepared
    jobs.resume("w")
    assert "w" in jobs.prepared
