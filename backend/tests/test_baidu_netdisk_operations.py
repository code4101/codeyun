"""Contract tests for operation intents: real temp files, a real TransferLedger and the public
filesystem-only resume methods.  The ledger is put into the state the client records right
before its submit click (public ``begin``/``update`` + ``write_manifest``); the official
client's finished download is a file appearing in its download dir.  No UI is simulated."""

import errno
import hashlib
import json
import os
import threading

import pytest

from backend.core.baidu_netdisk import TransferLedger, snapshot_dir, transfer_key, write_manifest
from backend.core.baidu_netdisk.client import NetdiskClient
from backend.core.baidu_netdisk.operations import OperationConflict, OperationStore, native_download_keys

DATA = b"payload-bytes"
SHA = hashlib.sha256(DATA).hexdigest().upper()


@pytest.fixture
def env(tmp_path):
    dl, local = tmp_path / "client-dl", tmp_path / "business"
    dl.mkdir(), local.mkdir()
    ledger = TransferLedger(tmp_path / "ledger" / "transfers.json")
    return {"dl": dl, "local": local, "ledger": ledger, "store": OperationStore(tmp_path / "ops", ledger, settle=0)}


def _req(env, **over):
    req = {"kind": "download", "remote_path": "/资料/a.zip", "local_dir": str(env["local"]),
           "expected_size": len(DATA), "expected_sha256": SHA.lower(), "resource_id": "r1", "resource_version": 2}
    return {**req, **over}


def _submitted(env, op="download", **inputs):
    """What download_file / download_dir persist before their single submit click."""
    record, created = env["ledger"].begin(op, **inputs)
    assert created
    manifest = env["ledger"].path.parent / f"{op}-{record['key']}.before.json"
    write_manifest(manifest, env["dl"], snapshot_dir(env["dl"]))
    return record["key"], manifest


def _submit_file(env, req):
    inputs = {"remote_path": req["remote_path"], "local_dir": str(env["local"].resolve()),
              "expected_size": req["expected_size"], "expected_sha256": req["expected_sha256"].upper()}
    key, manifest = _submitted(env, **inputs)
    env["ledger"].update(key, "submitted", via="official_client_gui", manifest=str(manifest), name="a.zip",
                         expected_size=req["expected_size"], expected_sha256=inputs["expected_sha256"],
                         local_dir=inputs["local_dir"])
    return key


def test_begin_is_stable_and_conflicting_request_is_rejected(env):
    store = env["store"]
    rec, created = store.begin("job:1", _req(env))
    again, created_again = store.begin("job:1", _req(env, expected_sha256=SHA))  # same after normalisation
    assert created and not created_again and again["fingerprint"] == rec["fingerprint"]
    assert rec["request"]["expected_sha256"] == SHA and rec["ledger_key"] is None
    with pytest.raises(OperationConflict):
        store.begin("job:1", _req(env, expected_size=1))
    files = [p.name for p in store.root.glob("op-*.json")]
    assert files == [f"op-{hashlib.sha256(b'job:1').hexdigest()[:32]}.json"]
    assert store.inspect("job:1")["history"][0]["event"] == "begin" and store.inspect("job:2") is None
    with pytest.raises(ValueError):
        store.begin("../escape", _req(env))
    with pytest.raises(ValueError):
        store.begin("job:3", _req(env), ledger_keys=["not-a-candidate"])
    assert not env["ledger"].path.exists()  # intent never touches the provider ledger


