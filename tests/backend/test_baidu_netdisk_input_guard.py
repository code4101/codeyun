"""GuardedDriver call-order contract over a recording inner driver (no GUI)."""

import pytest

from backend.core.baidu_netdisk.client import StepFailed
from backend.core.baidu_netdisk.input_guard import PARK_POINTER, GuardedDriver
from backend.core.baidu_netdisk.ownership import NativeAccountHandle

FRAME = {"token": "f1", "window_id": "w", "input_token": "tok", "width": 10, "height": 10}


class Leases:
    def __init__(self, log):
        self.log = log

    def check(self, resource, token):
        self.log.append("lease")
        return {"token": token}


class Inner:
    def __init__(self, log, now, cost=0.0, error=None):
        self.log, self.now, self.cost, self.error = log, now, cost, error

    def input(self, frame, **action):
        self.log.append(("input", frame["input_token"], dict(action)))
        self.now[0] += self.cost
        if self.error:
            raise self.error
        return {"ok": True}

    def close_window(self, window_id, expected_title):
        self.log.append(("close", window_id, expected_title))
        self.now[0] += self.cost
        if self.error:
            raise self.error
        return True

    def window_geometry(self, window_id):
        self.log.append(("geometry", window_id))
        return {"window_id": window_id, "width": 10, "height": 10}


def _guard(gap=2.0, cost=0.0, error=None, gate=None):
    log, now = [], [0.0]

    def sleep(seconds):
        log.append(("sleep", seconds))
        now[0] += seconds

    account = NativeAccountHandle(Leases(log), "t", external=True, action_gap=gap, clock=lambda: now[0],
                                  sleep=sleep)
    guard = GuardedDriver(Inner(log, now, cost, error), account=account,
                          gate=gate or (lambda frame, action: log.append(("gate", action.get("action")))))
    return guard, log, now


def test_order_is_lease_gate_pace_gate_then_one_delegate():
    guard, log, _ = _guard()
    assert guard.input(FRAME, action="click", x=.5, y=.5) == {"ok": True}
    assert log == ["lease", ("gate", "click"), "lease", ("sleep", 2.0), "lease", ("gate", "click"),
                   ("input", "tok", {"action": "click", "x": .5, "y": .5})]
    assert guard.generation == 1


def test_window_geometry_is_lease_checked_read_only_forwarding():
    guard, log, _ = _guard()
    assert guard.window_geometry("w") == {"window_id": "w", "width": 10, "height": 10}
    assert log == ["lease", ("geometry", "w")] and guard.generation == 0


def test_park_pointer_is_one_guarded_move():
    guard, log, _ = _guard()
    guard.park_pointer(FRAME)
    assert [e for e in log if e[0] == "input"] == [("input", "tok", PARK_POINTER)]
    assert log.count(("gate", "move")) == 2


def test_failing_delegate_not_retried_and_completion_still_anchors_gap():
    guard, log, now = _guard(cost=1.5, error=RuntimeError("x"))
    with pytest.raises(RuntimeError):
        guard.input(FRAME, action="key", key="enter")
    assert sum(e[0] == "input" for e in log if isinstance(e, tuple)) == 1 and guard.generation == 1
    assert now[0] == 3.5
    guard.inner.error = None
    log.clear()
    guard.input(FRAME, action="key", key="enter")
    assert ("sleep", 2.0) in log  # gap counted from the failed input's completion, not its permission


def test_gate_refusal_after_pacing_blocks_delegate():
    calls = []

    def gate(frame, action):
        calls.append(1)
        if len(calls) == 2:
            raise StepFailed("action_expired")

    guard, log, _ = _guard(gate=gate)
    with pytest.raises(StepFailed, match="action_expired"):
        guard.input(FRAME, action="click", x=.1, y=.1)
    assert not any(isinstance(e, tuple) and e[0] == "input" for e in log) and guard.generation == 0


def test_close_is_one_gated_paced_delegate_with_exact_title():
    guard, log, _ = _guard()
    assert guard.close_window("w", "T", frame=FRAME) is True
    gate = ("gate", "close")
    assert log == ["lease", gate, "lease", ("sleep", 2.0), "lease", gate, ("close", "w", "T")]
    assert guard.generation == 1


def test_failing_close_not_retried_and_completion_anchors_gap():
    guard, log, now = _guard(cost=1.5, error=OSError("x"))
    with pytest.raises(OSError):
        guard.close_window("w", "T", frame=FRAME)
    assert [e for e in log if e[0] == "close"] == [("close", "w", "T")] and guard.generation == 1
    assert now[0] == 3.5


def test_close_and_scheme_are_refused():
    guard, log, _ = _guard()
    with pytest.raises(StepFailed, match="close_window_not_declared"):
        guard.close_window("w", "t")  # legacy call without the verified frame
    with pytest.raises(StepFailed, match="close_window_not_declared"):
        guard.close_window("other", "t", frame=FRAME)  # frame of another window
    assert log == [] and guard.generation == 0
    with pytest.raises(StepFailed, match="open_scheme_not_supported"):
        guard.open_scheme("baiduyunguanjia://x")
