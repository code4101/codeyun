"""Bounded Baidu Netdisk automation service over the existing providers.

:class:`NetdiskAutomation` wires one :class:`TransferLedger`, :class:`OperationStore`
and :class:`AssetStore` from ``config.paths`` and exposes:

* ``inspect`` / ``reconcile`` / ``recover`` / ``verify``: filesystem only; they
  never open a native session, browser or GUI.
* ``list_directory`` / ``resolve``: the public listing provider, called only
  when requested (the caller may pass ``context``/``pace``).
* ``native()``: validates every configured scene (window role, profile) before
  any ownership, then holds one ``NativeAccountSession(with_browser=True)`` and
  one :class:`AssetNetdiskClient` sharing ledger, store, profile and scenes,
  whose ``canonical_listing`` borrows that session's browser and pacing.
* ``download``: one file.  Request and destination are validated and the
  intent persisted (``OperationStore.begin``) before any ownership; a repeated
  intent is answered by the read-only ``reconcile`` only.  A new intent refuses
  prior native records whose provenance cannot prove the resource (without a
  trusted SHA-256), proves the exact remote ID/path/type/size through
  ``resolve_remote`` on the owned browser, then calls ``download_file`` once;
  the native result is recorded under its key and re-read by ``reconcile``.
* ``download_dir``: one folder, same order.  The caller's trusted manifest is
  required and validated by ``normalize_request(kind=download_dir)`` together
  with destination and identity (a manifest claiming another resource is
  refused) before ``OperationStore.begin``; a repeated intent only reconciles.
  A prior native folder key is reused only with matching provenance or a
  manifest whose every file has a trusted SHA-256; the exact remote folder
  ID/path/type(/version) is proven on the owned browser, then the public
  ``download_dir`` is called once.  The collected tree is graded against the
  caller manifest (``content_verified`` only when every file has a trusted
  SHA-256); the receipt may carry the local manifest hash and counts only.
* ``upload``: one file into an existing remote directory.  The intent and its
  native ledger key are persisted (``UploadOperationStore.begin`` under
  ``operations_dir/uploads``) before any ownership; a repeated intent, or a
  new one whose native key already has a live ledger record, is answered by
  ``reconcile_upload`` only (no GUI).  Otherwise the source is re-described
  under ownership (a changed source is refused), ``upload_file`` is called once
  with ``create_missing=False``, the typed outcome recorded, and a submitted
  upload re-read on the owned browser.  ``inspect_upload`` /
  ``reconcile_upload`` never open a native session; their receipts carry
  codes, keys and remote facts, never the local path or source hash.

Any provider exception becomes :class:`AutomationFailure` carrying a sanitized
receipt (case id, code, error type and capture ids/hashes from typed facts;
never exception text, OCR or image bytes).  The receipt is written atomically
to ``failures_dir``, enqueued into the :class:`MaintenanceQueue` under
``root/maintenance`` and handed to ``on_failure``; a failing queue or hook only
adds its error type to the receipt and never replaces the failure.  Operations
that end in a durable unknown/review readback are not failures and are not queued.
``list_maintenance`` / ``inspect_maintenance`` read the queue; only an explicit
``dispatch_maintenance(case_id, workspace_dir)`` may reach the CC dispatcher,
under the queue's threshold and single-claim contract.  Nothing else calls a model.
``ensure_directory`` / ``inspect_directory`` / ``reconcile_directory`` delegate
to :class:`DirectoryOperationStore` under ``operations_dir/directories`` (one
mutation per operation id, repeats only read back); they never open a native session.
Directory uploads are not part of this service yet.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from contextlib import ExitStack, contextmanager
from typing import Callable, Iterable, Iterator

from . import listing
from .asset_client import AssetNetdiskClient, scene_window
from .assets import AssetStore
from .automation_config import AutomationConfig, DestinationError
from .directory_operations import DirectoryOperationStore
from .ledger import TransferLedger
from .maintenance import MaintenanceQueue
from .operations import OperationConflict, OperationStore, native_download_keys, normalize_request
from .ownership import NativeAccountSession
from .upload_operations import UploadOperationConflict, UploadOperationStore, UploadSourceRejected, describe_source
from .verification import verify_local

DEFAULT_OWNER = "baidu-netdisk-automation"
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_HEX = re.compile(r"^[0-9a-f]{16,64}$")
_SHA256 = re.compile(r"^[0-9A-F]{64}$")
_EVIDENCE_KEYS = ("capture_id", "capture_sha256", "evidence_capture_id", "evidence_capture_sha256",
                  "observation_id")
# Typed provider errors whose code is stable even though they carry no ``code``.
_TYPED_CODES = ((OperationConflict, "operation_conflict"), (UploadOperationConflict, "operation_conflict"),
                (DestinationError, "destination_invalid"))
# Native results after which the local artifact is re-read against the request.
_LANDED = {"downloaded", "already_downloaded", "already_verified"}
# Native upload results after which the remote is re-read (state_unknown stays as reported).
_UPLOAD_SENT = {"submitted", "remote_present", "already_submitted", "already_remote_present"}
# Ledger states the official client itself would start again (see ``TransferLedger.begin``).
_RESTARTABLE = {"failed", "abandoned"}
_REVIEW = "manual_review_do_not_resubmit"
# Integer counts of ``verify_local`` that a folder receipt may carry.
_COUNT_KEYS = ("files", "partial", "unstable", "expected", "missing", "extra", "size_mismatch", "hash_mismatch",
               "without_trusted_sha256", "content_matched")


class AutomationFailure(RuntimeError):
    """Sanitized provider failure; ``receipt`` is exactly what was persisted."""

    def __init__(self, receipt: dict):
        super().__init__(receipt["code"])
        self.receipt = receipt
        self.case_id, self.code, self.error_type = receipt["case_id"], receipt["code"], receipt["error_type"]


class _Refused(Exception):
    def __init__(self, code: str, **facts: object):
        super().__init__(code)
        self.code, self.facts = code, facts


def _receipt(operation: str, exc: BaseException) -> dict:
    code = getattr(exc, "code", None)
    code = next((c for t, c in _TYPED_CODES if isinstance(exc, t)), code)
    facts = getattr(exc, "facts", None)
    facts = facts if isinstance(facts, dict) else {}
    return {"case_id": uuid.uuid4().hex, "operation": operation,
            "code": code if isinstance(code, str) and _CODE.match(code) else "provider_error",
            "error_type": type(exc).__name__, "at": time.time(),
            "evidence": {k: facts[k] for k in _EVIDENCE_KEYS
                         if isinstance(facts.get(k), str) and _HEX.match(facts[k])}}


def _provenance(request: dict) -> dict:
    version = request["resource_version"]
    return {"resource_id": str(request["resource_id"]), "resource_version": None if version is None else str(version)}


def _trusted(request: dict) -> bool:
    """A trusted SHA-256 for the file, or for every file of a non-empty folder manifest."""
    if request["kind"] == "download":
        return bool(request["expected_sha256"])
    files = (request["expected_manifest"] or {}).get("files") or []
    return bool(files) and all(f["sha256"] for f in files)


def _bound_manifest(manifest: object, resource_id: object, resource_version: object) -> dict:
    """The caller manifest with the request identity; a manifest claiming another resource is refused."""
    if not isinstance(manifest, dict):
        raise _Refused("manifest_invalid")
    out = dict(manifest)
    for name, value in (("resource_id", resource_id), ("resource_version", resource_version)):
        claimed = manifest.get(name)
        if claimed is None:
            out[name] = value
        elif value is None or isinstance(claimed, bool) or str(claimed) != str(value):
            raise _Refused("manifest_identity_mismatch")
    return out


def _preflight_problem(remote: object, request: dict) -> str | None:
    """The exact object at the path must be the requested file/folder (MD5 is never taken as SHA-256)."""
    folder = request["kind"] == "download_dir"
    if not isinstance(remote, dict) or remote.get("status") != "resolved" or remote.get("is_root"):
        return "remote_not_resolved"
    if remote.get("id") is None or str(remote["id"]) != str(request["resource_id"]):
        return "remote_id_mismatch"
    if remote.get("path") != request["remote_path"]:
        return "remote_path_mismatch"
    if remote.get("is_dir") is not folder:
        return "remote_type_mismatch"
    size = remote.get("size")
    if not folder and (isinstance(size, bool) or not isinstance(size, int) or size != request["expected_size"]):
        return "remote_size_mismatch"
    wanted = _provenance(request)["resource_version"]
    if wanted is not None and (remote.get("version") is None or str(remote["version"]) != wanted):
        return "remote_version_mismatch"
    return None


def _manifest_facts(check: object) -> dict:
    """Local manifest hash and integer counts of a folder check (never names, paths or errors)."""
    if not isinstance(check, dict):
        return {}
    out = {}
    if isinstance(check.get("manifest_sha256"), str) and _SHA256.match(check["manifest_sha256"]):
        out["manifest_sha256"] = check["manifest_sha256"]
    counts = check.get("counts")
    if isinstance(counts, dict):
        out["counts"] = {k: counts[k] for k in _COUNT_KEYS if type(counts.get(k)) is int}
    return out


def _public(operation_id: str, created: bool, status: object, next_step: object, key: object,
            facts: dict | None = None, kind: str = "download") -> dict:
    """Whitelisted receipt: codes, key and trusted target facts only (no native error, exception or OCR text)."""
    facts = facts or {}
    level = facts.get("verification_level") if status == "verified" else None
    level = level if level in {"content_verified", "structure_verified"} else "none"
    out = {"operation_id": operation_id, "created": created,
           "status": status if isinstance(status, str) and _CODE.match(status) else "needs_review",
           "next": next_step if isinstance(next_step, str) and _CODE.match(next_step) else _REVIEW,
           "key": key if isinstance(key, str) and _HEX.match(key) else None,
           "verification_level": level, "content_verified": level == "content_verified"}
    if isinstance(facts.get("reason"), str) and _CODE.match(facts["reason"]):
        out["reason"] = facts["reason"]
    target = facts.get("target_path")
    if level != "none" and kind == "download":
        size, digest = facts.get("size"), facts.get("local_sha256")
        out.update(size=size if isinstance(size, int) and not isinstance(size, bool) else None,
                   sha256=digest if isinstance(digest, str) and _SHA256.match(digest) else None,
                   target_path=target if isinstance(target, str) else None)
    elif kind == "download_dir":
        if level != "none":
            out["target_path"] = target if isinstance(target, str) else None
        out.update(_manifest_facts(facts.get("check")))
    return out


def _readback(operation_id: str, created: bool, evidence: dict, kind: str = "download") -> dict:
    return _public(operation_id, created, evidence.get("status"), evidence.get("next"), evidence.get("ledger_key"),
                   evidence.get("evidence"), kind)


def _upload_facts(evidence: dict) -> dict:
    """Whitelisted remote facts of an upload readback (never the local path, size or source hash)."""
    present = evidence.get("remote_present") is True and evidence.get("status") == "remote_present"
    out = {"remote_present": present, "content_verified": False,
           "verification_level": "remote_path_size_id" if present else "none"}
    for name in ("reason", "ledger_state"):
        if isinstance(evidence.get(name), str) and _CODE.match(evidence[name]):
            out[name] = evidence[name]
    rid, size = evidence.get("resource_id"), evidence.get("remote_size")
    if isinstance(rid, str) and re.fullmatch(r"[A-Za-z0-9_.:\-]{1,80}", rid):
        out["resource_id"] = rid
    if type(size) is int and size >= 0:
        out["remote_size"] = size
    if isinstance(evidence.get("terminal"), bool):
        out["terminal"] = evidence["terminal"]
    return out


def _upload_public(operation_id: str, created: bool, op: dict, evidence: dict) -> dict:
    status, next_step = evidence.get("status"), evidence.get("next")
    return {"operation_id": operation_id, "created": created,
            "status": status if isinstance(status, str) and _CODE.match(status) else "needs_review",
            "next": next_step if isinstance(next_step, str) and _CODE.match(next_step) else _REVIEW,
            "key": op["native_key"], "remote_path": op["request"]["remote_path"], **_upload_facts(evidence)}


def _review_facts(reason: str) -> dict:
    return {"status": "needs_review", "next": _REVIEW, "reason": reason}


def _source_problem(request: dict) -> str | None:
    """The source re-described now must still be exactly the persisted intent."""
    try:
        current = describe_source(request["local_path"], request["remote_dir"])
    except UploadSourceRejected as exc:
        return exc.code if _CODE.match(exc.code) else "source_rejected"
    return None if current == request else "source_changed"


class NetdiskAutomation:
    def __init__(self, config: AutomationConfig, on_failure: Callable[[dict], object] | None = None,
                 maintenance: MaintenanceQueue | None = None):
        self.config, self.on_failure = config, on_failure
        paths = config.paths
        self.ledger = TransferLedger(paths.transfers_file)
        self.operations = OperationStore(paths.operations_dir, self.ledger)
        self.uploads = UploadOperationStore(paths.operations_dir / "uploads", self.ledger)
        self.directories = DirectoryOperationStore(paths.operations_dir / "directories")
        self.assets = AssetStore(paths.assets_dir)
        self.maintenance = maintenance if maintenance is not None else MaintenanceQueue(paths.root / "maintenance")

    # -- failures ------------------------------------------------------------

    def _persist(self, receipt: dict) -> None:
        folder = self.config.paths.failures_dir
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f".{receipt['case_id']}.{uuid.uuid4().hex}.tmp"
        with open(tmp, "x", encoding="utf-8") as fh:
            json.dump(receipt, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, folder / f"{receipt['case_id']}.json")

    def _fail(self, operation: str, exc: BaseException) -> AutomationFailure:
        receipt = _receipt(operation, exc)
        self._persist(receipt)
        clean = dict(receipt)  # Queue and hook both see the sanitized receipt, never each other's errors.
        try:
            self.maintenance.enqueue(dict(clean))
        except Exception as queue_exc:  # noqa: BLE001 - the original failure always wins
            receipt["maintenance_error_type"] = type(queue_exc).__name__
            self._persist(receipt)
        if self.on_failure is not None:
            try:
                self.on_failure(dict(clean))
            except Exception as hook_exc:  # noqa: BLE001 - the original failure always wins
                receipt["hook_error_type"] = type(hook_exc).__name__
                self._persist(receipt)
        return AutomationFailure(receipt)

    # -- maintenance (explicit only; never guarded so the queue cannot feed itself) --

    def list_maintenance(self) -> list[dict]:
        return self.maintenance.list()

    def inspect_maintenance(self, case_id: str) -> dict | None:
        return self.maintenance.inspect(case_id)

    def dispatch_maintenance(self, case_id: str, workspace_dir) -> dict:
        """The only path to the dispatcher: queue threshold/claim contract, never retried."""
        return self.maintenance.dispatch(case_id, workspace_dir)

    @contextmanager
    def _guard(self, operation: str) -> Iterator[None]:
        try:
            yield
        except AutomationFailure:
            raise
        except Exception as exc:  # noqa: BLE001 - reported as a typed receipt, never as text
            raise self._fail(operation, exc) from None

    # -- offline (filesystem only) ---------------------------------------------

    def inspect(self, operation_id: str) -> dict | None:
        with self._guard("inspect"):
            return self.operations.inspect(operation_id)

    def reconcile(self, operation_id: str) -> dict:
        with self._guard("reconcile"):
            return self.operations.reconcile(operation_id)

    def recover(self, operation_id: str) -> dict:
        with self._guard("recover"):
            return self.operations.recover_download(operation_id)

    def verify(self, destination: str, expected_manifest: dict | None = None) -> dict:
        """``verify_local`` of ``local_root/destination`` (traversal refused)."""
        with self._guard("verify"):
            return verify_local(self.config.resolve_destination(destination), expected_manifest)

    # -- file download -------------------------------------------------------------

    def download(self, operation_id: str, *, remote_path: str, resource_id: str | int, expected_size: int,
                 destination: str, expected_sha256: str | None = None, resource_version: str | int | None = None,
                 owner: str = DEFAULT_OWNER, lease_token: str | None = None) -> dict:
        """One file into ``local_root/destination``; a repeated ``operation_id`` only reconciles."""
        with self._guard("download_intent"):
            request = self._download_request(operation_id, remote_path, resource_id, expected_size, destination,
                                             expected_sha256, resource_version)
            _, created = self.operations.begin(operation_id, request)
        if not created:
            with self._guard("download_reconcile"):
                return _readback(operation_id, False, self.operations.reconcile(operation_id))
        with self._guard("download_provenance"):
            problem = self._unproven_prior(request)
            if problem:
                return self._refuse(operation_id, problem)
        with self.native(owner, lease_token=lease_token) as ctx:
            account = ctx.client.account
            with self._guard("download_preflight"):
                remote = listing.resolve_remote(request["remote_path"], str(request["resource_id"]), False,
                                                context=account.browser_context, pace=account.pace)
                problem = _preflight_problem(remote, request)
                if problem:
                    return self._refuse(operation_id, problem)
            with self._guard("download_submit"):
                outcome = ctx.client.download_file(
                    request["remote_path"], request["local_dir"], client_download_dir=self.config.client_download_dir,
                    expected_size=request["expected_size"], expected_sha256=request["expected_sha256"])
        with self._guard("download_record"):
            return self._settle_download(operation_id, request, outcome)

    def _local_dir(self, operation_id: str, resource_id: object, destination: str) -> str:
        """Operation id, resource id and destination checks shared by file and folder requests."""
        try:
            self.operations.inspect(operation_id)
        except ValueError:
            raise _Refused("operation_id_invalid") from None
        if isinstance(resource_id, bool) or not isinstance(resource_id, (str, int)) \
                or (isinstance(resource_id, str) and (not resource_id or resource_id.strip() != resource_id)):
            raise _Refused("resource_id_invalid")
        return os.fspath(self.config.resolve_destination(destination))

    def _download_request(self, operation_id: str, remote_path: str, resource_id: object, expected_size: object,
                          destination: str, expected_sha256: object, resource_version: object) -> dict:
        """Normalised request, or a typed refusal; nothing here touches the ledger, GUI or network."""
        local_dir = self._local_dir(operation_id, resource_id, destination)
        try:
            return normalize_request({"kind": "download", "remote_path": remote_path, "local_dir": local_dir,
                                      "expected_size": expected_size, "expected_sha256": expected_sha256,
                                      "resource_id": resource_id, "resource_version": resource_version})
        except ValueError:
            raise _Refused("request_invalid") from None

    def _unproven_prior(self, request: dict) -> str | None:
        """Without trusted SHA-256s a live native record must prove this resource (the key has no ID)."""
        if _trusted(request):
            return None
        wanted = _provenance(request)
        for candidate in native_download_keys(request):
            record = self.ledger.get(candidate["key"])
            if not record or record["state"] in {"failed", "abandoned"}:
                continue
            facts = record.get("facts") or {}
            if "resource_id" not in facts:
                return "prior_provenance_unknown"
            if {k: facts.get(k) for k in wanted} != wanted:
                return "prior_resource_mismatch"
        return None

    def _refuse(self, operation_id: str, reason: str) -> dict:
        self.operations.record(operation_id, {"status": "needs_review", "next": _REVIEW})
        return {**_public(operation_id, True, "needs_review", _REVIEW, None), "reason": reason}

    def _settle_download(self, operation_id: str, request: dict, outcome) -> dict:
        """Bind the native result to the intent once, then re-read a landed artifact against the request."""
        self.operations.record(operation_id, {"status": outcome.status, "next": outcome.next, "key": outcome.key,
                                              "collect_status": (outcome.facts or {}).get("collect_status")},
                               ledger_key=outcome.key)
        if outcome.status not in _LANDED:
            return _public(operation_id, True, outcome.status, outcome.next, outcome.key)
        evidence = self.operations.reconcile(operation_id)
        public = _readback(operation_id, True, evidence, request["kind"])
        if public["status"] == "verified":
            record = self.ledger.get(public["key"])
            # Only a transfer collected by this call proves which resource the key now holds.
            if outcome.status == "downloaded" and record and record["state"] == "verified" \
                    and "resource_id" not in (record.get("facts") or {}):
                self.ledger.update(public["key"], "verified", **_provenance(request))
        self.operations.record(operation_id, {"status": public["status"], "next": public["next"], "key": public["key"],
                                              "verification": public["verification_level"],
                                              "content_verified": public["content_verified"],
                                              "target_path": public.get("target_path"), "sha256": public.get("sha256")},
                               ledger_key=public["key"])
        return public

    # -- folder download -----------------------------------------------------------

    def download_dir(self, operation_id: str, *, remote_path: str, resource_id: str | int, destination: str,
                     expected_manifest: dict, resource_version: str | int | None = None,
                     owner: str = DEFAULT_OWNER, lease_token: str | None = None) -> dict:
        """One folder into ``local_dir/<name>`` graded against the caller manifest; a repeat only reconciles."""
        with self._guard("download_dir_intent"):
            request = self._download_dir_request(operation_id, remote_path, resource_id, destination,
                                                 expected_manifest, resource_version)
            _, created = self.operations.begin(operation_id, request)
        if not created:
            with self._guard("download_dir_reconcile"):
                return _readback(operation_id, False, self.operations.reconcile(operation_id), "download_dir")
        with self._guard("download_dir_provenance"):
            problem = self._unproven_prior(request)
            if problem:
                return self._refuse(operation_id, problem)
        with self.native(owner, lease_token=lease_token) as ctx:
            account = ctx.client.account
            with self._guard("download_dir_preflight"):
                remote = listing.resolve_remote(request["remote_path"], str(request["resource_id"]), True,
                                                context=account.browser_context, pace=account.pace)
                problem = _preflight_problem(remote, request)
                if problem:
                    return self._refuse(operation_id, problem)
            with self._guard("download_dir_submit"):
                outcome = ctx.client.download_dir(request["remote_path"], request["local_dir"],
                                                  client_download_dir=self.config.client_download_dir)
        with self._guard("download_dir_record"):
            return self._settle_download(operation_id, request, outcome)

    def _download_dir_request(self, operation_id: str, remote_path: str, resource_id: object, destination: str,
                              expected_manifest: object, resource_version: object) -> dict:
        """Normalised folder request with a required, identity-bound manifest; no ledger, GUI or network."""
        local_dir = self._local_dir(operation_id, resource_id, destination)
        if expected_manifest is None:
            raise _Refused("manifest_required")
        base = {"kind": "download_dir", "remote_path": remote_path, "local_dir": local_dir,
                "resource_id": resource_id, "resource_version": resource_version}
        try:
            normalize_request({**base, "expected_manifest": None})
        except ValueError:
            raise _Refused("request_invalid") from None
        manifest = _bound_manifest(expected_manifest, resource_id, resource_version)
        try:
            request = normalize_request({**base, "expected_manifest": manifest})
        except ValueError:
            raise _Refused("manifest_invalid") from None
        if not request["expected_manifest"]["files"]:
            raise _Refused("manifest_empty")
        return request

    # -- file upload ---------------------------------------------------------------

    def inspect_upload(self, operation_id: str) -> dict | None:
        """Stored upload intent as codes, keys and remote facts; never the ledger, source, GUI or network."""
        with self._guard("inspect_upload"):
            op = self.uploads.inspect(operation_id)
            if op is None:
                return None
            receipts = [{k: r[k] for k in ("status", "next", "reason", "state", "key")
                         if isinstance(r.get(k), str) and _CODE.match(r[k])} for r in op["receipts"]]
            final = op["final"]
            return {"operation_id": operation_id, "kind": "upload",
                    "state": op["state"] if _CODE.match(str(op["state"])) else "needs_review",
                    "native_key": op["native_key"], "ledger_key": op["ledger_key"],
                    "remote_path": op["request"]["remote_path"], "receipts": receipts,
                    "final": None if final is None else {"status": final.get("status"), **_upload_facts(final)}}

    def reconcile_upload(self, operation_id: str) -> dict:
        """Read-only readback in the listing provider's own tab; never a native session or resubmission."""
        with self._guard("upload_reconcile"):
            return self._upload_readback(operation_id, False)

    def _upload_readback(self, operation_id: str, created: bool, context=None, pace=None) -> dict:
        op = self.uploads.inspect(operation_id)
        evidence = self.uploads.reconcile(operation_id, context=context, pace=pace)
        if op is None:
            return {"operation_id": operation_id, "created": False, "status": "unknown_operation", "next": _REVIEW,
                    "key": None, **_upload_facts(evidence)}
        return _upload_public(operation_id, created, op, evidence)

    def upload(self, operation_id: str, source: str, remote_dir: str, *, owner: str = DEFAULT_OWNER,
               lease_token: str | None = None) -> dict:
        """One file into the existing ``remote_dir``; a repeated ``operation_id`` or live native key only reconciles."""
        with self._guard("upload_intent"):
            try:
                self.uploads.inspect(operation_id)
            except ValueError:
                raise _Refused("operation_id_invalid") from None
            op, created = self.uploads.begin(operation_id, source, remote_dir)
        if not created:
            with self._guard("upload_reconcile"):
                return self._upload_readback(operation_id, False)
        with self._guard("upload_prior"):
            prior = self.ledger.get(op["native_key"])
        if prior is not None and prior.get("state") not in _RESTARTABLE:
            # The official client already holds this key: never open the GUI, only read back.
            with self._guard("upload_reconcile"):
                return self._upload_readback(operation_id, True)
        with self.native(owner, lease_token=lease_token) as ctx:
            with self._guard("upload_source"):
                problem = _source_problem(op["request"])
                if problem:
                    self.uploads.record(operation_id, {"status": "needs_review", "next": _REVIEW, "reason": problem})
                    return _upload_public(operation_id, True, op, _review_facts(problem))
            # Submission happens once here; any later state is read back, never replayed.
            with self._guard("upload_submit"):
                outcome = ctx.client.upload_file(op["request"]["local_path"], op["request"]["remote_dir"],
                                                 create_missing=False)
            with self._guard("upload_record"):
                settled = self._record_upload(operation_id, op, outcome)
            if settled is not None:
                return settled
            account = ctx.client.account
            with self._guard("upload_reconcile"):
                return self._upload_readback(operation_id, True, context=account.browser_context, pace=account.pace)

    def _record_upload(self, operation_id: str, op: dict, outcome) -> dict | None:
        """Bind the typed native outcome once; None means the upload was (maybe) sent and needs readback."""
        reason = (outcome.facts or {}).get("reason")
        if outcome.key != op["native_key"]:
            # The client hashed different bytes than the intent: block, never bind a foreign key.
            self.uploads.record(operation_id, {"status": "needs_review", "next": _REVIEW,
                                               "reason": "native_key_mismatch"})
            return _upload_public(operation_id, True, op, _review_facts("native_key_mismatch"))
        self.uploads.record(operation_id, {"status": outcome.status, "next": outcome.next, "reason": reason,
                                           "key": outcome.key}, ledger_key=outcome.key)
        if outcome.status in _UPLOAD_SENT:
            return None
        return _upload_public(operation_id, True, op, {"status": outcome.status, "next": outcome.next,
                                                       "reason": reason})

    # -- remote directory ensure (public DirectoryOperationStore; never a native session) --

    def ensure_directory(self, operation_id: str, path: str, anchor_path: str, expected_anchor_id: str | None = None,
                         context: Callable[[], object] | None = None, pace: Callable[[], None] | None = None) -> dict:
        """At most one mutation per ``operation_id``; a repeat only reads back (provider contract)."""
        with self._guard("ensure_directory"):
            return self.directories.ensure(operation_id, path, anchor_path, expected_anchor_id,
                                           context=context, pace=pace)

    def inspect_directory(self, operation_id: str) -> dict:
        with self._guard("inspect_directory"):
            return self.directories.inspect(operation_id)

    def reconcile_directory(self, operation_id: str, context: Callable[[], object] | None = None,
                            pace: Callable[[], None] | None = None) -> dict:
        with self._guard("reconcile_directory"):
            return self.directories.reconcile(operation_id, context=context, pace=pace)

    # -- explicit listing --------------------------------------------------------

    def list_directory(self, path: str, expected_id: str | None = None, *,
                       context: Callable[[], object] | None = None, pace: Callable[[], object] | None = None) -> dict:
        with self._guard("list_directory"):
            return listing.list_directory(path, expected_id, context=context, pace=pace)

    def resolve(self, path: str, expected_id: str | None = None, want_dir: bool | None = None, *,
                context: Callable[[], object] | None = None, pace: Callable[[], object] | None = None) -> dict:
        with self._guard("resolve"):
            return listing.resolve_remote(path, expected_id, want_dir, context=context, pace=pace)

    # -- native ----------------------------------------------------------------

    def _validate_scenes(self) -> None:
        for scene_id in self.config.scene_ids:
            try:
                body = self.assets.get_scene(scene_id)["scene"]
                scene_window(body)
            except ValueError:
                raise _Refused("scene_invalid") from None
            if body.get("profile") != self.config.profile:
                raise _Refused("scene_profile_mismatch")

    @contextmanager
    def native(self, owner: str = DEFAULT_OWNER, *, lease_token: str | None = None) -> Iterator["NativeContext"]:
        """Validate scenes, then own the account browser + GUI lease for one client."""
        with self._guard("native_validate"):
            self._validate_scenes()
        stack = ExitStack()
        try:
            with self._guard("native_open"):
                account = stack.enter_context(NativeAccountSession(owner, lease_token=lease_token, with_browser=True))
                client = AssetNetdiskClient(
                    ledger=self.ledger, store=self.assets, profile=self.config.profile,
                    scene_ids=self.config.scene_ids, account=account,
                    canonical_listing=lambda path: listing.list_directory(
                        path, context=account.browser_context, pace=account.pace))
            yield NativeContext(self, client)
        finally:
            with self._guard("native_close"):
                stack.close()


class NativeContext:
    """Observation and capability-bound navigation through the owned client."""

    def __init__(self, service: NetdiskAutomation, client: AssetNetdiskClient):
        self.service, self.client = service, client

    def observe(self, role: str = "main") -> dict:
        with self.service._guard("observe"):
            return self.client.observe_assets(role=role)

    def action(self, scene_id: str, shape_id: str, expected: Iterable[str] | None = None,
               discovery: bool = False) -> dict:
        with self.service._guard("action"):
            return self.client.action_shape(scene_id, shape_id, expected_scene_ids=expected, discovery=discovery)
