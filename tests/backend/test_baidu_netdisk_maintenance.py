import json
import threading
import time
from types import SimpleNamespace

import pytest

from backend.core.agents.contracts import AgentTaskRequest
from backend.core.baidu_netdisk import maintenance
from backend.core.baidu_netdisk.maintenance import MaintenanceQueue

SECRETS = ("C:\\Users\\alice\\netdisk\\secret.png", "OCR: account alice@example.com balance",
           "Traceback password=hunter2", "iVBORw0KGgoAAAANSUhEUg")


def receipt(n: int = 1, **over) -> dict:
    base = {"case_id": f"{n:032x}", "operation": "download", "code": "window_not_found",
            "error_type": "ProviderError", "at": 1000.0 + n,
            "evidence": {"capture_id": f"{n:016x}", "capture_sha256": "ab" * 32}}
    base.update(over)
    return base


def dirty(n: int) -> dict:
    r = receipt(n, error=SECRETS[2], message=SECRETS[1], path=SECRETS[0], image=SECRETS[3],
                hook_error_type="X", ocr_text=SECRETS[1])
    r["evidence"] = {**r["evidence"], "screenshot_path": SECRETS[0], "observation_id": SECRETS[1],
                     "ocr": SECRETS[1], "evidence_capture_id": "cd" * 8}
    return r


def worktree(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".git").write_text("gitdir: /tmp/fake/.git/worktrees/ws\n", encoding="utf-8")
    return ws


class FakeDispatcher:
    def __init__(self, delay=0.0, fail=False, provider="claude"):
        self.calls, self.delay, self.fail, self.provider = [], delay, fail, provider
        self.lock = threading.Lock()

    def __call__(self, request, **kwargs):
        with self.lock:
            self.calls.append((request, kwargs))
        time.sleep(self.delay)
        if self.fail:
            raise RuntimeError(SECRETS[2])
        return SimpleNamespace(dispatch_id="f" * 32, provider=self.provider)


def all_text(root) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in root.rglob("*.json"))


def test_repeated_failures_dedupe_into_one_case(tmp_path):
    q = MaintenanceQueue(tmp_path / "q", dispatcher=FakeDispatcher())
    first = q.enqueue(receipt(1))
    q.enqueue(receipt(2, at=900.0))
    again = q.enqueue(receipt(2))  # Same source case id: idempotent.
    q.enqueue(receipt(3, code="other_code"))
    assert again["case_id"] == first["case_id"]
    assert again["count"] == 2 and again["first_seen"] == 900.0 and again["last_seen"] == 1001.0
    assert again["ready"] is True and first["ready"] is False
    assert {c["code"]: c["count"] for c in q.list()} == {"window_not_found": 2, "other_code": 1}
    assert q.inspect(first["case_id"])["evidence"][0]["capture_id"] == f"{1:016x}"


def test_private_strings_are_stripped(tmp_path):
    root = tmp_path / "q"
    dispatcher = FakeDispatcher()
    q = MaintenanceQueue(root, dispatcher=dispatcher)
    q.enqueue(dirty(1))
    case = q.enqueue(dirty(2))
    assert case["evidence"][0] == {"capture_id": f"{1:016x}", "capture_sha256": "ab" * 32,
                                   "evidence_capture_id": "cd" * 8}
    q.dispatch(case["case_id"], worktree(tmp_path))
    request, kwargs = dispatcher.calls[0]
    texts = [all_text(root), json.dumps(q.list()), json.dumps(q.inspect(case["case_id"])), repr(request)]
    for text in texts:
        for secret in (*SECRETS, "alice", "hunter2", "screenshot_path"):
            assert secret not in text
    for bad in ({"operation": "C:\\x"}, {"code": "Bad Code"}, {"error_type": "x y"}, {"case_id": "nothex"},
                {"at": "now"}, {"at": float("nan")}, {"at": True}):
        with pytest.raises(ValueError) as err:
            q.enqueue(receipt(9, **bad))
        assert "C:" not in str(err.value)
    with pytest.raises(ValueError):
        q.enqueue("not a dict")


def test_threshold_and_sanitized_cc_request(tmp_path):
    dispatcher = FakeDispatcher()
    q = MaintenanceQueue(tmp_path / "q", threshold=3, dispatcher=dispatcher)
    ws = worktree(tmp_path)
    case = q.enqueue(receipt(1))
    q.enqueue(receipt(2))
    assert q.dispatch(case["case_id"], ws)["dispatch"] == {"state": "below_threshold"}
    assert dispatcher.calls == [] and q.inspect(case["case_id"])["dispatch"] is None
    q.enqueue(receipt(3))
    out = q.dispatch(case["case_id"], ws)
    assert out["dispatch"]["state"] == "dispatched" and out["dispatch"]["provider"] == "claude"
    (request, kwargs), = dispatcher.calls
    assert isinstance(request, AgentTaskRequest)
    assert request.pair == "cc" and request.mode == "repair"
    assert kwargs == {"workspace_dir": str(ws.resolve())}
    assert request.suggested_focus == ("backend/core/baidu_netdisk", "tests/backend")
    body = repr(request)
    assert "capture" not in body and "ab" * 32 not in body and str(tmp_path) not in body
    joined = " ".join(request.constraints).lower()
    for word in ("gui", "browser", "worker", "screenshots", "env", "runtime state", "pinned sdk"):
        assert word in joined


