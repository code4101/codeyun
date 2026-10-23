"""Download provenance on repeat/reconcile/recover: same path and size is not the same resource.

Pure temporary fixtures: no scene is published and every native/listing entry
is replaced by a function that fails the test, so nothing here can reach a
lease, browser lock, GUI, listing or network.  The "other run" is written only
through the public TransferLedger / local_files APIs.  recover uses the real
filesystem-only resume path (GUI forbidden by OperationStore itself).
"""
import hashlib

import pytest

from backend.core.baidu_netdisk import automation as am
from backend.core.baidu_netdisk import automation_config as ac
from backend.core.baidu_netdisk.local_files import snapshot_dir, write_manifest

BODY = b"abc"
SHA = hashlib.sha256(BODY).hexdigest().upper()
FILE = dict(remote_path="/m/a.bin", expected_size=3, destination="out")


@pytest.fixture
def svc(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("native/browser/listing must not be touched")
    monkeypatch.setattr(am, "NativeAccountSession", boom)
    monkeypatch.setattr(am, "AssetNetdiskClient", boom)
    monkeypatch.setattr(am.listing, "list_directory", boom)
    monkeypatch.setattr(am.listing, "resolve_remote", boom)
    cfg = ac.AutomationConfig(profile="main", scene_ids=["home"], client_download_dir=tmp_path / "dl",
                              local_root=tmp_path / "local", paths=ac.AutomationPaths(tmp_path / "auto"))
    service = am.NetdiskAutomation(cfg)
    service.operations.settle = 0  # recover polls the filesystem only
    return service


def failed_intent(svc, op, **kw):
    """The intent persists, then the run fails before ownership (no scene): nothing was submitted."""
    with pytest.raises(am.AutomationFailure) as err:
        svc.download(op, **{**FILE, **kw})
    assert err.value.code == "scene_invalid"
    assert svc.inspect(op)["state"] == "intent"


def other_run_landed(svc, state="verified", body=BODY, **facts):
    """Another run's record for the same remote path/local dir/size (public ledger API only)."""
    local = svc.config.resolve_destination("out")
    target = local / "a.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    rec, created = svc.ledger.begin("download", remote_path="/m/a.bin", local_dir=str(local), expected_size=3,
                                    expected_sha256=None)
    assert created
    if state == "verified":
        facts = {"verification": "local_size", "target_path": str(target),
                 "sha256": hashlib.sha256(body).hexdigest().upper(), **facts}
    svc.ledger.update(rec["key"], state, **facts)
    return rec["key"]


# -- the reported defect -----------------------------------------------------------

def test_repeat_of_unsubmitted_intent_never_adopts_another_resource(svc):
    failed_intent(svc, "op-B", resource_id="B")
    key = other_run_landed(svc, resource_id="A", resource_version=None)
    before = svc.ledger.get(key)
    repeat = svc.download("op-B", resource_id="B", **FILE)
    assert repeat["created"] is False and repeat["status"] == "needs_review"
    assert repeat["verification_level"] == "none" and repeat["content_verified"] is False
    assert svc.reconcile("op-B")["evidence"]["reason"] == "prior_resource_mismatch"
    fresh = svc.download("op-C", resource_id="B", **FILE)
    assert fresh["status"] == "needs_review" and fresh["reason"] == "prior_resource_mismatch"
    assert svc.ledger.get(key) == before  # nothing rebound, updated or resubmitted


@pytest.mark.parametrize("recorded, requested", [("1", "2"), (None, "2"), ("1", None)])
def test_repeat_with_different_or_unclear_version_is_refused(svc, recorded, requested):
    failed_intent(svc, "op-A", resource_id="A", resource_version=requested)
    other_run_landed(svc, resource_id="A", resource_version=recorded)
    repeat = svc.download("op-A", resource_id="A", resource_version=requested, **FILE)
    assert repeat["status"] == "needs_review"
    assert svc.reconcile("op-A")["evidence"]["reason"] == "prior_resource_mismatch"


def test_repeat_with_record_lacking_provenance_is_unknown_not_verified(svc):
    failed_intent(svc, "op-B", resource_id="B")
    other_run_landed(svc)  # no resource_id fact at all
    assert svc.download("op-B", resource_id="B", **FILE)["status"] == "needs_review"
    assert svc.reconcile("op-B")["evidence"]["reason"] == "prior_provenance_unknown"


def test_repeat_with_exact_identity_still_verifies(svc):
    failed_intent(svc, "op-A", resource_id="A", resource_version="7")
    other_run_landed(svc, resource_id="A", resource_version="7")
    repeat = svc.download("op-A", resource_id="A", resource_version="7", **FILE)
    assert repeat["status"] == "verified" and repeat["verification_level"] == "structure_verified"


def test_own_bound_record_without_provenance_still_verifies(svc):
    failed_intent(svc, "op-A", resource_id="A")
    key = other_run_landed(svc)
    svc.operations.record("op-A", {"status": "downloaded", "key": key}, ledger_key=key)  # this op's own receipt
    assert svc.reconcile("op-A")["status"] == "verified"


def test_bound_record_proving_another_resource_is_still_refused(svc):
    failed_intent(svc, "op-B", resource_id="B")
    key = other_run_landed(svc, resource_id="A", resource_version=None)
    svc.operations.record("op-B", {"status": "downloaded", "key": key}, ledger_key=key)
    assert svc.reconcile("op-B")["evidence"]["reason"] == "prior_resource_mismatch"


# -- trusted SHA-256 content reuse is kept --------------------------------------------

