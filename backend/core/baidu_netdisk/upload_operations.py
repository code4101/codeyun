"""Durable upload intents with read-only remote readback.

An upper layer calls :meth:`UploadOperationStore.begin` with a caller-chosen
``operation_id`` *before* any GUI action.  The source is described through the
public :func:`describe_upload_source` (absolute local path, name, size,
SHA-256, remote path) after link-safe checks; that identity and its
fingerprint are immutable.  The same id with the same request returns the
stored record (``created=False``: do not start again); a different request
raises :class:`UploadOperationConflict`.  The native ledger key the official
client uses for this upload (``transfer_key("upload", sha256, remote_path)``)
is computed and stored up front.

* :meth:`record` attaches whitelisted, typed receipt fields (codes, never
  exception text or local paths) and binds the native key, in one locked
  read-modify-write.
* :meth:`reconcile` reads the native ledger record and resolves the exact
  ``remote_path`` through the public :func:`resolve_remote`.  Only a file at
  exactly that path with the stored size and a resource id is
  ``remote_present``; that is stored once as the terminal local receipt and
  later reads return it without touching the source or the account again.
  ``content_verified`` is always ``False``: a remote listing proves path,
  size and id, never content.  Anything missing or uncertain is
  ``submitted``/``needs_review`` with ``manual_review_do_not_resubmit``;
  absence never authorises a replay.  Reconcile never clicks, types, submits,
  re-hashes the source or writes the ledger.

Records live only under the store's own ``root``; file names are a hash of
the id.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat as stat_mod
import time
import uuid
from pathlib import Path

from filelock import FileLock

from .ledger import TransferLedger, transfer_key
from .listing import resolve_remote
from .local_files import describe_upload_source, validate_file_name, validate_remote_dir
from .operations_evidence import is_link, linked_component

_ID_RE = re.compile(r"^[\w.:@+\-]{1,128}$")
_CODE_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,80}$")
_SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
# Typed receipt whitelist: codes, the native key and remote facts only.
_CODE_FIELDS = {"status", "next", "state", "reason", "key"}
_REMOTE_FIELDS = {"resource_id": str, "remote_size": int, "remote_present": bool, "content_verified": bool}
DO_NOT_RESUBMIT = "manual_review_do_not_resubmit"
REMOTE_PRESENT_NEXT = "done_remote_present_content_unverified"


class UploadOperationConflict(ValueError):
    """The operation id already holds a different request or native key."""


class UploadSourceRejected(ValueError):
    """The source is not a plain regular file reachable without links; ``code`` says why."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fingerprint(request: dict) -> str:
    raw = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def native_upload_key(request: dict) -> str:
    """The official client's ledger key for this upload (see ``NetdiskClient.upload_file``)."""
    return transfer_key("upload", sha256=request["sha256"], remote_path=request["remote_path"])


def _plain_source(source: object) -> Path:
    """Absolute, link-free path of a regular file; raises :class:`UploadSourceRejected`."""
    if not isinstance(source, (str, os.PathLike)) or not os.path.isabs(source):
        raise UploadSourceRejected("source_not_absolute")
    path = Path(os.path.abspath(source))
    try:
        if linked_component(path):
            raise UploadSourceRejected("source_link_or_reparse_point")
        st = os.lstat(path)
    except FileNotFoundError:
        raise UploadSourceRejected("source_missing") from None
    except OSError:
        raise UploadSourceRejected("source_unreadable") from None
    if is_link(st) or not stat_mod.S_ISREG(st.st_mode):
        raise UploadSourceRejected("source_not_regular_file")
    try:
        validate_file_name(path.name)
    except ValueError:
        raise UploadSourceRejected("source_name_invalid") from None
    return path


def describe_source(source: object, remote_dir: str) -> dict:
    """Link-safe :func:`describe_upload_source`; the stored request is exactly its result."""
    path = _plain_source(source)
    try:
        remote = validate_remote_dir(remote_dir)
    except ValueError:
        raise UploadSourceRejected("remote_dir_invalid") from None
    before = os.lstat(path)
    try:
        described = describe_upload_source(path, remote)
    except FileNotFoundError:
        raise UploadSourceRejected("source_missing") from None
    except OSError:
        raise UploadSourceRejected("source_unreadable") from None
    try:
        after = os.lstat(path)
    except OSError:
        raise UploadSourceRejected("source_changed_while_hashing") from None
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) \
            or described["size"] != after.st_size:
        raise UploadSourceRejected("source_changed_while_hashing")
    if os.path.normcase(described["local_path"]) != os.path.normcase(os.fspath(path)):
        raise UploadSourceRejected("source_path_not_canonical")  # resolve() saw something the lexical path hides
    return {k: described[k] for k in ("local_path", "name", "size", "sha256", "remote_dir", "remote_path")}


