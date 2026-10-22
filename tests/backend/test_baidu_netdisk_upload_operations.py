import json
import os
import sys
import threading

import pytest

from backend.core.baidu_netdisk import upload_operations as uo
from backend.core.baidu_netdisk.ledger import TransferLedger, transfer_key
from backend.core.baidu_netdisk.local_files import sha256_file

REMOTE_DIR = "/CodeYun自动化测试/upload-ops"
SECRET = "SECRET-TOKEN-xyz"


@pytest.fixture
def env(tmp_path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    source = source_dir / "report.bin"
    source.write_bytes(b"upload payload" * 100)
    ledger = TransferLedger(tmp_path / "ledger" / "ledger.json")
    store = uo.UploadOperationStore(tmp_path / "ops", ledger)
    return store, ledger, source


def _resolved(source, **over):
    row = {"status": "resolved", "is_root": False, "id": "778899", "name": source.name,
           "path": f"{REMOTE_DIR}/{source.name}", "is_dir": False, "size": source.stat().st_size,
           "version": 1700000000, "chain": []}
    return {**row, **over}


def _fake_remote(monkeypatch, result):
    calls = []

    def fake(path, expected_id=None, want_dir=None, *args, context=None, pace=None, **kwargs):
        calls.append({"path": path, "want_dir": want_dir, "context": context, "pace": pace})
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(uo, "resolve_remote", fake)
    return calls


def _no_side_effects(monkeypatch):
    """Readback must not hash the source or write the native ledger."""
    def boom(*a, **k):
        raise AssertionError("forbidden side effect during reconcile")

    monkeypatch.setattr(uo, "describe_upload_source", boom)
    monkeypatch.setattr(TransferLedger, "update", boom)
    monkeypatch.setattr(TransferLedger, "begin", boom)


def _submit_native(ledger, record, state="submitted"):
    req = record["request"]
    native, _ = ledger.begin("upload", sha256=req["sha256"], remote_path=req["remote_path"])
    assert native["key"] == record["native_key"]
    return ledger.update(native["key"], state, via="official_client_gui") if state != "planned" else native


# -- intent -------------------------------------------------------------------------

def test_begin_persists_identity_and_native_key_before_gui(env):
    store, ledger, source = env
    record, created = store.begin("op-1", source, REMOTE_DIR)
    assert created is True
    req = record["request"]
    assert req == {"local_path": os.fspath(source.resolve()), "name": "report.bin", "size": source.stat().st_size,
                   "sha256": sha256_file(source), "remote_dir": REMOTE_DIR,
                   "remote_path": f"{REMOTE_DIR}/report.bin"}
    assert record["native_key"] == transfer_key("upload", sha256=req["sha256"], remote_path=req["remote_path"])
    assert record["state"] == "intent" and record["ledger_key"] is None
    assert ledger.get(record["native_key"]) is None  # nothing submitted, no ledger write
    assert store.inspect("op-1") == record
    files = [p.name for p in store.root.iterdir() if p.suffix == ".json"]
    assert files == [f"upload-{__import__('hashlib').sha256(b'op-1').hexdigest()[:32]}.json"]


def test_duplicate_same_request_returns_existing(env):
    store, _, source = env
    first, created = store.begin("op-1", source, REMOTE_DIR)
    again, created_again = store.begin("op-1", os.fspath(source), REMOTE_DIR + "/")
    assert created and not created_again
    assert again == first


def test_conflicting_request_rejected_and_record_immutable(env, tmp_path):
    store, _, source = env
    first, _ = store.begin("op-1", source, REMOTE_DIR)
    with pytest.raises(uo.UploadOperationConflict):
        store.begin("op-1", source, "/other")
    other = tmp_path / "src" / "other.bin"
    other.write_bytes(b"x")
    with pytest.raises(uo.UploadOperationConflict):
        store.begin("op-1", other, REMOTE_DIR)
    source.write_bytes(b"changed content")
    with pytest.raises(uo.UploadOperationConflict):
        store.begin("op-1", source, REMOTE_DIR)
    assert store.inspect("op-1") == first


def test_concurrent_begin_creates_exactly_once(env):
    store, _, source = env
    results, errors = [], []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        try:
            results.append(store.begin("op-race", source, REMOTE_DIR))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sum(created for _, created in results) == 1
    assert len({r["fingerprint"] for r, _ in results}) == 1
    assert len({r["created_at"] for r, _ in results}) == 1


# -- source / root / id safety ---------------------------------------------------------

@pytest.mark.parametrize("bad", ["../escape", "a/b", "a\\b", "", "x" * 129, "op id", None])
def test_operation_id_traversal_rejected(env, bad):
    store, _, source = env
    with pytest.raises(ValueError):
        store.begin(bad, source, REMOTE_DIR)
    with pytest.raises(ValueError):
        store.inspect(bad)


def test_dotted_ids_stay_inside_root(env):
    store, _, source = env
    for op_id in ("..", "...", "a..b"):
        store.begin(op_id, source, REMOTE_DIR)
    files = [p for p in store.root.iterdir() if p.suffix == ".json"]
    assert len(files) == 3 and all(p.parent == store.root and p.name.startswith("upload-") for p in files)


def test_root_must_be_absolute(tmp_path):
    with pytest.raises(ValueError):
        uo.UploadOperationStore("relative/ops", TransferLedger(tmp_path / "l.json"))


@pytest.mark.parametrize("remote", ["relative", "/a/../b", "/a/b:c", "/a\\b"])
def test_bad_remote_dir_rejected(env, remote):
    store, _, source = env
    with pytest.raises(uo.UploadSourceRejected) as info:
        store.begin("op-1", source, remote)
    assert info.value.code == "remote_dir_invalid"
    assert store.inspect("op-1") is None


def test_source_must_be_absolute_regular_file(env, tmp_path):
    store, _, source = env
    cases = {"source_not_absolute": "src/report.bin", "source_missing": tmp_path / "nope.bin",
             "source_not_regular_file": tmp_path / "src"}
    for code, value in cases.items():
        with pytest.raises(uo.UploadSourceRejected) as info:
            store.begin("op-1", value, REMOTE_DIR)
        assert info.value.code == code
        assert os.fspath(tmp_path) not in str(info.value)
    assert store.inspect("op-1") is None


def test_symlinked_source_rejected(env, tmp_path):
    store, _, source = env
    link = tmp_path / "src" / "link.bin"
    try:
        os.symlink(source, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted")
    with pytest.raises(uo.UploadSourceRejected) as info:
        store.begin("op-1", link, REMOTE_DIR)
    assert info.value.code in {"source_link_or_reparse_point", "source_not_regular_file"}
    assert store.inspect("op-1") is None


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows only")
def test_junction_in_source_chain_rejected(env, tmp_path):
    import _winapi

    store, _, source = env
    junction = tmp_path / "jn"
    _winapi.CreateJunction(os.fspath(source.parent), os.fspath(junction))
    try:
        with pytest.raises(uo.UploadSourceRejected) as info:
            store.begin("op-1", junction / source.name, REMOTE_DIR)
        assert info.value.code == "source_link_or_reparse_point"
        with pytest.raises(ValueError):
            uo.UploadOperationStore(junction / "ops", TransferLedger(tmp_path / "l.json"))
    finally:
        os.rmdir(junction)  # removes the junction only, not its target
    assert source.exists()


# -- record -------------------------------------------------------------------------------

def test_record_binds_native_key_and_filters_private_fields(env, tmp_path):
    store, _, source = env
    record, _ = store.begin("op-1", source, REMOTE_DIR)
    key = record["native_key"]
    out = store.record("op-1", {"status": "state_unknown", "next": "observe_remote_do_not_resubmit", "key": key,
                                "error": f"click failed at {tmp_path} {SECRET}",
                                "details": {"local_path": os.fspath(source)},
                                "reason": f"C:\\Users\\{SECRET}\\x", "remote_present": "yes",
                                "resource_id": 123, "remote_size": 5})
    receipt = out["receipts"][-1]
    assert out["ledger_key"] == key and out["state"] == "recorded"
    assert {k for k in receipt if k != "at"} == {"status", "next", "key", "remote_size"}
    stored = (store.root / next(p.name for p in store.root.iterdir() if p.suffix == ".json")).read_text("utf-8")
    assert SECRET not in stored and os.fspath(tmp_path) not in json.dumps(receipt)


def test_record_rejects_other_keys_and_private_status(env, tmp_path):
    store, _, source = env
    store.begin("op-1", source, REMOTE_DIR)
    with pytest.raises(uo.UploadOperationConflict):
        store.record("op-1", {"status": "submitted"}, ledger_key="0" * 24)
    with pytest.raises(uo.UploadOperationConflict):
        store.record("op-1", {"status": "submitted", "key": transfer_key("upload", sha256="A" * 64, remote_path="/x")})
    with pytest.raises(ValueError) as info:
        store.record("op-1", {"status": f"failed: {tmp_path} {SECRET}"})
    assert SECRET not in str(info.value) and os.fspath(tmp_path) not in str(info.value)
    with pytest.raises(KeyError):
        store.record("op-missing", {"status": "submitted"})
    assert store.inspect("op-1")["receipts"] == []


def test_concurrent_record_keeps_every_receipt(env):
    store, _, source = env
    record, _ = store.begin("op-1", source, REMOTE_DIR)
    barrier = threading.Barrier(8)
    errors = []

    def worker(i):
        barrier.wait()
        try:
            store.record("op-1", {"status": f"observed_{i}", "key": record["native_key"]})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    final = store.inspect("op-1")
    assert sorted(r["status"] for r in final["receipts"]) == sorted(f"observed_{i}" for i in range(8))
    assert final["ledger_key"] == record["native_key"]
    assert len([h for h in final["history"] if h["event"] == "record"]) == 8


# -- reconcile ------------------------------------------------------------------------------

def test_reconcile_unknown_operation_blocks(env, monkeypatch):
    store, _, _ = env
    calls = _fake_remote(monkeypatch, AssertionError("must not resolve"))
    out = store.reconcile("op-none")
    assert out["status"] == "unknown_operation" and out["next"] == uo.DO_NOT_RESUBMIT
    assert out["remote_present"] is False and calls == []


@pytest.mark.parametrize("remote", [
    {"status": "missing", "at": "/x"},
    {"status": "blocked", "reason": "no_dedicated_tab"},
    {"status": "unstable"},
    {"status": "ambiguous"},
    {"status": "invalid", "reason": "invalid_arguments"},
    None,
    RuntimeError(f"tab crashed {SECRET}"),
])
def test_submitted_but_missing_or_uncertain_stays_blocking(env, monkeypatch, remote):
    store, ledger, source = env
    record, _ = store.begin("op-1", source, REMOTE_DIR)
    native = _submit_native(ledger, record)
    _fake_remote(monkeypatch, remote)
    _no_side_effects(monkeypatch)
    out = store.reconcile("op-1")
    assert out["status"] == "submitted" and out["next"] == uo.DO_NOT_RESUBMIT
    assert out["remote_present"] is False and out["content_verified"] is False and out["terminal"] is False
    assert SECRET not in json.dumps(out)
    assert ledger.get(native["key"])["state"] == "submitted"  # never failed/abandoned
    assert store.inspect("op-1")["final"] is None
    # absence does not authorise a new intent either
    monkeypatch.undo()
    assert store.begin("op-1", source, REMOTE_DIR)[1] is False


def test_closed_chooser_without_ledger_or_remote_needs_review(env, monkeypatch):
    store, ledger, source = env
    record, _ = store.begin("op-1", source, REMOTE_DIR)
    _fake_remote(monkeypatch, {"status": "missing"})
    _no_side_effects(monkeypatch)
    out = store.reconcile("op-1")
    assert out["status"] == "needs_review" and out["next"] == uo.DO_NOT_RESUBMIT
    assert out["ledger_state"] is None and ledger.get(record["native_key"]) is None
    store_receipt = {"status": "state_unknown", "key": record["native_key"]}
    monkeypatch.undo()
    store.record("op-1", store_receipt)
    _fake_remote(monkeypatch, {"status": "missing"})
    assert store.reconcile("op-1")["status"] == "submitted"


def test_exact_path_size_id_is_remote_present_and_terminal(env, monkeypatch):
    store, ledger, source = env
    record, _ = store.begin("op-1", source, REMOTE_DIR)
    _submit_native(ledger, record)
    ctx, pace = object(), object()
    calls = _fake_remote(monkeypatch, _resolved(source))
    _no_side_effects(monkeypatch)
    out = store.reconcile("op-1", context=ctx, pace=pace)
    assert calls == [{"path": f"{REMOTE_DIR}/report.bin", "want_dir": False, "context": ctx, "pace": pace}]
    assert out["status"] == "remote_present" and out["remote_present"] is True
    assert out["content_verified"] is False and out["terminal"] is True
    assert out["resource_id"] == "778899" and out["remote_size"] == source.stat().st_size
    assert out["verification_level"] == "remote_path_size_id"
    stored = store.inspect("op-1")
    assert stored["state"] == "remote_present" and stored["final"]["resource_id"] == "778899"
    # terminal receipt survives source removal; no further remote or source access
    source.unlink()
    calls.clear()
    again = store.reconcile("op-1")
    assert again["status"] == "remote_present" and again["terminal"] is True and calls == []
    assert again["at"] == out["at"]
    assert ledger.get(record["native_key"])["state"] == "submitted"


@pytest.mark.parametrize("over,reason", [
    ({"size": 1}, "remote_size_mismatch"),
    ({"size": None}, "remote_size_mismatch"),
    ({"id": None}, "remote_resource_id_missing"),
    ({"id": ""}, "remote_resource_id_missing"),
    ({"is_dir": True}, "remote_not_a_file"),
    ({"path": f"{REMOTE_DIR}/report(1).bin"}, "remote_path_mismatch"),
    ({"name": "REPORT.bin"}, "remote_path_mismatch"),
    ({"status": "type_mismatch"}, "remote_not_a_file"),
])
def test_inexact_remote_is_not_present(env, monkeypatch, over, reason):
    store, ledger, source = env
    record, _ = store.begin("op-1", source, REMOTE_DIR)
    _submit_native(ledger, record)
    _fake_remote(monkeypatch, _resolved(source, **over))
    _no_side_effects(monkeypatch)
    out = store.reconcile("op-1")
    assert out["status"] == "needs_review" and out["reason"] == reason
    assert out["remote_present"] is False and out["next"] == uo.DO_NOT_RESUBMIT
    assert store.inspect("op-1")["final"] is None


def test_remote_present_without_submission_is_not_terminal(env, monkeypatch):
    store, ledger, source = env
    record, _ = store.begin("op-1", source, REMOTE_DIR)
    _submit_native(ledger, record, state="failed")  # provider: nothing sent (e.g. remote_name_exists)
    _fake_remote(monkeypatch, _resolved(source))
    _no_side_effects(monkeypatch)
    out = store.reconcile("op-1")
    assert out["status"] == "needs_review" and out["reason"] == "remote_present_without_submission"
    assert out["remote_present"] is True and out["content_verified"] is False and out["terminal"] is False
    assert store.inspect("op-1")["final"] is None


def test_concurrent_reconcile_settles_once(env, monkeypatch):
    store, ledger, source = env
    record, _ = store.begin("op-1", source, REMOTE_DIR)
    _submit_native(ledger, record)
    _fake_remote(monkeypatch, _resolved(source))
    barrier = threading.Barrier(6)
    outs = []

    def worker():
        barrier.wait()
        outs.append(store.reconcile("op-1"))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({o["at"] for o in outs}) == 1
    assert [h["event"] for h in store.inspect("op-1")["history"]].count("remote_present") == 1


def test_module_has_no_gui_or_submission_path():
    import ast

    tree = ast.parse(open(uo.__file__, encoding="utf-8").read())
    imported = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not modules & {"client", "bridge", "automation", "input_guard", "file_selection"}
    assert "NetdiskClient" not in imported
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not calls & {"update", "input", "click", "upload_file", "activate"}
