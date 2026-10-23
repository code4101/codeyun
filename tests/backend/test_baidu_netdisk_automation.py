import hashlib
import io
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from PIL import Image

from backend.core.baidu_netdisk import automation as am
from backend.core.baidu_netdisk import automation_config as ac
from backend.core.baidu_netdisk import upload_operations as uo
from backend.core.baidu_netdisk.client import StepFailed, TransferResult
from backend.core.baidu_netdisk.operations import native_download_keys
from backend.core.baidu_netdisk.upload_operations import describe_source

SECRET = "SECRET-ocr-token-9f8e"
CAPTURE_ID = "a" * 32
CAPTURE_SHA = "b" * 64


def test_diagnostic_account_wait_respects_busy_owner_before_gui(tmp_path, monkeypatch):
    from backend.core.baidu_netdisk.ownership import NativeAccountSession, NativeAccountBusy
    from backend.core.baidu_netdisk import web
    seen = []

    @contextmanager
    def busy_context(**options):
        seen.append(options['lock_timeout'])
        raise web.WebError('busy')
        yield  # pragma: no cover

    class NoGuiLease:
        def acquire(self, *args, **kwargs):
            pytest.fail('busy account must not acquire GUI')

    with pytest.raises(NativeAccountBusy):
        with NativeAccountSession('diagnosis', with_browser=True, lock_timeout=30,
                                  owned_context=busy_context, lease_store=NoGuiLease()):
            pytest.fail('must preserve existing account owner')
    assert seen == [30]


@pytest.mark.parametrize('wait', [-1, 31, float('inf'), float('nan'), True])
def test_native_rejects_invalid_account_wait_before_providers(tmp_path, wait):
    service = am.NetdiskAutomation(make_config(tmp_path))
    with pytest.raises(ValueError, match='account_wait_seconds'):
        with service.native(account_wait_seconds=wait):
            pytest.fail('invalid wait must not reach providers')