def test_native_keys_match_provider_exact_legacy_and_hashless(env):
    req = _req(env)
    identity = {"remote_path": "/资料/a.zip", "local_dir": str(env["local"].resolve())}
    keys = native_download_keys(req)
    assert [k["variant"] for k in keys] == ["exact", "legacy", "hashless"]
    assert keys[0]["key"] == transfer_key("download", **identity, expected_size=len(DATA), expected_sha256=SHA)
    assert keys[1]["key"] == transfer_key("download", **identity)
    assert keys[2]["key"] == transfer_key("download", **identity, expected_size=len(DATA), expected_sha256=None)
    assert [k["variant"] for k in native_download_keys(_req(env, expected_sha256=None))] == ["exact", "legacy"]
    folder = native_download_keys({"kind": "download_dir", "remote_path": "/资料/d", "local_dir": str(env["local"])})
    assert folder == [{"variant": "exact", "key": transfer_key("download_dir", remote_path="/资料/d",
                                                               local_dir=str(env["local"].resolve()))}]
    for bad in ({"expected_sha256": hashlib.md5(DATA).hexdigest()}, {"expected_size": True},
                {"expected_size": "13"}, {"local_dir": "relative/dir"}, {"remote_path": "/a/../b"}):
        with pytest.raises(ValueError):
            native_download_keys(_req(env, **bad))


def test_without_ledger_or_when_planned_nothing_is_retried(env):
    store = env["store"]
    store.begin("op", _req(env))
    assert store.reconcile("op")["status"] == "needs_review" and not store.reconcile("op")["recoverable"]
    env["ledger"].begin("download", remote_path="/资料/a.zip", local_dir=str(env["local"].resolve()),
                        expected_size=len(DATA), expected_sha256=SHA)
    (env["dl"] / "a.zip").write_bytes(DATA)
    before = json.loads(env["ledger"].path.read_text(encoding="utf-8"))
    result = store.recover_download("op")
    assert result["status"] == "planned" and not result["recovered"]
    assert json.loads(env["ledger"].path.read_text(encoding="utf-8")) == before
    assert (env["dl"] / "a.zip").exists() and not list(env["local"].iterdir())
    assert store.reconcile("missing")["status"] == "unknown_operation"


def test_ready_download_recovers_once_then_only_verifies(env):
    store = env["store"]
    store.begin("op", _req(env))
    key = _submit_file(env, _req(env))
    (env["dl"] / "a.zip").write_bytes(DATA)
    ready = store.reconcile("op")
    assert ready["status"] == "ready" and ready["recoverable"] and ready["ledger_key"] == key
    assert ready["evidence"]["local_sha256"] == SHA and ready["evidence"]["hash_source"] == "local_read"
    assert (env["dl"] / "a.zip").exists() and not list(env["local"].iterdir())  # reconcile moved nothing
    done = store.recover_download("op")
    assert done["recovered"] and done["status"] == "verified"
    assert done["resume_status"] == "downloaded"  # GUI guard never tripped: resume is filesystem only
    assert done["evidence"]["verification_level"] == "content_verified"
    assert (env["local"] / "a.zip").read_bytes() == DATA and not (env["dl"] / "a.zip").exists()
    op = store.inspect("op")
    assert op["state"] == "verified" and op["final"]["verification_level"] == "content_verified"
    assert op["ledger_key"] == key and op["request"]["resource_version"] == 2
    ledger_before = env["ledger"].get(key)
    again = store.recover_download("op")
    assert again["status"] == "verified" and not again["recovered"]
    assert env["ledger"].get(key) == ledger_before and len(store.inspect("op")["history"]) == len(op["history"])


def test_collision_name_is_candidate_and_two_candidates_are_ambiguous(env):
    (env["dl"] / "a.zip").write_bytes(b"old download")
    store = env["store"]
    store.begin("op", _req(env))
    _submit_file(env, _req(env))
    (env["dl"] / "a(1).zip").write_bytes(DATA)
    assert store.reconcile("op")["evidence"]["candidate"] == "a(1).zip"
    (env["dl"] / "a(2).zip").write_bytes(DATA)
    result = store.recover_download("op")
    assert result["status"] == "ambiguous" and not result["recovered"] and not list(env["local"].iterdir())