def test_workspace_must_be_separate_worktree(tmp_path):
    dispatcher = FakeDispatcher()
    q = MaintenanceQueue(tmp_path / "q", dispatcher=dispatcher)
    case = q.enqueue(receipt(1))
    q.enqueue(receipt(2))
    plain = tmp_path / "plain"
    (plain / ".git").mkdir(parents=True)  # Main checkout, not a worktree.
    with pytest.raises(ValueError):
        q.dispatch(case["case_id"], plain)
    with pytest.raises(FileNotFoundError):
        q.dispatch(case["case_id"], tmp_path / "missing")
    with pytest.raises(KeyError):
        q.dispatch("0" * 32, worktree(tmp_path))
    assert dispatcher.calls == [] and q.inspect(case["case_id"])["dispatch"] is None


def test_concurrent_dispatch_claims_exactly_once(tmp_path):
    dispatcher = FakeDispatcher(delay=0.2)
    root = tmp_path / "q"
    ws = worktree(tmp_path)
    case = MaintenanceQueue(root).enqueue(receipt(1))
    MaintenanceQueue(root).enqueue(receipt(2))
    results, start = [], threading.Barrier(8)

    def run():
        start.wait()
        results.append(MaintenanceQueue(root, dispatcher=dispatcher, inspector=lambda i: None)
                       .dispatch(case["case_id"], ws))

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(dispatcher.calls) == 1 and len(results) == 8
    assert sum(r["dispatch"]["state"] == "dispatched" for r in results) >= 1
    assert MaintenanceQueue(root, inspector=lambda i: None).inspect(case["case_id"])["dispatch"]["state"] == "dispatched"


def test_crash_after_claim_and_failed_launch_never_redispatch(tmp_path):
    root = tmp_path / "q"
    ws = worktree(tmp_path)
    q = MaintenanceQueue(root, dispatcher=FakeDispatcher())
    case = q.enqueue(receipt(1))
    q.enqueue(receipt(2))

    class Crash(BaseException):
        pass

    def crash(request, **kwargs):
        raise Crash()  # Process dies after the claim was persisted.

    with pytest.raises(Crash):
        MaintenanceQueue(root, dispatcher=crash).dispatch(case["case_id"], ws)
    stored = json.loads((root / "cases" / f"{case['case_id']}.json").read_text(encoding="utf-8"))
    assert stored["dispatch"]["state"] == "claimed" and stored["dispatch"]["dispatch_id"] is None
    after = MaintenanceQueue(root, dispatcher=(d := FakeDispatcher())).dispatch(case["case_id"], ws)
    assert d.calls == [] and after["dispatch"]["state"] == "needs_review"

    # A raising launcher (unknown outcome) settles needs_review without its message, never retried.
    other = q.enqueue(receipt(3, code="other"))
    q.enqueue(receipt(4, code="other"))
    failing = FakeDispatcher(fail=True)
    q2 = MaintenanceQueue(root, dispatcher=failing)
    out = q2.dispatch(other["case_id"], ws)
    assert out["dispatch"]["state"] == "needs_review" and out["dispatch"]["error_type"] == "RuntimeError"
    q2.dispatch(other["case_id"], ws)
    assert len(failing.calls) == 1 and "hunter2" not in all_text(root)

    # A receipt from another provider is not accepted as CC and never triggers a fallback.
    third = q.enqueue(receipt(5, code="third"))
    q.enqueue(receipt(6, code="third"))
    wrong = FakeDispatcher(provider="codex")
    out = MaintenanceQueue(root, dispatcher=wrong).dispatch(third["case_id"], ws)
    assert out["dispatch"]["state"] == "needs_review" and len(wrong.calls) == 1


def test_status_lookup_through_inspector(tmp_path):
    seen = []

    def inspector(dispatch_id):
        seen.append(dispatch_id)
        return SimpleNamespace(status="running", acceptance="pending", error=SECRETS[2])

    root = tmp_path / "q"
    q = MaintenanceQueue(root, dispatcher=FakeDispatcher(), inspector=inspector)
    case = q.enqueue(receipt(1))
    q.enqueue(receipt(2))
    q.dispatch(case["case_id"], worktree(tmp_path))
    info = q.inspect(case["case_id"])["dispatch"]
    assert info["status"] == "running" and info["acceptance"] == "pending" and info["dispatch_id"] == "f" * 32
    assert "hunter2" not in json.dumps(info) and set(seen) == {"f" * 32}

    def broken(dispatch_id):
        raise OSError(SECRETS[0])

    info = MaintenanceQueue(root, inspector=broken).inspect(case["case_id"])["dispatch"]
    assert info["status"] == "status_unavailable" and "alice" not in json.dumps(info)
    assert q.inspect("0" * 32) is None


def test_enqueue_never_dispatches_and_module_is_independent(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("model call")

    monkeypatch.setattr(maintenance, "_default_dispatcher", boom)
    q = MaintenanceQueue(tmp_path / "q", threshold=1)
    for n in range(5):
        q.enqueue(receipt(n))
    assert q.list()[0]["dispatch"] is None
    source = open(maintenance.__file__, encoding="utf-8").read()
    assert "import automation" not in source and "from .automation" not in source


def test_oldest_receipt_replay_stays_idempotent_beyond_twenty_ids(tmp_path):
    q = MaintenanceQueue(tmp_path / "q", dispatcher=FakeDispatcher())
    for n in range(1, 26):
        before = q.enqueue(receipt(n))
    assert before["count"] == 25 and len(before["evidence"]) == 20
    again = q.enqueue(receipt(1))
    assert again == before
    reopened = MaintenanceQueue(tmp_path / "q", dispatcher=FakeDispatcher()).enqueue(receipt(1))
    assert reopened == before
