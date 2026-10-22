import itertools
import json
import threading

import pytest

from backend.core.baidu_netdisk import directory_operations as dop
from backend.core.baidu_netdisk.directory_operations import DirectoryOperationStore

SECRET = "token=abc123 C:/private/cookie"


class FakeDisk:
    """Generated remote tree: path -> (id, is_dir).  No browser, no account."""

    def __init__(self):
        self.nodes = {"/A": ("a1", True), "/F": ("f1", False)}
        self.ids = (f"n{i}" for i in itertools.count(1))
        self.mutations, self.resolves = 0, []
        self.on_ensure = None  # optional hook(path) run inside the mutator

    def resolve_remote(self, path, expected_id=None, want_dir=None, *, context=None, pace=None):
        self.resolves.append(path)
        if pace is not None:
            pace()
        if path == "/":
            return {"status": "resolved", "path": "/", "is_root": True, "id": None, "is_dir": True, "chain": []}
        chain, cur = [], ""
        for name in path.strip("/").split("/"):
            cur += "/" + name
            if cur not in self.nodes:
                return {"status": "missing", "at": cur, "chain": chain}
            chain.append({"path": cur, "id": self.nodes[cur][0]})
        node_id, is_dir = self.nodes[path]
        if want_dir is not None and is_dir is not want_dir:
            return {"status": "type_mismatch", "at": path, "id": node_id}
        if expected_id is not None and node_id != expected_id:
            return {"status": "id_mismatch", "at": path, "id": node_id}
        return {"status": "resolved", "path": path, "is_root": False, "id": node_id, "is_dir": is_dir,
                "chain": chain, "cleanup": {"listener": "stopped", "tab": "closed", "before_preserved": True,
                                            "record": "C:/tmp/private.json"}}

    def ensure_directory(self, path, anchor_path, expected_anchor_id=None, *, context=None, **kw):
        assert not kw, "provider pacing must stay provider-owned"
        self.mutations += 1
        if self.on_ensure:
            self.on_ensure(path)
        made, cur = [], anchor_path.rstrip("/")
        for name in path[len(cur) + 1:].split("/"):
            cur += "/" + name
            if cur not in self.nodes:
                self.nodes[cur] = (next(self.ids), True)
            made.append({"path": cur, "status": "created", "id": self.nodes[cur][0]})
        return {"status": "created", "path": path, "id": self.nodes[path][0], "steps": made,
                "tab_cleanup": "closed", "secret": SECRET}


@pytest.fixture
def disk(monkeypatch):
    fake = FakeDisk()
    monkeypatch.setattr(dop.listing, "resolve_remote", fake.resolve_remote)
    monkeypatch.setattr(dop.web_directories, "ensure_directory", fake.ensure_directory)
    return fake


@pytest.fixture
def store(tmp_path):
    return DirectoryOperationStore(tmp_path / "ops")


def _no_secret(store, result):
    files = list(store.root.rglob("*.json"))
    text = json.dumps(result) + "".join(f.read_text("utf-8") for f in files)
    assert "abc123" not in text and "private" not in text


def test_creates_once_records_target_and_whitelists_receipts(store, disk):
    paced = []
    res = store.ensure("op-1", "/A/x/y", "/A", "a1", pace=lambda: paced.append(1))
    assert res["status"] == "completed" and res["do_not_replay"] is True
    assert res["target_id"] == disk.nodes["/A/x/y"][0] and disk.mutations == 1
    assert res["provider"]["tab_cleanup"] == "closed" and "secret" not in res["provider"]
    assert res["last_check"]["cleanup"] == {"listener": "stopped", "tab": "closed", "before_preserved": True}
    assert paced  # caller pace used for listing proofs only
    info = store.inspect("op-1")
    assert info["status"] == "found" and info["phase"] == "submitted"
    assert info["intent"] == {"path": "/A/x/y", "anchor_path": "/A", "expected_anchor_id": "a1"}
    assert len(info["fingerprint"]) == 64
    _no_secret(store, res)


def test_duplicate_only_reconciles_and_conflict_refuses(store, disk):
    first = store.ensure("op-1", "/A/x", "/A", "a1")
    again = store.ensure("op-1", "/A/x", "/A", "a1")
    assert again["status"] == "completed" and again["target_id"] == first["target_id"]
    conflict = store.ensure("op-1", "/A/other", "/A", "a1")
    assert conflict["status"] == "conflict" and disk.mutations == 1
    assert store.inspect("op-1")["intent"]["path"] == "/A/x"  # immutable intent


@pytest.mark.parametrize("args,reason", [
    (("/A/x", "/A", None), "expected_anchor_id_required"),
    (("/A/x", "/", "a1"), "root_has_no_id"),
    (("/B/x", "/A", "a1"), "outside_anchor"),
    (("/A", "/A", "a1"), "nothing_to_create"),
    (("/A//x", "/A", "a1"), "invalid_path"),
    (("/A/x/", "/A", "a1"), "invalid_path"),
    (("/A/x", "/A", "bad id!"), "expected_anchor_id_required"),
])
def test_invalid_arguments_store_nothing(store, disk, args, reason):
    assert store.ensure("op-1", *args)["reason"] == reason
    assert store.inspect("op-1")["status"] == "not_found" and disk.mutations == 0 and not disk.resolves


def test_invalid_operation_id(store, disk):
    for bad in ("", "../x", "a/b", "a" * 200, None):
        assert store.ensure(bad, "/A/x", "/A", "a1")["status"] == "invalid"
    assert disk.mutations == 0