@pytest.mark.parametrize("case", ["hash", "partial", "target", "size"])
def test_mismatch_partial_and_conflict_are_never_ready(env, case):
    store = env["store"]
    store.begin("op", _req(env))
    _submit_file(env, _req(env))
    if case == "hash":
        (env["dl"] / "a.zip").write_bytes(b"x" * len(DATA))
    elif case == "partial":
        (env["dl"] / "a.zip.baiduyun.p.downloading").write_bytes(b"half")
    elif case == "size":
        (env["dl"] / "a.zip").write_bytes(DATA + b"!")
    else:
        (env["dl"] / "a.zip").write_bytes(DATA)
        (env["local"] / "a.zip").write_bytes(b"someone else's file")
    expected = {"hash": "hash_mismatch", "partial": "in_progress", "target": "target_exists", "size": "not_found"}[case]
    result = store.recover_download("op")
    assert result["status"] == expected and not result["recoverable"] and not result["recovered"]
    assert not (env["local"] / "a.zip").exists() or case == "target"


def test_moved_without_ledger_update_and_missing_manifest_need_review(env):
    store = env["store"]
    store.begin("op", _req(env))
    key = _submit_file(env, _req(env))
    (env["local"] / "a.zip").write_bytes(DATA)  # an earlier collect moved it, then the process died
    assert store.reconcile("op")["evidence"]["reason"] == "target_present_ledger_unverified"
    (env["local"] / "a.zip").unlink()
    (env["ledger"].path.parent / f"download-{key}.before.json").unlink()
    result = store.reconcile("op")
    assert result["status"] == "needs_review" and result["evidence"]["reason"] == "manifest_unreadable"


def test_legacy_record_is_found_and_record_binds_only_candidate_keys(env):
    store = env["store"]
    store.begin("op", _req(env))
    inputs = {"remote_path": "/资料/a.zip", "local_dir": str(env["local"].resolve())}
    key, manifest = _submitted(env, **inputs)
    env["ledger"].update(key, "submitted", manifest=str(manifest), name="a.zip", expected_size=len(DATA),
                         local_dir=inputs["local_dir"])
    with pytest.raises(OperationConflict):
        store.record("op", {"status": "downloaded", "key": "f" * 24})
    rec = store.record("op", {"status": "submitted", "key": key, "error": "secret token=abc", "facts": {"x": 1}})
    assert rec["ledger_key"] == key and "error" not in rec["receipts"][0] and "facts" not in rec["receipts"][0]
    (env["dl"] / "a.zip").write_bytes(DATA)
    result = store.reconcile("op")
    assert result["ledger_variant"] == "legacy" and result["status"] == "ready"
    assert store.recover_download("op")["evidence"]["verification_level"] == "content_verified"


def test_verified_record_rechecks_target_file(env):
    store = env["store"]
    store.begin("op", _req(env))
    key = _submit_file(env, _req(env))
    (env["dl"] / "a.zip").write_bytes(DATA)
    assert store.recover_download("op")["recovered"]
    (env["local"] / "a.zip").write_bytes(b"y" * len(DATA))
    result = store.reconcile("op")
    assert result["ledger_state"] == "verified" and result["status"] == "artifact_changed"
    assert env["ledger"].get(key)["state"] == "verified"


def _dir_request(env, manifest=True):
    req = {"kind": "download_dir", "remote_path": "/资料/set", "local_dir": str(env["local"])}
    if manifest:
        req["expected_manifest"] = {"files": [
            {"rel": "a.bin", "size": 5, "sha256": hashlib.sha256(b"alpha").hexdigest()},
            {"rel": "sub/b.txt", "size": 5, "sha256": hashlib.sha256(b"beta!").hexdigest()}],
            "resource_id": "set", "resource_version": 7}
    return req


def _submit_dir(env):
    local = str(env["local"].resolve())
    key, manifest = _submitted(env, "download_dir", remote_path="/资料/set", local_dir=local)
    env["ledger"].update(key, "submitted", via="official_client_gui", manifest=str(manifest), name="set",
                         expected_files={"a.bin": "5B", "sub/b.txt": "5B"}, expected_dirs=["sub"], local_dir=local)
    (env["dl"] / "set" / "sub").mkdir(parents=True)
    (env["dl"] / "set" / "a.bin").write_bytes(b"alpha")
    (env["dl"] / "set" / "sub" / "b.txt").write_bytes(b"beta!")
    return key


