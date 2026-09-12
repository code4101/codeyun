import pytest
from pydantic import ValidationError

from backend.core.fanxiu.remote.runtime_observation import RuntimeSnapshot, plan_runtime_probe
from backend.core.fanxiu.remote.sessions import RemoteError, RemoteSessions
from backend.core.fanxiu.remote.observation import plan_remote_observation


def snapshot(**fields):
    return {"schema_version": 1, "query": "process", "source": "adb_proc_mem",
            "captured_at": 100.0, "completeness": "complete",
            "process_identity": {"package_name": "game", "pid": 1, "start_ticks": 50, "device_serial": "emu"},
            "facts": {"elf_class": 64}, **fields}


def test_runtime_missing_partial_and_stale_are_not_empty_success():
    assert plan_runtime_probe(None)["action"]["kind"] == "collect_runtime"
    assert plan_runtime_probe(snapshot(), now=105)["status"] == "completed"
    assert plan_runtime_probe(snapshot(), now=140)["reason"] == "runtime_stale"
    assert plan_runtime_probe(snapshot(completeness="partial"), now=105)["reason"] == "runtime_incomplete"
    with pytest.raises(ValidationError):
        RuntimeSnapshot.model_validate(snapshot(process_identity=None))


def test_runtime_is_never_accepted_without_the_matching_requested_query():
    sessions = RemoteSessions()
    sid = sessions.create(1, "emu", "runtime_probe")["session_id"]
    body = {"request_id": "1", "frame_id": "1", "runtime_snapshot": snapshot()}
    with pytest.raises(RemoteError) as error:
        sessions.step(1, sid, body, b"", plan_remote_observation)
    assert error.value.status_code == 409
    first = sessions.step(1, sid, {"request_id": "1", "frame_id": "1"}, b"", plan_remote_observation)
    with pytest.raises(RemoteError):
        sessions.step(1, sid, {"request_id": "2", "frame_id": "2",
                              "previous_action_id": first["action"]["action_id"],
                              "previous_action_status": "executed"}, b"", plan_remote_observation)