def test_concurrent_calls_mutate_exactly_once(tmp_path, disk):
    gate = threading.Barrier(8)
    disk.on_ensure = lambda path: threading.Event().wait(0.05)
    results = []

    def call():
        gate.wait()
        results.append(DirectoryOperationStore(tmp_path / "ops").ensure("op-1", "/A/x/y", "/A", "a1"))

    threads = [threading.Thread(target=call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert disk.mutations == 1 and len(results) == 8
    assert {r["status"] for r in results} <= {"completed", "needs_review"}
    assert all(r["do_not_replay"] for r in results)
    final = DirectoryOperationStore(tmp_path / "ops").reconcile("op-1")
    assert final["status"] == "completed" and final["target_id"] == disk.nodes["/A/x/y"][0]


class Crash(BaseException):
    """Simulated process death: not an Exception, so nothing handles it."""


def test_crash_inside_mutation_with_partial_creation_is_readback_only(tmp_path, disk):
    def partial(path):
        disk.nodes["/A/x"] = ("p1", True)  # first level made, then the process dies
        raise Crash
    disk.on_ensure = partial
    with pytest.raises(Crash):
        DirectoryOperationStore(tmp_path / "ops").ensure("op-1", "/A/x/y", "/A", "a1")
    disk.on_ensure = None
    revived = DirectoryOperationStore(tmp_path / "ops")
    assert revived.inspect("op-1")["phase"] == "submitting"
    for _ in range(3):
        res = revived.ensure("op-1", "/A/x/y", "/A", "a1")
        assert res["status"] == "needs_review" and res["reason"] == "target_missing"
    assert disk.mutations == 1 and "/A/x/y" not in disk.nodes  # never replayed
    disk.nodes["/A/x/y"] = ("m1", True)  # operator finished it by hand
    assert revived.reconcile("op-1")["status"] == "completed"
    assert disk.mutations == 1


def test_crash_before_submit_never_mutates_later(tmp_path, disk, monkeypatch):
    def dying(*a, **kw):
        raise Crash
    monkeypatch.setattr(dop.listing, "resolve_remote", dying)
    with pytest.raises(Crash):
        DirectoryOperationStore(tmp_path / "ops").ensure("op-1", "/A/x", "/A", "a1")
    monkeypatch.setattr(dop.listing, "resolve_remote", disk.resolve_remote)
    revived = DirectoryOperationStore(tmp_path / "ops")
    assert revived.inspect("op-1")["phase"] == "claimed"
    res = revived.ensure("op-1", "/A/x", "/A", "a1")
    assert res["status"] == "needs_review" and res["reason"] == "target_missing"
    assert disk.mutations == 0


@pytest.mark.parametrize("anchor,anchor_id,reason", [
    ("/A", "zz", "anchor_id_mismatch"),
    ("/F", "f1", "anchor_type_mismatch"),
    ("/Gone", "g1", "anchor_missing"),
])
def test_mismatched_anchor_prevents_mutation(store, disk, anchor, anchor_id, reason):
    res = store.ensure("op-1", f"{anchor}/x", anchor, anchor_id)
    assert (res["status"], res["reason"], res["phase"]) == ("refused", reason, "anchor_refused")
    disk.nodes["/A/x"] = ("late", True)
    again = store.ensure("op-1", f"{anchor}/x", anchor, anchor_id)
    assert again["status"] == "refused" and disk.mutations == 0


def test_anchor_replaced_after_creation_is_not_proven(store, disk):
    assert store.ensure("op-1", "/A/x", "/A", "a1")["status"] == "completed"
    disk.nodes["/A"] = ("a2", True)
    res = store.reconcile("op-1")
    assert (res["status"], res["reason"]) == ("needs_review", "anchor_not_proven") and disk.mutations == 1


def test_target_replaced_at_same_path_is_refused(store, disk):
    first = store.ensure("op-1", "/A/x", "/A", "a1")
    disk.nodes["/A/x"] = ("impostor", True)
    res = store.reconcile("op-1")
    assert (res["status"], res["reason"]) == ("needs_review", "target_replaced")
    assert res["target_id"] == first["target_id"] and disk.mutations == 1
    assert disk.resolves[-1] == "/A/x"


def test_provider_exception_blocks_without_private_text(store, disk):
    def boom(path):
        raise RuntimeError(SECRET)
    disk.on_ensure = boom
    res = store.ensure("op-1", "/A/x", "/A", "a1")
    assert (res["status"], res["phase"]) == ("needs_review", "submit_error")
    assert res["provider"] == {"status": "exception", "error": "RuntimeError", "steps": []}
    assert store.ensure("op-1", "/A/x", "/A", "a1")["status"] == "needs_review" and disk.mutations == 1
    _no_secret(store, res)


def test_readback_exception_blocks_without_private_text(store, disk, monkeypatch):
    store.ensure("op-1", "/A/x", "/A", "a1")

    def broken(*a, **kw):
        raise OSError(SECRET)
    monkeypatch.setattr(dop.listing, "resolve_remote", broken)
    res = store.reconcile("op-1")
    assert (res["status"], res["reason"]) == ("needs_review", "readback_exception")
    _no_secret(store, res)


def test_unreadable_record_never_mutates(store, disk):
    store.root.joinpath("operations").mkdir(parents=True)
    store.root.joinpath("operations", "op-1.json").write_text("{bad", encoding="utf-8")
    res = store.ensure("op-1", "/A/x", "/A", "a1")
    assert (res["status"], res["reason"]) == ("needs_review", "record_unreadable") and disk.mutations == 0