def test_folder_without_trusted_manifest_needs_manifest(env):
    store = env["store"]
    store.begin("op", _dir_request(env, manifest=False))
    _submit_dir(env)
    result = store.recover_download("op")
    assert result["status"] == "needs_manifest" and not result["recovered"]
    assert (env["dl"] / "set" / "a.bin").exists()


def test_folder_recovers_and_is_graded_by_verify_local(env):
    store = env["store"]
    store.begin("op", _dir_request(env))
    _submit_dir(env)
    assert store.reconcile("op")["status"] == "ready"
    result = store.recover_download("op")
    assert result["recovered"] and result["evidence"]["verification_level"] == "content_verified"
    assert (env["local"] / "set" / "sub" / "b.txt").read_bytes() == b"beta!"
    assert store.inspect("op")["final"]["manifest_sha256"]
    assert store.recover_download("op")["recovered"] is False


def test_folder_with_extra_file_is_mismatch(env):
    store = env["store"]
    store.begin("op", _dir_request(env))
    _submit_dir(env)
    (env["dl"] / "set" / "extra.txt").write_bytes(b"?")
    result = store.recover_download("op")
    assert result["status"] == "mismatch" and not (env["local"] / "set").exists()


def test_hashless_record_of_same_size_is_compatible(env):
    store = env["store"]
    store.begin("op", _req(env))
    inputs = {"remote_path": "/资料/a.zip", "local_dir": str(env["local"].resolve()), "expected_size": len(DATA),
              "expected_sha256": None}
    key, manifest = _submitted(env, **inputs)
    env["ledger"].update(key, "submitted", manifest=str(manifest), name="a.zip", expected_size=len(DATA),
                         expected_sha256=None, local_dir=inputs["local_dir"])
    (env["dl"] / "a.zip").write_bytes(b"z" * len(DATA))  # same size, wrong content: our trusted hash decides
    result = store.recover_download("op")
    assert result["ledger_variant"] == "hashless" and result["status"] == "hash_mismatch"
    assert env["ledger"].get(key)["state"] == "submitted" and not (env["local"] / "a.zip").exists()


# -- links and reparse points ---------------------------------------------------------