def _receipt(receipt: object) -> dict:
    if not isinstance(receipt, dict) or not isinstance(receipt.get("status"), str) \
            or not _CODE_RE.match(receipt["status"]):
        raise ValueError("receipt 须含代码形式的 status")
    clean: dict = {}
    for name, value in receipt.items():
        if name in _CODE_FIELDS:
            if isinstance(value, str) and _CODE_RE.match(value):
                clean[name] = value
        elif name in _REMOTE_FIELDS:
            kind = _REMOTE_FIELDS[name]
            if type(value) is kind and (kind is not str or _CODE_RE.match(value)) \
                    and (kind is not int or value >= 0):
                clean[name] = value
    return clean


def _review(status: str, reason: str, **facts) -> dict:
    return {"status": status, "next": DO_NOT_RESUBMIT, "reason": reason, "remote_present": False,
            "content_verified": False, **facts}


class UploadOperationStore:
    def __init__(self, root: str | os.PathLike, ledger: TransferLedger):
        if not isinstance(root, (str, os.PathLike)) or not os.path.isabs(root):
            raise ValueError("root 必须是绝对路径")
        path = Path(os.path.abspath(root))
        if linked_component(path):
            raise ValueError("root 路径链含链接或重解析点")
        path.mkdir(parents=True, exist_ok=True)
        if linked_component(path) or not path.is_dir():
            raise ValueError("root 路径链含链接或重解析点")
        self.root = path
        self.ledger = ledger

    # -- storage ---------------------------------------------------------------

    def _path(self, operation_id: str) -> Path:
        if not isinstance(operation_id, str) or not _ID_RE.match(operation_id):
            raise ValueError("operation_id 须为 1-128 位字母数字或 ._:@+-")
        return self.root / f"upload-{hashlib.sha256(operation_id.encode('utf-8')).hexdigest()[:32]}.json"

    def _lock(self, path: Path) -> FileLock:
        return FileLock(os.fspath(path) + ".lock")

    def _read(self, path: Path) -> dict | None:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def _write(self, path: Path, record: dict) -> None:
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        for attempt in range(50):  # Windows refuses replace while an unlocked reader has the file open
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 49:
                    tmp.unlink(missing_ok=True)
                    raise
                time.sleep(0.02)

    def _transact(self, operation_id: str, change) -> dict:
        """Read, ``change(record)`` and write under one lock; ``change`` returns the history event or None."""
        path = self._path(operation_id)
        with self._lock(path):
            record = self._read(path)
            if record is None:
                raise KeyError("unknown operation_id")
            event = change(record)
            if event is not None:
                record["history"].append({**event, "at": time.time()})
                self._write(path, record)
            return record

    # -- intent ----------------------------------------------------------------

    def begin(self, operation_id: str, source: str | os.PathLike, remote_dir: str) -> tuple[dict, bool]:
        """Persist the immutable upload identity before any GUI action; ``created=False`` means do not start again.

        The source is hashed outside the lock; comparing and writing happen under it,
        so concurrent callers create exactly one record.
        """
        path = self._path(operation_id)
        request = describe_source(source, remote_dir)
        fingerprint = _fingerprint(request)
        with self._lock(path):
            existing = self._read(path)
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    raise UploadOperationConflict("operation_id 已绑定不同上传请求")
                return existing, False
            now = time.time()
            record = {"operation_id": operation_id, "kind": "upload", "request": request,
                      "fingerprint": fingerprint, "native_key": native_upload_key(request), "ledger_key": None,
                      "state": "intent", "receipts": [], "final": None, "created_at": now,
                      "history": [{"event": "begin", "at": now}]}
            self._write(path, record)
            return record, True

    def inspect(self, operation_id: str) -> dict | None:
        """Stored metadata only; never looks at the ledger, the source, the GUI or the network."""
        return self._read(self._path(operation_id))

    def record(self, operation_id: str, receipt: dict, ledger_key: str | None = None) -> dict:
        """Attach a whitelisted receipt; its key must be this upload's native key.

        Key check, binding and append are one locked transaction.  A receipt after the
        terminal receipt keeps the terminal state.
        """
        self._path(operation_id)
        clean = _receipt(receipt)
        key = ledger_key if ledger_key is not None else clean.get("key")
        if ledger_key is not None and (not isinstance(ledger_key, str) or not _CODE_RE.match(ledger_key)):
            raise UploadOperationConflict("ledger_key 不是该上传的原生键")

        def change(op: dict) -> dict:
            if key is not None and key != op["native_key"]:
                raise UploadOperationConflict("ledger_key 不是该上传的原生键")
            entry = {**clean, "at": time.time()}
            op["receipts"].append(entry)
            op["ledger_key"] = key or op["ledger_key"]
            if op["final"] is None:
                op["state"] = "recorded"
            return {"event": "record", "receipt": entry, "ledger_key": op["ledger_key"], "state": op["state"]}

        return self._transact(operation_id, change)

    # -- read-only readback --------------------------------------------------------

    def reconcile(self, operation_id: str, context=None, pace=None) -> dict:
        """Ledger state plus exact remote readback; never GUI input, resubmission, source hashing or ledger writes."""
        op = self.inspect(operation_id)
        if op is None:
            return {"operation_id": operation_id, **_review("unknown_operation", "operation_not_begun")}
        req = op["request"]
        ledger_record = self.ledger.get(op["native_key"])
        base = {"operation_id": operation_id, "native_key": op["native_key"], "ledger_bound": bool(op["ledger_key"]),
                "ledger_state": ledger_record.get("state") if ledger_record else None,
                "remote_path": req["remote_path"], "expected_size": req["size"]}
        if op["final"] is not None:
            return {**base, **op["final"], "terminal": True}
        try:
            found = resolve_remote(req["remote_path"], want_dir=False, context=context, pace=pace)
        except Exception as exc:  # noqa: BLE001 - reported as a code, never as text
            return {**base, **self._unresolved(op, ledger_record, f"resolve_error:{type(exc).__name__}"),
                    "terminal": False}
        evidence = self._judge(req, found)
        if evidence is None:
            status = found.get("status") if isinstance(found, dict) else None
            reason = f"remote_{status}" if isinstance(status, str) and _CODE_RE.match(status) else "remote_unresolved"
            return {**base, **self._unresolved(op, ledger_record, reason), "terminal": False}
        if evidence["status"] != "remote_present":
            return {**base, **evidence, "terminal": False}
        if not _submitted(op, ledger_record):
            # A same-name file without our submission may pre-date this intent.
            return {**base, **evidence, "status": "needs_review", "next": DO_NOT_RESUBMIT,
                    "reason": "remote_present_without_submission", "terminal": False}
        settled = self._transact(operation_id, lambda current: _settle(current, evidence))
        return {**base, **settled["final"], "terminal": True}

    @staticmethod
    def _judge(req: dict, found: object) -> dict | None:
        """``remote_present`` only for exact path + file + size + resource id; None when nothing was resolved."""
        if not isinstance(found, dict) or found.get("status") not in {"resolved", "type_mismatch", "id_mismatch"}:
            return None
        if found.get("status") != "resolved" or found.get("is_dir") is not False:
            return _review("needs_review", "remote_not_a_file")
        rid, size = found.get("id"), found.get("size")
        if found.get("path") != req["remote_path"] or found.get("name") != req["name"]:
            return _review("needs_review", "remote_path_mismatch")
        if type(size) is not int or size != req["size"]:
            return _review("needs_review", "remote_size_mismatch",
                           remote_size=size if type(size) is int else None)
        if not isinstance(rid, str) or not rid or not _CODE_RE.match(rid):
            return _review("needs_review", "remote_resource_id_missing")
        version = found.get("version")
        return {"status": "remote_present", "next": REMOTE_PRESENT_NEXT, "reason": "exact_path_size_id",
                "remote_present": True, "content_verified": False, "verification_level": "remote_path_size_id",
                "resource_id": rid, "remote_size": size, "remote_version": version if type(version) is int else None,
                "local_sha256": req["sha256"] if _SHA256_RE.match(req["sha256"]) else None}

    @staticmethod
    def _unresolved(op: dict, ledger_record: dict | None, reason: str) -> dict:
        """Missing or uncertain: blocking, never a permission to replay."""
        return _review("submitted" if _submitted(op, ledger_record) else "needs_review", reason)


def _submitted(op: dict, ledger_record: dict | None) -> bool:
    """The native ledger or a stored receipt says the irreversible confirm was (maybe) sent."""
    states = {ledger_record.get("state")} if ledger_record else set()
    states |= {r.get(k) for r in op["receipts"] for k in ("state", "status")}
    return bool(states & {"submitted", "state_unknown", "verified"})


def _settle(op: dict, evidence: dict) -> dict | None:
    """Store the first terminal receipt under the lock; later calls change nothing."""
    if op["final"] is not None:
        return None
    op["final"] = {**evidence, "at": time.time()}
    op["state"] = "remote_present"
    return {"event": "remote_present", "final": op["final"], "state": "remote_present"}
