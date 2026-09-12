"""Protocol/lease invariants only; these planners do not simulate a game."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from backend.core.fanxiu.remote.sessions import RemoteError, RemoteSessions


def payload(number=1, **kwargs):
    return {"request_id": f"request-{number}", "frame_id": f"frame-{number}", **kwargs}


def wait_planner(*args, **kwargs):
    return {"status": "running", "observation": {}, "action": {"kind": "wait", "wait_ms": 500}}


def test_owner_isolation_device_lease_and_restart():
    sessions = RemoteSessions()
    attempt = sessions.create(1, "device", "observe")
    with pytest.raises(RemoteError) as error:
        sessions.get(2, attempt["session_id"])
    assert error.value.status_code == 404
    with pytest.raises(RemoteError) as error:
        sessions.create(1, "device", "observe")
    assert error.value.status_code == 409
    with pytest.raises(RemoteError):
        RemoteSessions().get(1, attempt["session_id"])
    sessions.close(1, attempt["session_id"])
    assert sessions.create(1, "device", "observe")["session_id"] != attempt["session_id"]


def test_request_replay_is_same_action_and_payload_changes_are_rejected():
    now = [100.0]
    sessions = RemoteSessions(clock=lambda: now[0])
    sid = sessions.create(1, "device", "observe")["session_id"]
    first = sessions.step(1, sid, payload(), b"frame", wait_planner)
    first["action"]["kind"] = "tampered"
    replay = sessions.step(1, sid, payload(), b"frame", wait_planner)
    assert replay["action"]["kind"] == "wait"
    assert replay["action"]["expires_at"] == 115.0
    now[0] = 120.0
    assert sessions.step(1, sid, payload(), b"frame", wait_planner) == replay
    with pytest.raises(RemoteError) as error:
        sessions.step(1, sid, payload(frame_id="changed"), b"frame", wait_planner)
    assert error.value.status_code == 409


def test_receipt_required_old_requests_and_old_frames_rejected():
    sessions = RemoteSessions()
    sid = sessions.create(1, "device", "navigate", 34)["session_id"]
    first = sessions.step(1, sid, payload(), b"frame", wait_planner)
    with pytest.raises(RemoteError):
        sessions.step(1, sid, payload(2), b"frame", wait_planner)
    receipt = {"previous_action_id": first["action"]["action_id"], "previous_action_status": "executed"}
    with pytest.raises(RemoteError):
        sessions.step(1, sid, payload(2, frame_id="frame-1", **receipt), b"frame", wait_planner)
    sessions.step(1, sid, payload(2, **receipt), b"frame", wait_planner)
    with pytest.raises(RemoteError):
        sessions.step(1, sid, payload(), b"frame", wait_planner)


@pytest.mark.parametrize("status", ["failed", "uncertain"])
def test_uncertain_receipt_stops_without_further_planning(status):
    sessions = RemoteSessions()
    sid = sessions.create(1, "device", "observe")["session_id"]
    first = sessions.step(1, sid, payload(), b"frame", wait_planner)
    def never(*args, **kwargs):
        pytest.fail("failed action must not trigger another plan")
    result = sessions.step(1, sid, payload(2, previous_action_id=first["action"]["action_id"],
                           previous_action_status=status), b"frame", never)
    assert result["status"] == "blocked"
    assert not result.get("action")


def test_expired_session_is_released():
    now = [0.0]
    sessions = RemoteSessions(clock=lambda: now[0])
    sid = sessions.create(1, "device", "observe")["session_id"]
    now[0] = 121.0
    with pytest.raises(RemoteError):
        sessions.get(1, sid)
    assert sessions.create(1, "device", "observe")["session_id"] != sid


def test_parallel_steps_cannot_plan_two_actions():
    sessions = RemoteSessions()
    sid = sessions.create(1, "device", "observe")["session_id"]
    entered, release = Event(), Event()
    def blocking(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return wait_planner()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(sessions.step, 1, sid, payload(), b"frame", blocking)
        assert entered.wait(5)
        try:
            with pytest.raises(RemoteError) as error:
                sessions.step(1, sid, payload(2), b"frame", wait_planner)
            assert error.value.status_code == 409
        finally:
            release.set()
        assert future.result()["action"]["kind"] == "wait"