def test_native_forwards_bounded_account_wait(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(am, 'NativeAccountSession', fake_session(calls, FakeHandle()))
    monkeypatch.setattr(am, 'AssetNetdiskClient', lambda **kw: FakeClient(calls, **kw))
    service = am.NetdiskAutomation(make_config(tmp_path))
    publish(service, 'home')
    publish(service, 'files')
    with service.native('diagnosis', account_wait_seconds=30):
        assert calls[0] == ('session', 'diagnosis',
                            {'lease_token': None, 'with_browser': True, 'lock_timeout': 30})


def make_config(tmp_path, scene_ids=("home", "files"), profile="main"):
    return ac.AutomationConfig(profile=profile, scene_ids=list(scene_ids), client_download_dir=tmp_path / "dl",
                               local_root=tmp_path / "local", paths=ac.AutomationPaths(tmp_path / "auto"))


def publish(service, scene_id, profile="main"):
    buf = io.BytesIO()
    Image.new("RGB", (40, 30)).save(buf, "PNG")
    ref = service.assets.capture(buf.getvalue())
    service.assets.publish_scene({"scene_id": scene_id, "title": scene_id, "profile": profile,
                                  "reference_image_id": ref["image_id"],
                                  "shapes": [{"shape_id": "tab", "role": "identity", "bbox": [0, 0, 10, 10],
                                              "ocr": "x"}]}, 0)


def boom(*a, **k):
    raise AssertionError("native/browser/listing must not be touched")


@pytest.fixture
def no_native(monkeypatch):
    monkeypatch.setattr(am, "NativeAccountSession", boom)
    monkeypatch.setattr(am, "AssetNetdiskClient", boom)
    monkeypatch.setattr(am.listing, "list_directory", boom)
    monkeypatch.setattr(am.listing, "resolve_remote", boom)


class FakeHandle:
    def browser_context(self):
        raise AssertionError("not entered in this test")

    def pace(self):
        return {}


def fake_session(calls, handle):
    @contextmanager
    def session(owner, **kwargs):
        calls.append(("session", owner, kwargs))
        yield handle
        calls.append(("session_closed",))
    return session


class FakeClient:
    def __init__(self, calls, **kwargs):
        self.kwargs, self.calls = kwargs, calls
        calls.append(("client", kwargs))

    def observe_assets(self, window_id=None, *, role=None):
        self.calls.append(("observe", role))
        return {"observation_id": "o1"}

    def action_shape(self, scene_id, shape_id, expected_scene_ids=None, discovery=False):
        self.calls.append(("action", scene_id, shape_id, expected_scene_ids, discovery))
        raise StepFailed("action_expired", capture_id=CAPTURE_ID, capture_sha256=CAPTURE_SHA, ocr_text=SECRET)


def test_providers_derive_from_config_paths(tmp_path):
    cfg = make_config(tmp_path)
    service = am.NetdiskAutomation(cfg)
    assert service.ledger.path == cfg.paths.transfers_file
    assert service.operations.root == cfg.paths.operations_dir
    assert service.operations.ledger is service.ledger
    assert service.assets.root == cfg.paths.assets_dir.resolve()


def test_unsubmitted_package_retries_entire_original_folder(tmp_path, monkeypatch, no_native):
    service = am.NetdiskAutomation(make_config(tmp_path))
    request = {"kind": "download_dir", "remote_path": "/docs/pkg", "local_dir": str(tmp_path / "local"),
               "resource_id": "2", "expected_manifest": {"files": [{"rel": "nested/a.txt", "size": 3}]}}
    op, _ = service.operations.begin("package-retry", request)
    native, _ = service.ledger.begin("download_dir", remote_path="/docs/pkg", local_dir=str(tmp_path / "local"))
    service.ledger.update(native["key"], "failed", before_submit=True)
    service.operations.record("package-retry", {"status": "not_submitted"}, ledger_key=native["key"])
    calls = []
    monkeypatch.setattr(service, "_execute_download_dir", lambda *args: calls.append(args) or {"status": "verified"})
    assert service.retry_not_submitted("package-retry")["status"] == "verified"
    assert calls[0][0] == "package-retry" and calls[0][1] == op["request"]


@pytest.mark.parametrize("history, before_submit, receipt, allowed", [
    (["failed"], True, "not_submitted", True),
    (["submitted"], False, "submitted", False),
    (["submitted", "failed"], True, "not_submitted", False),
    (["failed"], False, "not_submitted", False),
    (["failed"], True, "retry_started", False),
])
def test_retry_preserves_intent_and_only_replays_proven_unsubmitted(
        tmp_path, monkeypatch, no_native, history, before_submit, receipt, allowed):
    service = am.NetdiskAutomation(make_config(tmp_path))
    request = {"kind": "download", "remote_path": "/docs/a.txt", "local_dir": str(tmp_path / "local"),
               "resource_id": "1", "expected_size": 3}
    op, _ = service.operations.begin("retry-one", request)
    native, _ = service.ledger.begin("download", remote_path="/docs/a.txt", local_dir=str(tmp_path / "local"),
                                     expected_size=3, expected_sha256=None)
    for state in history:
        service.ledger.update(native["key"], state, before_submit=before_submit)
    service.operations.record("retry-one", {"status": receipt}, ledger_key=native["key"])
    calls = []
    monkeypatch.setattr(service, "_execute_download", lambda *args: calls.append(args) or {"status": "verified"})
    result = service.retry_not_submitted("retry-one")
    assert (result["status"] == "verified") is allowed
    assert len(calls) == int(allowed)
    current = service.inspect("retry-one")
    assert current["request"] == op["request"] and current["ledger_key"] == native["key"]
    if allowed:
        assert current["receipts"][-1]["status"] == "retry_started"
        assert service.retry_not_submitted("retry-one")["status"] == "needs_review"
        assert len(calls) == 1  # crash after a claim is never an implicit replay


def test_offline_methods_never_open_native_or_listing(tmp_path, no_native):
    cfg = make_config(tmp_path)
    service = am.NetdiskAutomation(cfg)
    request = {"kind": "download", "remote_path": "/docs/a.txt", "local_dir": str(tmp_path / "local"),
               "expected_size": 3}
    service.operations.begin("op-1", request)
    assert service.inspect("op-1")["state"] == "intent"
    assert service.inspect("missing") is None
    assert service.reconcile("op-1")["status"] == "needs_review"
    assert service.recover("op-1")["recovered"] is False
    (tmp_path / "local" / "out").mkdir(parents=True)
    (tmp_path / "local" / "out" / "f.txt").write_bytes(b"abc")
    assert service.verify("out")["status"] == "local_hash_recorded"
    with pytest.raises(am.AutomationFailure):
        service.verify("../escape")
    assert service.ledger.get("anything") is None


@pytest.mark.parametrize("profile, code", [("main", "scene_invalid"), ("other", "scene_profile_mismatch")])
def test_scene_problems_rejected_before_ownership(tmp_path, no_native, profile, code):
    hooked = []
    service = am.NetdiskAutomation(make_config(tmp_path), on_failure=hooked.append)
    publish(service, "home", profile=profile)
    if profile == "other":
        publish(service, "files", profile=profile)
    with pytest.raises(am.AutomationFailure) as err:
        with service.native():
            pytest.fail("context must not open")
    assert err.value.code == code and hooked[0]["code"] == code
    assert hooked[0]["operation"] == "native_validate"


def test_native_context_shares_providers_and_borrowed_listing(tmp_path, monkeypatch):
    calls, handle = [], FakeHandle()
    monkeypatch.setattr(am, "NativeAccountSession", fake_session(calls, handle))
    monkeypatch.setattr(am, "AssetNetdiskClient", lambda **kw: FakeClient(calls, **kw))
    listed = []
    monkeypatch.setattr(am.listing, "list_directory", lambda *a, **k: listed.append((a, k)) or {"status": "listed"})
    service = am.NetdiskAutomation(make_config(tmp_path))
    publish(service, "home")
    publish(service, "files")
    with service.native("task-1", lease_token="tok") as ctx:
        assert calls[0] == ("session", "task-1", {"lease_token": "tok", "with_browser": True})
        kw = ctx.client.kwargs
        assert kw["ledger"] is service.ledger and kw["store"] is service.assets and kw["account"] is handle
        assert kw["profile"] == "main" and list(kw["scene_ids"]) == ["home", "files"]
        assert kw["canonical_listing"]("/docs") == {"status": "listed"}
        assert listed == [(("/docs",), {"context": handle.browser_context, "pace": handle.pace})]
        assert ctx.observe("settings") == {"observation_id": "o1"}
    assert calls[-1] == ("session_closed",)
    assert ("observe", "settings") in calls


def test_provider_exception_is_sanitized_and_persisted(tmp_path, monkeypatch):
    calls, hooked = [], []
    monkeypatch.setattr(am, "NativeAccountSession", fake_session(calls, FakeHandle()))
    monkeypatch.setattr(am, "AssetNetdiskClient", lambda **kw: FakeClient(calls, **kw))
    cfg = make_config(tmp_path)
    service = am.NetdiskAutomation(cfg, on_failure=hooked.append)
    publish(service, "home")
    publish(service, "files")
    with service.native() as ctx:
        with pytest.raises(am.AutomationFailure) as err:
            ctx.action("home", "tab", ["files"])
    assert ("action", "home", "tab", ["files"], False) in calls
    failure = err.value
    assert (failure.code, failure.error_type) == ("action_expired", "StepFailed")
    assert failure.receipt["evidence"] == {"capture_id": CAPTURE_ID, "capture_sha256": CAPTURE_SHA}
    files = list(cfg.paths.failures_dir.iterdir())
    assert [f.name for f in files] == [f"{failure.case_id}.json"]
    text = files[0].read_text(encoding="utf-8")
    assert json.loads(text) == failure.receipt == hooked[0]
    assert SECRET not in text and SECRET not in json.dumps(hooked) and SECRET not in str(failure)


def test_listing_delegation_contract_and_plain_exception(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(am.listing, "list_directory", lambda *a, **k: seen.append(("ls", a, k)) or {"status": "listed"})
    monkeypatch.setattr(am.listing, "resolve_remote", lambda *a, **k: seen.append(("rs", a, k)) or {"status": "resolved"})
    monkeypatch.setattr(am, "NativeAccountSession", boom)
    service = am.NetdiskAutomation(make_config(tmp_path))
    ctx_factory, pace = object(), object()
    service.list_directory("/a", "id1", context=ctx_factory, pace=pace)
    service.resolve("/a/b", None, False)
    assert seen == [("ls", ("/a", "id1"), {"context": ctx_factory, "pace": pace}),
                    ("rs", ("/a/b", None, False), {"context": None, "pace": None})]

    def leak(*a, **k):
        raise ValueError(SECRET)
    monkeypatch.setattr(am.listing, "list_directory", leak)
    with pytest.raises(am.AutomationFailure) as err:
        service.list_directory("/a")
    assert (err.value.code, err.value.error_type) == ("provider_error", "ValueError")
    assert err.value.__cause__ is None and err.value.__suppress_context__
    stored = (service.config.paths.failures_dir / f"{err.value.case_id}.json").read_text(encoding="utf-8")
    assert SECRET not in stored


def test_failing_hook_never_masks_the_original_failure(tmp_path, monkeypatch):
    hooked = []

    def hook(receipt):
        hooked.append(receipt)
        raise RuntimeError(SECRET)
    monkeypatch.setattr(am.listing, "list_directory", lambda *a, **k: (_ for _ in ()).throw(StepFailed("listing_unstable")))
    service = am.NetdiskAutomation(make_config(tmp_path), on_failure=hook)
    with pytest.raises(am.AutomationFailure) as err:
        service.list_directory("/a")
    assert err.value.code == "listing_unstable" and len(hooked) == 1
    assert "hook_error_type" not in hooked[0] and err.value.receipt["hook_error_type"] == "RuntimeError"
    stored = (service.config.paths.failures_dir / f"{err.value.case_id}.json").read_text(encoding="utf-8")
    assert json.loads(stored) == err.value.receipt and SECRET not in stored
    assert [p.name for p in service.config.paths.failures_dir.iterdir()] == [f"{err.value.case_id}.json"]


# -- maintenance queue ------------------------------------------------------------------

def leak(*a, **k):
    err = StepFailed("listing_unstable")
    err.facts = {"capture_id": CAPTURE_ID, "ocr": SECRET, "path": "C:/secret/" + SECRET}
    raise err


def test_failures_feed_default_queue_sanitized_with_hook_and_no_dispatch(tmp_path, monkeypatch):
    dispatched, hooked = [], []
    monkeypatch.setattr(am.listing, "list_directory", leak)
    cfg = make_config(tmp_path)
    service = am.NetdiskAutomation(cfg, on_failure=hooked.append)
    service.maintenance.dispatcher = lambda *a, **k: dispatched.append(a) or pytest.fail("implicit dispatch")
    assert service.maintenance.root == cfg.paths.root / "maintenance"
    failures = []
    for _ in range(3):
        with pytest.raises(am.AutomationFailure) as err:
            service.list_directory("/a")
        failures.append(err.value)
    assert [h["case_id"] for h in hooked] == [f.case_id for f in failures]
    assert all("maintenance_error_type" not in f.receipt for f in failures)
    cases = service.list_maintenance()
    assert len(cases) == 1 and cases[0]["count"] == 3 and cases[0]["ready"] and cases[0]["dispatch"] is None
    assert (cases[0]["operation"], cases[0]["code"], cases[0]["error_type"]) == (
        "list_directory", "listing_unstable", "StepFailed")
    assert cases[0]["evidence"] == [{"capture_id": CAPTURE_ID}]
    # A replayed source receipt (e.g. by the hook owner) never counts twice.
    service.maintenance.enqueue(hooked[0])
    assert service.inspect_maintenance(cases[0]["case_id"])["count"] == 3
    stored = "".join(p.read_text(encoding="utf-8") for p in (cfg.paths.root / "maintenance").rglob("*.json"))
    assert SECRET not in stored and SECRET not in json.dumps(hooked) and "secret" not in stored
    assert dispatched == []


class FailingQueue:
    def __init__(self):
        self.calls = []

    def enqueue(self, receipt):
        self.calls.append(receipt)
        raise OSError(SECRET)


@pytest.mark.parametrize("hook_fails", [False, True])
def test_failing_queue_never_masks_failure_or_skips_hook(tmp_path, monkeypatch, hook_fails):
    hooked, queue = [], FailingQueue()

    def hook(receipt):
        hooked.append(receipt)
        if hook_fails:
            raise RuntimeError(SECRET)
    monkeypatch.setattr(am.listing, "list_directory", leak)
    service = am.NetdiskAutomation(make_config(tmp_path), on_failure=hook, maintenance=queue)
    with pytest.raises(am.AutomationFailure) as err:
        service.list_directory("/a")
    receipt = err.value.receipt
    assert err.value.code == "listing_unstable" and receipt["maintenance_error_type"] == "OSError"
    assert len(queue.calls) == 1 and len(hooked) == 1
    assert hooked[0] == queue.calls[0] and "maintenance_error_type" not in hooked[0]
    assert ("hook_error_type" in receipt) is hook_fails
    stored = (service.config.paths.failures_dir / f"{err.value.case_id}.json").read_text(encoding="utf-8")
    assert json.loads(stored) == receipt and SECRET not in stored


def test_dispatch_maintenance_delegates_to_queue_contract_only(tmp_path):
    calls = []

    class Queue:
        def list(self):
            return calls.append(("list",)) or []

        def inspect(self, case_id):
            return calls.append(("inspect", case_id))

        def dispatch(self, case_id, workspace_dir):
            return calls.append(("dispatch", case_id, workspace_dir)) or {"dispatch": {"state": "dispatched"}}
    service = am.NetdiskAutomation(make_config(tmp_path), maintenance=Queue())
    assert service.list_maintenance() == [] and service.inspect_maintenance("c" * 32) is None
    assert service.dispatch_maintenance("c" * 32, "W") == {"dispatch": {"state": "dispatched"}}
    assert calls == [("list",), ("inspect", "c" * 32), ("dispatch", "c" * 32, "W")]


# -- download ------------------------------------------------------------------------

FILE = "/docs/a.txt"
RID = "998877"


def download_args(**over):
    return {"remote_path": FILE, "resource_id": RID, "expected_size": 3, "destination": "out", **over}


class DownloadClient(FakeClient):
    """Records preflight order; ``download_file`` returns a scripted native outcome, never a landed file."""

    def __init__(self, calls, outcome=None, **kwargs):
        super().__init__(calls, **kwargs)
        self.account, self.outcome = kwargs["account"], outcome

    def download_file(self, remote_path, local_dir, *, client_download_dir, expected_size, expected_sha256=None):
        self.calls.append(("download_file", remote_path, local_dir, client_download_dir, expected_size, expected_sha256))
        if self.outcome is None:
            raise AssertionError("download_file must not be called")
        return self.outcome(self)


def native_service(tmp_path, monkeypatch, calls, remote, outcome=None):
    handle = FakeHandle()
    monkeypatch.setattr(am, "NativeAccountSession", fake_session(calls, handle))
    monkeypatch.setattr(am, "AssetNetdiskClient", lambda **kw: DownloadClient(calls, outcome, **kw))
    monkeypatch.setattr(am.listing, "list_directory", boom)

    def resolve(path, expected_id=None, want_dir=None, **kw):
        assert kw == {"context": handle.browser_context, "pace": handle.pace}
        calls.append(("resolve", path, expected_id, want_dir))
        return remote
    monkeypatch.setattr(am.listing, "resolve_remote", resolve)
    service = am.NetdiskAutomation(make_config(tmp_path))
    publish(service, "home")
    publish(service, "files")
    return service


def resolved(**over):
    return {"status": "resolved", "is_root": False, "path": FILE, "id": int(RID), "name": "a.txt", "is_dir": False,
            "size": 3, "md5": "c" * 32, **over}


@pytest.mark.parametrize("over, code", [
    ({"resource_id": None}, "resource_id_invalid"), ({"resource_id": True}, "resource_id_invalid"),
    ({"resource_id": ""}, "resource_id_invalid"), ({"destination": "../escape"}, "destination_invalid"),
    ({"expected_size": -1}, "request_invalid"), ({"expected_sha256": "c" * 32}, "request_invalid"),
    ({"remote_path": "relative.txt"}, "request_invalid")])
def test_download_validation_fails_before_intent_or_ownership(tmp_path, no_native, over, code):
    service = am.NetdiskAutomation(make_config(tmp_path))
    with pytest.raises(am.AutomationFailure) as err:
        service.download("op-dl", **download_args(**over))
    assert err.value.code == code and err.value.receipt["operation"] == "download_intent"
    assert service.inspect("op-dl") is None
    with pytest.raises(am.AutomationFailure) as err:
        service.download("bad id!", **download_args())
    assert err.value.code == "operation_id_invalid"


@pytest.mark.parametrize("over, reason", [
    ({"id": 1}, "remote_id_mismatch"), ({"is_dir": True}, "remote_type_mismatch"),
    ({"size": 4}, "remote_size_mismatch"), ({"path": "/docs/b.txt"}, "remote_path_mismatch"),
    ({"status": "not_found"}, "remote_not_resolved")])
def test_remote_preflight_mismatch_prevents_native_download(tmp_path, monkeypatch, over, reason):
    calls = []
    service = native_service(tmp_path, monkeypatch, calls, resolved(**over))
    out = service.download("op-dl", **download_args())
    assert ("resolve", FILE, RID, False) in calls
    assert not [c for c in calls if c[0] == "download_file"] and calls[-1] == ("session_closed",)
    assert out == {"operation_id": "op-dl", "created": True, "status": "needs_review", "next": am._REVIEW,
                   "key": None, "verification_level": "none", "content_verified": False, "reason": reason}
    op = service.inspect("op-dl")
    assert op["request"]["resource_id"] == RID and op["ledger_key"] is None
    assert op["receipts"][-1]["status"] == "needs_review"


def test_duplicate_intent_only_reconciles_and_conflict_is_typed(tmp_path, monkeypatch):
    calls = []
    service = native_service(tmp_path, monkeypatch, calls, resolved(size=4))
    assert service.download("op-dl", **download_args())["reason"] == "remote_size_mismatch"
    for name in ("NativeAccountSession", "AssetNetdiskClient"):
        monkeypatch.setattr(am, name, boom)
    monkeypatch.setattr(am.listing, "resolve_remote", boom)
    again = service.download("op-dl", **download_args())
    assert again["created"] is False and again["status"] == "needs_review"
    assert again["reason"] == "ledger_record_missing" and again["verification_level"] == "none"
    with pytest.raises(am.AutomationFailure) as err:
        service.download("op-dl", **download_args(expected_size=5))
    assert (err.value.code, err.value.error_type) == ("operation_conflict", "OperationConflict")
    assert len(service.inspect("op-dl")["receipts"]) == 1


@pytest.mark.parametrize("facts, reason", [
    ({}, "prior_provenance_unknown"), ({"resource_id": "other"}, "prior_resource_mismatch"),
    ({"resource_id": RID, "resource_version": "v1"}, "prior_resource_mismatch")])
def test_prior_native_record_without_trusted_sha_is_not_reused(tmp_path, no_native, facts, reason):
    service = am.NetdiskAutomation(make_config(tmp_path))
    local_dir = str(service.config.resolve_destination("out"))
    prior, _ = service.ledger.begin("download", remote_path=FILE, local_dir=local_dir, expected_size=3,
                                    expected_sha256=None)
    service.ledger.update(prior["key"], "submitted", **facts)
    out = service.download("op-dl", **download_args())
    assert (out["status"], out["reason"], out["key"]) == ("needs_review", reason, None)
    assert service.inspect("op-dl")["ledger_key"] is None
    assert service.ledger.get(prior["key"])["state"] == "submitted"


def test_unknown_native_result_is_bound_once_and_never_replayed(tmp_path, monkeypatch):
    calls = []

    def unknown(client):
        record, _ = client.kwargs["ledger"].begin("download", remote_path=FILE, local_dir=local_dir, expected_size=3,
                                                  expected_sha256=None)
        client.kwargs["ledger"].update(record["key"], "submitted", via="official_client_gui")
        return TransferResult("state_unknown", "collect_do_not_resubmit", record["key"], {"error": SECRET})
    service = native_service(tmp_path, monkeypatch, calls, resolved(), unknown)
    local_dir = str(service.config.resolve_destination("out"))
    out = service.download("op-dl", **download_args())
    exact = native_download_keys({"kind": "download", "remote_path": FILE, "local_dir": local_dir,
                                  "expected_size": 3})[0]["key"]
    assert [c for c in calls if c[0] == "download_file"] == [
        ("download_file", FILE, local_dir, service.config.client_download_dir, 3, None)]
    assert out == {"operation_id": "op-dl", "created": True, "status": "state_unknown",
                   "next": "collect_do_not_resubmit", "key": exact, "verification_level": "none",
                   "content_verified": False}
    op = service.inspect("op-dl")
    assert op["ledger_key"] == exact and SECRET not in json.dumps(op) and SECRET not in json.dumps(out)
    for name in ("NativeAccountSession", "AssetNetdiskClient"):
        monkeypatch.setattr(am, name, boom)
    monkeypatch.setattr(am.listing, "resolve_remote", boom)
    again = service.download("op-dl", **download_args())
    assert again["created"] is False and again["key"] == exact and again["content_verified"] is False
    assert again["status"] == "needs_review" and again["reason"] == "manifest_missing"
    assert len([c for c in calls if c[0] == "download_file"]) == 1
    assert "resource_id" not in (service.ledger.get(exact).get("facts") or {})


# -- folder download ------------------------------------------------------------------

DIR = "/docs/pack"
DIR_ID = "556677"
DIR_VERSION = "1700000000"
DIR_FILES = {"a.txt": b"abc", "sub/b.bin": b"hello"}


def sha(data):
    return hashlib.sha256(data).hexdigest().upper()


def trusted_manifest(**over):
    files = [{"rel": rel, "size": len(data), "sha256": sha(data)} for rel, data in DIR_FILES.items()]
    return {"files": files, **over}


def dir_args(**over):
    return {"remote_path": DIR, "resource_id": DIR_ID, "destination": "out", "expected_manifest": trusted_manifest(),
            "resource_version": DIR_VERSION, **over}


def dir_resolved(**over):
    return {"status": "resolved", "is_root": False, "path": DIR, "id": int(DIR_ID), "name": "pack", "is_dir": True,
            "size": 0, "version": int(DIR_VERSION), **over}


class DirClient(DownloadClient):
    def download_dir(self, remote_dir, local_dir, *, client_download_dir, expected_manifest):
        assert expected_manifest["resource_id"] is not None and expected_manifest["files"]
        self.calls.append(("download_dir", remote_dir, local_dir, client_download_dir))
        if self.outcome is None:
            raise AssertionError("download_dir must not be called")
        record, _ = self.kwargs["ledger"].begin("download_dir", remote_path=remote_dir, local_dir=local_dir)
        return self.outcome(self, record["key"], local_dir)


def landed(files=DIR_FILES):
    """Emulates collect_dir: the tree lands under local_dir/<name> and the ledger is verified."""
    def outcome(client, key, local_dir):
        target = Path(local_dir) / "pack"
        for rel, data in files.items():
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            (target / rel).write_bytes(data)
        client.kwargs["ledger"].update(key, "verified", verification="local_names_sizes", target_path=str(target),
                                       files=[{"rel": r, "size": len(d)} for r, d in files.items()], duplicates=[])
        return TransferResult("downloaded", "done", key, {"files_total": len(files), "error": SECRET})
    return outcome


def unknown_dir(client, key, local_dir):
    client.kwargs["ledger"].update(key, "submitted", via="official_client_gui")
    return TransferResult("state_unknown", "resume_download_dir_do_not_resubmit", key, {"error": SECRET})


def dir_service(tmp_path, monkeypatch, calls, remote, outcome=None):
    service = native_service(tmp_path, monkeypatch, calls, remote)
    monkeypatch.setattr(am, "AssetNetdiskClient", lambda **kw: DirClient(calls, outcome, **kw))
    return service


def offline_only(monkeypatch):
    for name in ("NativeAccountSession", "AssetNetdiskClient"):
        monkeypatch.setattr(am, name, boom)
    monkeypatch.setattr(am.listing, "resolve_remote", boom)
    monkeypatch.setattr(am.listing, "list_directory", boom)


def dir_key(service):
    local_dir = str(service.config.resolve_destination("out"))
    return native_download_keys({"kind": "download_dir", "remote_path": DIR, "local_dir": local_dir})[0]["key"]


@pytest.mark.parametrize("over, code", [
    ({"expected_manifest": None}, "manifest_required"),
    ({"expected_manifest": []}, "manifest_invalid"),
    ({"expected_manifest": {"files": [{"rel": "../x", "size": 1}]}}, "manifest_invalid"),
    ({"expected_manifest": {"files": [{"rel": "a.txt", "size": 3, "sha256": "c" * 32}]}}, "manifest_invalid"),
    ({"expected_manifest": {"files": []}}, "manifest_empty"),
    ({"expected_manifest": trusted_manifest(resource_id="other")}, "manifest_identity_mismatch"),
    ({"expected_manifest": trusted_manifest(resource_version="1"), "resource_version": None},
     "manifest_identity_mismatch"),
    ({"resource_id": ""}, "resource_id_invalid"), ({"destination": "../escape"}, "destination_invalid"),
    ({"remote_path": "relative"}, "request_invalid")])
def test_download_dir_validation_fails_before_intent_or_ownership(tmp_path, no_native, over, code):
    service = am.NetdiskAutomation(make_config(tmp_path))
    with pytest.raises(am.AutomationFailure) as err:
        service.download_dir("op-dir", **dir_args(**over))
    assert err.value.code == code and err.value.receipt["operation"] == "download_dir_intent"
    assert service.inspect("op-dir") is None and service.ledger.get(dir_key(service)) is None


def test_download_dir_manifest_is_normalized_and_identity_bound(tmp_path, no_native, monkeypatch):
    service = am.NetdiskAutomation(make_config(tmp_path))
    monkeypatch.setattr(service, "_unproven_prior", lambda request: "stop_before_native")
    manifest = trusted_manifest(resource_id=int(DIR_ID))
    manifest["files"][0]["sha256"] = manifest["files"][0]["sha256"].lower()
    assert service.download_dir("op-dir", **dir_args(expected_manifest=manifest))["reason"] == "stop_before_native"
    stored = service.inspect("op-dir")["request"]
    assert stored["kind"] == "download_dir" and stored["resource_id"] == DIR_ID
    assert stored["expected_manifest"]["resource_id"] == int(DIR_ID)
    assert stored["expected_manifest"]["resource_version"] == DIR_VERSION
    assert [f["sha256"] for f in stored["expected_manifest"]["files"]] == [sha(d) for d in DIR_FILES.values()]


@pytest.mark.parametrize("over, reason", [
    ({"id": 1}, "remote_id_mismatch"), ({"is_dir": False}, "remote_type_mismatch"),
    ({"path": "/docs/other"}, "remote_path_mismatch"), ({"version": 1}, "remote_version_mismatch"),
    ({"status": "not_found"}, "remote_not_resolved"), ({"is_root": True}, "remote_not_resolved")])
def test_download_dir_preflight_mismatch_prevents_native_submit(tmp_path, monkeypatch, over, reason):
    calls = []
    service = dir_service(tmp_path, monkeypatch, calls, dir_resolved(**over), landed())
    out = service.download_dir("op-dir", **dir_args())
    assert ("resolve", DIR, DIR_ID, True) in calls
    assert not [c for c in calls if c[0] == "download_dir"] and calls[-1] == ("session_closed",)
    assert out == {"operation_id": "op-dir", "created": True, "status": "needs_review", "next": am._REVIEW,
                   "key": None, "verification_level": "none", "content_verified": False, "reason": reason}
    assert service.inspect("op-dir")["ledger_key"] is None and service.ledger.get(dir_key(service)) is None


def test_fresh_download_dir_is_called_once_and_content_verified_against_manifest(tmp_path, monkeypatch):
    calls = []
    service = dir_service(tmp_path, monkeypatch, calls, dir_resolved(), landed())
    out = service.download_dir("op-dir", **dir_args(), owner="task-dir", lease_token="tok")
    key, local_dir = dir_key(service), str(service.config.resolve_destination("out"))
    assert calls[0] == ("session", "task-dir", {"lease_token": "tok", "with_browser": True})
    assert [c for c in calls if c[0] in {"resolve", "download_dir"}] == [
        ("resolve", DIR, DIR_ID, True), ("download_dir", DIR, local_dir, service.config.client_download_dir)]
    assert set(out) == {"operation_id", "created", "status", "next", "key", "verification_level", "content_verified",
                        "target_path", "manifest_sha256", "counts"}
    assert (out["status"], out["key"], out["created"]) == ("verified", key, True)
    assert out["verification_level"] == "content_verified" and out["content_verified"] is True
    assert out["target_path"] == str(tmp_path / "local" / "out" / "pack")
    assert len(out["manifest_sha256"]) == 64
    assert out["counts"]["files"] == 2 and out["counts"]["content_matched"] == 2 and out["counts"]["missing"] == 0
    assert SECRET not in json.dumps(out) and "sub/b.bin" not in json.dumps(out)
    facts = service.ledger.get(key)["facts"]
    assert (facts["resource_id"], facts["resource_version"]) == (DIR_ID, DIR_VERSION)
    op = service.inspect("op-dir")
    assert op["ledger_key"] == key and op["receipts"][-1]["verification"] == "content_verified"
    offline_only(monkeypatch)
    again = service.download_dir("op-dir", **dir_args())
    assert again == {**out, "created": False}
    assert service.reconcile("op-dir")["status"] == "verified"
    assert len([c for c in calls if c[0] == "download_dir"]) == 1


def test_download_dir_without_complete_trusted_sha_is_only_structure_verified(tmp_path, monkeypatch):
    calls = []
    manifest = trusted_manifest()
    manifest["files"][1]["sha256"] = None
    service = dir_service(tmp_path, monkeypatch, calls, dir_resolved(), landed())
    out = service.download_dir("op-dir", **dir_args(expected_manifest=manifest))
    assert out["status"] == "verified" and out["verification_level"] == "structure_verified"
    assert out["content_verified"] is False and out["counts"]["without_trusted_sha256"] == 1


def test_download_dir_content_differing_from_manifest_is_not_verified(tmp_path, monkeypatch):
    calls = []
    service = dir_service(tmp_path, monkeypatch, calls, dir_resolved(), landed({**DIR_FILES, "a.txt": b"xyz"}))
    out = service.download_dir("op-dir", **dir_args())
    assert (out["status"], out["reason"], out["next"]) == ("artifact_changed", "hash_mismatch", am._REVIEW)
    assert out["verification_level"] == "none" and out["content_verified"] is False and "target_path" not in out
    assert out["counts"]["hash_mismatch"] == 1 and len(out["manifest_sha256"]) == 64
    assert "resource_id" not in service.ledger.get(out["key"])["facts"]
    assert len([c for c in calls if c[0] == "download_dir"]) == 1


def test_unknown_download_dir_result_is_bound_and_only_reconciled_offline(tmp_path, monkeypatch):
    calls = []
    service = dir_service(tmp_path, monkeypatch, calls, dir_resolved(), unknown_dir)
    out = service.download_dir("op-dir", **dir_args())
    key = dir_key(service)
    assert out == {"operation_id": "op-dir", "created": True, "status": "state_unknown",
                   "next": "resume_download_dir_do_not_resubmit", "key": key, "verification_level": "none",
                   "content_verified": False}
    op = service.inspect("op-dir")
    assert op["ledger_key"] == key and SECRET not in json.dumps(op)
    offline_only(monkeypatch)
    again = service.download_dir("op-dir", **dir_args())
    assert again["created"] is False and again["key"] == key and again["status"] == "needs_review"
    assert again["reason"] == "manifest_missing" and again["content_verified"] is False
    assert service.recover("op-dir")["recovered"] is False  # filesystem only, never resubmits
    with pytest.raises(am.AutomationFailure) as err:
        service.download_dir("op-dir", **dir_args(resource_version="1"))
    assert err.value.code == "operation_conflict"
    assert len([c for c in calls if c[0] == "download_dir"]) == 1
    assert service.ledger.get(key)["state"] == "submitted"


@pytest.mark.parametrize("facts, reason", [
    ({}, "prior_provenance_unknown"), ({"resource_id": "other"}, "prior_resource_mismatch"),
    ({"resource_id": DIR_ID, "resource_version": "1"}, "prior_resource_mismatch")])
def test_prior_native_dir_key_without_provenance_or_trusted_manifest_is_refused(tmp_path, no_native, facts, reason):
    service = am.NetdiskAutomation(make_config(tmp_path))
    manifest = trusted_manifest()
    manifest["files"][0]["sha256"] = None
    prior, _ = service.ledger.begin("download_dir", remote_path=DIR,
                                    local_dir=str(service.config.resolve_destination("out")))
    service.ledger.update(prior["key"], "submitted", **facts)
    out = service.download_dir("op-dir", **dir_args(expected_manifest=manifest))
    assert (out["status"], out["reason"], out["key"]) == ("needs_review", reason, None)
    assert service.inspect("op-dir")["ledger_key"] is None
    assert service.ledger.get(prior["key"])["state"] == "submitted"


@pytest.mark.parametrize("facts", [{}, {"resource_id": DIR_ID, "resource_version": DIR_VERSION}])
def test_prior_native_dir_key_reused_only_with_trusted_manifest_or_matching_provenance(tmp_path, monkeypatch, facts):
    calls = []
    service = dir_service(tmp_path, monkeypatch, calls, dir_resolved(), unknown_dir)
    prior, _ = service.ledger.begin("download_dir", remote_path=DIR,
                                    local_dir=str(service.config.resolve_destination("out")))
    service.ledger.update(prior["key"], "submitted", **facts)
    out = service.download_dir("op-dir", **dir_args())  # trusted SHA-256 for every file
    assert out["status"] == "state_unknown" and out["key"] == prior["key"]
    assert len([c for c in calls if c[0] == "download_dir"]) == 1


# -- upload --------------------------------------------------------------------------

REMOTE_DIR = "/up"
UP_PATH = "/up/u.txt"


def up_source(tmp_path, data=b"hello"):
    folder = tmp_path / "src"
    folder.mkdir(exist_ok=True)
    path = folder / "u.txt"
    path.write_bytes(data)
    return str(path)


def up_remote(**over):
    return {"status": "resolved", "is_root": False, "path": UP_PATH, "id": "4455", "name": "u.txt",
            "is_dir": False, "size": 5, **over}


class UploadClient(FakeClient):
    """Emulates the official client's ledger write; ``upload_file`` returns a scripted outcome."""

    def __init__(self, calls, outcome=None, **kwargs):
        super().__init__(calls, **kwargs)
        self.account, self.outcome = kwargs["account"], outcome

    def upload_file(self, local_path, remote_dir, *, create_missing=False):
        self.calls.append(("upload_file", local_path, remote_dir, create_missing))
        if self.outcome is None:
            raise AssertionError("upload_file must not be called")
        source = describe_source(local_path, remote_dir)
        record, _ = self.kwargs["ledger"].begin("upload", sha256=source["sha256"], remote_path=source["remote_path"])
        return self.outcome(self, record["key"])


def upload_service(tmp_path, monkeypatch, calls, remote, outcome=None, on_enter=None):
    handle = FakeHandle()

    @contextmanager
    def session(owner, **kwargs):
        calls.append(("session", owner, kwargs))
        if on_enter is not None:
            on_enter()
        yield handle
        calls.append(("session_closed",))
    monkeypatch.setattr(am, "NativeAccountSession", session)
    monkeypatch.setattr(am, "AssetNetdiskClient", lambda **kw: UploadClient(calls, outcome, **kw))
    monkeypatch.setattr(am.listing, "list_directory", boom)
    monkeypatch.setattr(am.listing, "resolve_remote", boom)

    def resolve(path, expected_id=None, want_dir=None, **kw):
        assert kw == {"context": handle.browser_context, "pace": handle.pace}
        calls.append(("resolve", path, want_dir))
        return remote
    monkeypatch.setattr(uo, "resolve_remote", resolve)
    service = am.NetdiskAutomation(make_config(tmp_path))
    publish(service, "home")
    publish(service, "files")
    return service


def offline_resolve(monkeypatch, calls, remote):
    """Remote readback outside any native session: the provider's own tab (no context/pace)."""
    for name in ("NativeAccountSession", "AssetNetdiskClient"):
        monkeypatch.setattr(am, name, boom)

    def resolve(path, expected_id=None, want_dir=None, **kw):
        assert kw == {"context": None, "pace": None}
        calls.append(("offline_resolve", path, want_dir))
        return remote
    monkeypatch.setattr(uo, "resolve_remote", resolve)


def submitted(state="submitted", status="submitted", next_step="check_transfer_page_or_remote_later"):
    def outcome(client, key):
        client.kwargs["ledger"].update(key, state, via="official_client_gui")
        return TransferResult(status, next_step, key, {"error": SECRET})
    return outcome


def test_uploads_store_lives_under_operations_dir(tmp_path):
    service = am.NetdiskAutomation(make_config(tmp_path))
    assert service.uploads.root == service.config.paths.operations_dir / "uploads"
    assert service.uploads.ledger is service.ledger


def test_fresh_upload_submits_exactly_once_then_reads_back_in_same_session(tmp_path, monkeypatch):
    calls = []
    source = up_source(tmp_path)
    service = upload_service(tmp_path, monkeypatch, calls, up_remote(), submitted())
    out = service.upload("op-up", source, REMOTE_DIR, owner="task-up", lease_token="tok")
    key = service.uploads.inspect("op-up")["native_key"]
    assert [c for c in calls if c[0] == "upload_file"] == [("upload_file", source, REMOTE_DIR, False)]
    assert calls[0] == ("session", "task-up", {"lease_token": "tok", "with_browser": True})
    # The readback ran on the owned browser before the session closed.
    assert calls.index(("resolve", UP_PATH, False)) < calls.index(("session_closed",))
    assert out["status"] == "remote_present" and out["created"] is True and out["key"] == key
    assert out["remote_present"] is True and out["content_verified"] is False
    assert (out["resource_id"], out["remote_size"], out["remote_path"]) == ("4455", 5, UP_PATH)
    sha = service.uploads.inspect("op-up")["request"]["sha256"]
    for text in (json.dumps(out), json.dumps(service.inspect_upload("op-up"))):
        assert SECRET not in text and source not in text and sha not in text
    inspected = service.inspect_upload("op-up")
    assert inspected["ledger_key"] == key and inspected["final"]["status"] == "remote_present"
    offline_resolve(monkeypatch, calls, up_remote())
    again = service.upload("op-up", source, REMOTE_DIR)
    assert again["created"] is False and again["status"] == "remote_present" and again["terminal"] is True
    assert service.reconcile_upload("op-up")["status"] == "remote_present"
    assert len([c for c in calls if c[0] == "upload_file"]) == 1
    assert not [c for c in calls if c[0] == "offline_resolve"]  # terminal receipt is read from the store


def test_unknown_native_result_stays_blocking_and_duplicate_never_opens_gui(tmp_path, monkeypatch):
    calls = []
    source = up_source(tmp_path)
    unknown = submitted(status="state_unknown", next_step="observe_remote_do_not_resubmit")
    service = upload_service(tmp_path, monkeypatch, calls, up_remote(), unknown)
    out = service.upload("op-up", source, REMOTE_DIR)
    assert (out["status"], out["next"]) == ("state_unknown", "observe_remote_do_not_resubmit")
    assert out["remote_present"] is False and not [c for c in calls if c[0] == "resolve"]
    offline_resolve(monkeypatch, calls, {"status": "not_found"})
    again = service.upload("op-up", source, REMOTE_DIR)
    assert again["created"] is False and again["status"] == "submitted"
    assert again["next"] == am._REVIEW and again["reason"] == "remote_not_found" and again["remote_present"] is False
    readback = service.reconcile_upload("op-up")
    assert readback["status"] == "submitted" and readback["terminal"] is False
    assert len([c for c in calls if c[0] == "upload_file"]) == 1
    assert service.ledger.get(out["key"])["state"] == "submitted"


@pytest.mark.parametrize("state", ["planned", "submitted", "verified", "state_unknown"])
def test_prior_native_key_is_only_reconciled_without_gui(tmp_path, monkeypatch, no_native, state):
    calls = []
    source = up_source(tmp_path)
    described = describe_source(source, REMOTE_DIR)
    service = am.NetdiskAutomation(make_config(tmp_path))
    prior, _ = service.ledger.begin("upload", sha256=described["sha256"], remote_path=described["remote_path"])
    if state != "planned":
        service.ledger.update(prior["key"], state)
    offline_resolve(monkeypatch, calls, {"status": "not_found"})
    out = service.upload("op-up", source, REMOTE_DIR)
    assert out["created"] is True and out["key"] == prior["key"] and out["remote_present"] is False
    assert out["status"] == ("needs_review" if state == "planned" else "submitted") and out["next"] == am._REVIEW
    assert calls == [("offline_resolve", UP_PATH, False)]
    assert service.ledger.get(prior["key"])["state"] == state
    assert service.uploads.inspect("op-up")["receipts"] == []


def test_upload_conflict_and_bad_source_are_typed_before_ownership(tmp_path, monkeypatch, no_native):
    service = am.NetdiskAutomation(make_config(tmp_path))
    with pytest.raises(am.AutomationFailure) as err:
        service.upload("op-up", str(tmp_path / "missing.txt"), REMOTE_DIR)
    assert (err.value.code, err.value.receipt["operation"]) == ("source_missing", "upload_intent")
    assert service.inspect_upload("op-up") is None
    with pytest.raises(am.AutomationFailure) as err:
        service.upload("bad id!", up_source(tmp_path), REMOTE_DIR)
    assert err.value.code == "operation_id_invalid"
    source = up_source(tmp_path)
    service.uploads.begin("op-up", source, REMOTE_DIR)
    with pytest.raises(am.AutomationFailure) as err:
        service.upload("op-up", source, "/other")
    assert (err.value.code, err.value.error_type) == ("operation_conflict", "UploadOperationConflict")


def test_source_changed_under_ownership_is_refused_before_client_submission(tmp_path, monkeypatch):
    calls = []
    source = up_source(tmp_path)
    def change():  # same size, different bytes, after the intent was persisted
        (tmp_path / "src" / "u.txt").write_bytes(b"HELLO")
    service = upload_service(tmp_path, monkeypatch, calls, up_remote(), submitted(), on_enter=change)
    out = service.upload("op-up", source, REMOTE_DIR)
    assert (out["status"], out["reason"], out["next"]) == ("needs_review", "source_changed", am._REVIEW)
    assert not [c for c in calls if c[0] in {"upload_file", "resolve"}] and calls[-1] == ("session_closed",)
    op = service.uploads.inspect("op-up")
    assert op["ledger_key"] is None and op["receipts"][-1]["reason"] == "source_changed"
    assert service.ledger.get(op["native_key"]) is None
    offline_resolve(monkeypatch, calls, {"status": "not_found"})
    with pytest.raises(am.AutomationFailure) as err:  # the changed bytes are a different request
        service.upload("op-up", source, REMOTE_DIR)
    assert err.value.code == "operation_conflict"
    assert service.reconcile_upload("op-up")["status"] == "needs_review"
    assert not [c for c in calls if c[0] == "upload_file"]


# -- remote directory ensure ------------------------------------------------------------


class RecordingDirectories:
    def __init__(self, result=None, raises=None):
        self.calls, self.result, self.raises = [], result, raises

    def _answer(self, name, *a, **k):
        self.calls.append((name, a, k))
        if self.raises is not None:
            raise self.raises
        return self.result

    def ensure(self, *a, **k):
        return self._answer("ensure", *a, **k)

    def inspect(self, *a, **k):
        return self._answer("inspect", *a, **k)

    def reconcile(self, *a, **k):
        return self._answer("reconcile", *a, **k)


def test_directory_store_lives_under_operations_dir(tmp_path, no_native):
    cfg = make_config(tmp_path)
    service = am.NetdiskAutomation(cfg)
    assert service.directories.root == cfg.paths.operations_dir / "directories"


def test_directory_methods_delegate_public_store_once(tmp_path, no_native):
    service = am.NetdiskAutomation(make_config(tmp_path))
    service.directories = store = RecordingDirectories({"status": "completed"})
    ctx, pace = object(), object()
    assert service.ensure_directory("op-d", "/A/b", "/A", "a1", context=ctx, pace=pace) == {"status": "completed"}
    assert service.ensure_directory("op-r", "/b", "/") == {"status": "completed"}
    assert service.inspect_directory("op-d") == {"status": "completed"}
    assert service.reconcile_directory("op-d", context=ctx, pace=pace) == {"status": "completed"}
    assert store.calls == [("ensure", ("op-d", "/A/b", "/A", "a1"), {"context": ctx, "pace": pace}),
                           ("ensure", ("op-r", "/b", "/", None), {"context": None, "pace": None}),
                           ("inspect", ("op-d",), {}),
                           ("reconcile", ("op-d",), {"context": ctx, "pace": pace})]


def test_directory_store_exception_is_typed_failure(tmp_path, no_native):
    service = am.NetdiskAutomation(make_config(tmp_path))
    service.directories = RecordingDirectories(raises=RuntimeError(SECRET))
    for call, operation in ((lambda: service.ensure_directory("op-d", "/A/b", "/A", "a1"), "ensure_directory"),
                            (lambda: service.inspect_directory("op-d"), "inspect_directory"),
                            (lambda: service.reconcile_directory("op-d"), "reconcile_directory")):
        with pytest.raises(am.AutomationFailure) as err:
            call()
        assert err.value.receipt["operation"] == operation and SECRET not in json.dumps(err.value.receipt)


def test_duplicate_directory_ensure_inherits_no_mutation_contract(tmp_path, no_native, monkeypatch):
    from backend.core.baidu_netdisk import directory_operations as dop
    nodes, mutations = {"/A": "a1"}, []

    def resolve(path, expected_id=None, want_dir=None, *, context=None, pace=None):
        if path not in nodes:
            return {"status": "missing", "at": path}
        chain = [{"path": p, "id": nodes[p]} for p in ("/A", "/A/b") if p in nodes and path.startswith(p)]
        return {"status": "resolved", "path": path, "is_root": False, "id": nodes[path], "is_dir": True,
                "chain": chain}

    def ensure(path, anchor_path, expected_anchor_id=None, *, context=None):
        mutations.append(path)
        nodes[path] = "n1"
        return {"status": "created", "path": path, "id": "n1", "steps": [{"status": "created", "id": "n1"}]}
    monkeypatch.setattr(dop.listing, "resolve_remote", resolve)
    monkeypatch.setattr(dop.web_directories, "ensure_directory", ensure)
    service = am.NetdiskAutomation(make_config(tmp_path))
    first = service.ensure_directory("op-d", "/A/b", "/A", "a1")
    assert first["status"] == "completed" and first["target_id"] == "n1"
    assert service.ensure_directory("op-d", "/A/b", "/A", "a1")["status"] == "completed"
    assert service.ensure_directory("op-d", "/A/c", "/A", "a1")["status"] == "conflict"
    assert service.reconcile_directory("op-d")["status"] == "completed"
    assert service.inspect_directory("op-d")["status"] == "found"
    assert service.inspect_directory("op-x")["status"] == "not_found"
    assert service.ensure_directory("op-n", "/A/b", "/A")["reason"] == "expected_anchor_id_required"
    assert mutations == ["/A/b"]
    del nodes["/A/b"]  # removed afterwards: readback is honest and never re-creates
    assert service.reconcile_directory("op-d")["status"] == "needs_review"
    assert mutations == ["/A/b"]