def _dir_link(link, target):
    """Directory symlink, else (Windows without the symlink privilege) an NTFS junction."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        import _winapi
        _winapi.CreateJunction(str(target), str(link))


def _file_link(link, target):
    try:
        os.symlink(target, link)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314 or exc.errno in {errno.EPERM, errno.EACCES}:
            pytest.skip(f"OS denied symlink creation: {exc.errno}/{getattr(exc, 'winerror', None)}")
        raise


def _assert_refused(store, result):
    assert result["status"] == "unsafe" and result["evidence"]["reason"] == "link_or_reparse_point"
    assert not result["recoverable"] and result["next"] == "manual_review_do_not_resubmit"
    after = store.recover_download("op")
    assert after["status"] == "unsafe" and not after["recovered"]
    assert store.inspect("op")["final"] is None


def test_linked_file_candidate_is_unsafe_not_hashed(env, tmp_path):
    store = env["store"]
    store.begin("op", _req(env))
    _submit_file(env, _req(env))
    outside = tmp_path / "outside.zip"
    outside.write_bytes(DATA)  # right size and hash: only the link makes it unsafe
    _file_link(env["dl"] / "a.zip", outside)
    _assert_refused(store, store.reconcile("op"))
    assert outside.exists() and not list(env["local"].iterdir())


def test_reparse_entry_named_like_candidate_is_unsafe(env, tmp_path):
    store = env["store"]
    store.begin("op", _req(env))
    _submit_file(env, _req(env))
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "inner.bin").write_bytes(DATA)
    _dir_link(env["dl"] / "a(1).zip", tmp_path / "elsewhere")
    (env["dl"] / "a.zip").write_bytes(DATA)
    result = store.reconcile("op")
    _assert_refused(store, result)
    assert result["evidence"]["paths"] == ["a(1).zip"] and (env["dl"] / "a.zip").exists()


@pytest.mark.parametrize("where", ["target", "local_dir"])
def test_linked_destination_is_unsafe_before_recovery(env, tmp_path, where):
    store = env["store"]
    store.begin("op", _req(env))
    _submit_file(env, _req(env))
    (env["dl"] / "a.zip").write_bytes(DATA)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    if where == "target":
        _dir_link(env["local"] / "a.zip", elsewhere)
    else:  # local_dir swapped for a junction after begin: the chain is re-checked with lstat
        env["local"].rename(tmp_path / "business-old")
        _dir_link(env["local"], elsewhere)
    _assert_refused(store, store.reconcile("op"))
    assert (env["dl"] / "a.zip").exists() and not list(elsewhere.iterdir())


@pytest.mark.parametrize("where", ["top", "inner", "root"])
def test_linked_folder_candidate_or_root_is_unsafe(env, tmp_path, where):
    store = env["store"]
    store.begin("op", _dir_request(env))
    _submit_dir(env)
    if where == "top":  # the whole new folder is a junction to an outside copy with the right content
        (env["dl"] / "set").rename(tmp_path / "outside-set")
        _dir_link(env["dl"] / "set", tmp_path / "outside-set")
    elif where == "inner":
        (env["dl"] / "set" / "sub").rename(tmp_path / "outside-sub")
        _dir_link(env["dl"] / "set" / "sub", tmp_path / "outside-sub")
    else:  # the client download root itself became a junction after the before-snapshot
        env["dl"].rename(tmp_path / "real-dl")
        _dir_link(env["dl"], tmp_path / "real-dl")
    _assert_refused(store, store.reconcile("op"))
    assert not (env["local"] / "set").exists()


def test_verified_target_replaced_by_link_is_unsafe(env, tmp_path):
    store = env["store"]
    store.begin("op", _dir_request(env))
    _submit_dir(env)
    assert store.recover_download("op")["recovered"]
    (env["local"] / "set").rename(tmp_path / "moved-set")
    _dir_link(env["local"] / "set", tmp_path / "moved-set")
    result = store.reconcile("op")
    assert result["ledger_state"] == "verified" and result["status"] == "unsafe"


# -- concurrent record / recover --------------------------------------------------------

@pytest.mark.parametrize("second", ["exact", "legacy"])
def test_concurrent_record_is_one_transaction(env, monkeypatch, second):
    """Writer A is held between its read and write while B enters record; B must see A's write."""
    store = env["store"]
    store.begin("op", _req(env))
    keys = {k["variant"]: k["key"] for k in native_download_keys(_req(env))}
    a_inside, b_entering = threading.Event(), threading.Event()
    real_read, real_lock = store._read, store._lock

    def read(path):
        record = real_read(path)
        if threading.current_thread().name == "A" and not a_inside.is_set():
            a_inside.set()
            assert b_entering.wait(10)
        return record

    def lock(path):
        if threading.current_thread().name == "B":
            b_entering.set()
        return real_lock(path)

    monkeypatch.setattr(store, "_read", read)
    monkeypatch.setattr(store, "_lock", lock)
    outcome = {}

    def run(name, key):
        try:
            outcome[name] = store.record("op", {"status": f"by-{name}", "key": key})
        except OperationConflict as exc:
            outcome[name] = exc

    a = threading.Thread(target=run, args=("A", keys["exact"]), name="A")
    a.start()
    assert a_inside.wait(10)
    b = threading.Thread(target=run, args=("B", keys[second]), name="B")
    b.start()
    a.join(10), b.join(10)
    assert not a.is_alive() and not b.is_alive()
    op = store.inspect("op")
    assert op["ledger_key"] == keys["exact"]
    if second == "exact":
        assert [r["status"] for r in op["receipts"]] == ["by-A", "by-B"]
    else:
        assert isinstance(outcome["B"], OperationConflict)
        assert [r["status"] for r in op["receipts"]] == ["by-A"]
    assert [h["event"] for h in op["history"]] == ["begin", "record"] + (["record"] if second == "exact" else [])