def sha_record(svc, body, **facts):
    local = svc.config.resolve_destination("out")
    local.mkdir(parents=True, exist_ok=True)
    (local / "a.bin").write_bytes(body)
    rec, _ = svc.ledger.begin("download", remote_path="/m/a.bin", local_dir=str(local), expected_size=3,
                              expected_sha256=SHA)
    svc.ledger.update(rec["key"], "verified", verification="local_hash", target_path=str(local / "a.bin"),
                      sha256=hashlib.sha256(body).hexdigest().upper(), **facts)


def test_trusted_sha_content_is_reused_across_identity(svc):
    with pytest.raises(am.AutomationFailure):
        svc.download("op-B", resource_id="B", expected_sha256=SHA, **FILE)
    sha_record(svc, BODY, resource_id="A", resource_version=None)
    repeat = svc.download("op-B", resource_id="B", expected_sha256=SHA, **FILE)
    assert repeat["status"] == "verified" and repeat["content_verified"] is True and repeat["sha256"] == SHA


def test_trusted_sha_does_not_accept_other_bytes(svc):
    with pytest.raises(am.AutomationFailure):
        svc.download("op-B", resource_id="B", expected_sha256=SHA, **FILE)
    sha_record(svc, b"xyz")
    assert svc.download("op-B", resource_id="B", expected_sha256=SHA, **FILE)["status"] != "verified"


# -- unknown / old failed: never replayed, never adopted ---------------------------------

def test_unknown_record_of_other_resource_is_refused_without_native(svc):
    failed_intent(svc, "op-B", resource_id="B")
    key = other_run_landed(svc, state="submitted", resource_id="A")
    before = svc.ledger.get(key)
    assert svc.download("op-B", resource_id="B", **FILE)["status"] == "needs_review"
    assert svc.recover("op-B")["recovered"] is False
    assert svc.ledger.get(key) == before


def test_old_failed_key_is_not_replayed_by_repeat_or_recover(svc):
    failed_intent(svc, "op-B", resource_id="B")
    key = other_run_landed(svc, state="failed", reason="x", before_submit=True)
    before = svc.ledger.get(key)
    repeat = svc.download("op-B", resource_id="B", **FILE)
    assert repeat["created"] is False and repeat["status"] == "not_submitted"
    assert svc.recover("op-B")["recovered"] is False
    assert svc.ledger.get(key) == before  # repeat/recover never restart or rewrite it


# -- recover: the one filesystem side effect follows the same check -------------------------

def submitted_with_new_file(svc, **facts):
    dl = svc.config.client_download_dir
    dl.mkdir(parents=True)
    local = svc.config.resolve_destination("out")
    manifest = svc.ledger.path.parent / "download-probe.before.json"
    write_manifest(manifest, dl, snapshot_dir(dl))
    (dl / "a.bin").write_bytes(BODY)
    rec, _ = svc.ledger.begin("download", remote_path="/m/a.bin", local_dir=str(local), expected_size=3,
                              expected_sha256=None)
    svc.ledger.update(rec["key"], "submitted", via="official_client_gui", manifest=str(manifest), name="a.bin",
                      expected_size=3, expected_sha256=None, local_dir=str(local), **facts)
    return rec["key"], local / "a.bin", dl / "a.bin"


def test_recover_never_moves_another_runs_unproven_download(svc):
    failed_intent(svc, "op-B", resource_id="B")
    key, target, source = submitted_with_new_file(svc)
    out = svc.recover("op-B")
    assert out["recovered"] is False and out["evidence"]["reason"] == "prior_provenance_unknown"
    assert source.exists() and not target.exists()
    assert svc.ledger.get(key)["state"] == "submitted"


def test_recover_of_own_bound_submission_still_moves_and_verifies(svc):
    failed_intent(svc, "op-A", resource_id="A")
    key, target, source = submitted_with_new_file(svc)
    svc.operations.record("op-A", {"status": "submitted", "key": key}, ledger_key=key)
    out = svc.recover("op-A")
    assert out["recovered"] is True and out["status"] == "verified"
    assert target.read_bytes() == BODY and not source.exists()


# -- folder ------------------------------------------------------------------------------

def folder_landed(svc, **facts):
    local = svc.config.resolve_destination("out")
    root = local / "pkg"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_bytes(b"aaaa")
    (root / "sub" / "b.png").write_bytes(b"bb")
    rec, _ = svc.ledger.begin("download_dir", remote_path="/m/pkg", local_dir=str(local))
    svc.ledger.update(rec["key"], "verified", verification="local_names_sizes", target_path=str(root), **facts)


def manifest(trusted):
    files = [("a.txt", b"aaaa"), ("sub/b.png", b"bb")]
    return {"files": [{"rel": r, "size": len(b), **({"sha256": hashlib.sha256(b).hexdigest()} if trusted else {})}
                      for r, b in files]}


@pytest.mark.parametrize("trusted, expected", [(False, "needs_review"), (True, "verified")])
def test_download_dir_repeat_checks_identity_unless_fully_trusted(svc, trusted, expected):
    args = dict(remote_path="/m/pkg", resource_id="B", destination="out", expected_manifest=manifest(trusted))
    with pytest.raises(am.AutomationFailure):
        svc.download_dir("op-B", **args)
    folder_landed(svc, resource_id="A", resource_version=None)
    repeat = svc.download_dir("op-B", **args)
    assert repeat["status"] == expected
    if trusted:
        assert repeat["verification_level"] == "content_verified"
    else:
        assert svc.reconcile("op-B")["evidence"]["reason"] == "prior_resource_mismatch"


def test_download_dir_repeat_with_matching_identity_is_structure_verified(svc):
    args = dict(remote_path="/m/pkg", resource_id="A", destination="out", expected_manifest=manifest(False))
    with pytest.raises(am.AutomationFailure):
        svc.download_dir("op-A", **args)
    folder_landed(svc, resource_id="A", resource_version=None)
    assert svc.download_dir("op-A", **args)["verification_level"] == "structure_verified"