@pytest.mark.parametrize("bind", ["exact", "legacy"])
def test_record_landing_during_recovery_respects_reserved_key(env, monkeypatch, bind):
    """Another thread records while recovery is inside the real resume; the key was reserved first."""
    store = env["store"]
    store.begin("op", _req(env))
    key = _submit_file(env, _req(env))
    (env["dl"] / "a.zip").write_bytes(DATA)
    keys = {k["variant"]: k["key"] for k in native_download_keys(_req(env))}
    real_resume = NetdiskClient.resume_download
    outcome = {}

    def late_record():
        try:
            outcome["late"] = store.record("op", {"status": "late", "key": keys[bind]})
        except OperationConflict as exc:
            outcome["late"] = exc

    def resume(client, ledger_key):
        assert store.inspect("op")["ledger_key"] == ledger_key == key  # reserved before any move
        assert (env["dl"] / "a.zip").exists() and not list(env["local"].iterdir())
        out = real_resume(client, ledger_key)
        other = threading.Thread(target=late_record)
        other.start()
        other.join(10)
        assert not other.is_alive()  # record never waits on the recovery lock
        return out

    monkeypatch.setattr(NetdiskClient, "resume_download", resume)
    result = store.recover_download("op")
    op = store.inspect("op")
    assert key == keys["exact"] and op["ledger_key"] == key
    assert result["recovered"] and op["state"] == "verified" and (env["local"] / "a.zip").read_bytes() == DATA
    if bind == "exact":  # same key: the receipt is appended next to the recovery's
        assert [r["status"] for r in op["receipts"]] == ["late", "downloaded"]
        assert [h["event"] for h in op["history"]] == ["begin", "reserve", "record", "recover"]
    else:  # another key after the reservation: rejected, nothing written
        assert isinstance(outcome["late"], OperationConflict)
        assert [r["status"] for r in op["receipts"]] == ["downloaded"]
        assert [h["event"] for h in op["history"]] == ["begin", "reserve", "recover"]
    history = len(op["history"])
    again = store.recover_download("op")
    assert again["status"] == "verified" and not again["recovered"] and len(store.inspect("op")["history"]) == history
    store.record("op", {"status": "after", "key": key})
    with pytest.raises(OperationConflict):
        store.record("op", {"status": "other", "key": keys["legacy"]})
    assert store.inspect("op")["state"] == "verified"


def test_conflicting_binding_before_reservation_blocks_recovery(env, monkeypatch):
    """A different key bound between reconcile and reservation wins; no resume, no move."""
    store = env["store"]
    store.begin("op", _req(env))
    key = _submit_file(env, _req(env))
    (env["dl"] / "a.zip").write_bytes(DATA)
    legacy = {k["variant"]: k["key"] for k in native_download_keys(_req(env))}["legacy"]
    real_reconcile = store.reconcile

    def reconcile(operation_id):
        evidence = real_reconcile(operation_id)
        other = threading.Thread(target=store.record, args=("op", {"status": "bound", "key": legacy}))
        other.start()
        other.join(10)
        assert not other.is_alive()
        return evidence

    def resume(client, ledger_key):
        raise AssertionError("resume must not run after a conflicting binding")

    monkeypatch.setattr(store, "reconcile", reconcile)
    monkeypatch.setattr(NetdiskClient, "resume_download", resume)
    ledger_before = env["ledger"].get(key)
    result = store.recover_download("op")
    assert result["status"] == "needs_review" and result["evidence"]["reason"] == "ledger_key_conflict"
    assert not result["recovered"] and result["ledger_key"] == key
    assert (env["dl"] / "a.zip").read_bytes() == DATA and not list(env["local"].iterdir())
    assert env["ledger"].get(key) == ledger_before
    op = store.inspect("op")
    assert op["ledger_key"] == legacy and op["final"] is None and op["state"] == "recorded"
    assert [r["status"] for r in op["receipts"]] == ["bound"]
    assert [h["event"] for h in op["history"]] == ["begin", "record"]
