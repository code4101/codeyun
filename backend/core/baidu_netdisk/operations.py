"""Stable operation intents around :class:`TransferLedger` downloads.

An upper layer calls :meth:`OperationStore.begin` with a caller-chosen
``operation_id`` *before* any GUI action.  The normalised request and its
fingerprint are immutable: the same id with the same request returns the
stored record (``created=False``, do not start the business again); a
different request raises :class:`OperationConflict`.  The native ledger keys
the provider may use are stored up front, so a later independent process can
find the transfer after a crash.

* :meth:`inspect` returns stored metadata only (no GUI, network or ledger).
* :meth:`reconcile` reads the ledger and filesystem facts and never clicks,
  navigates, resubmits or moves anything (see :mod:`.operations_evidence`).
  Without trusted SHA-256 content a record not bound by this operation must
  carry the intent's exact ``resource_id``/``resource_version``
  (:func:`provenance_problem`); otherwise it is ``needs_review`` and, through
  reconcile, never recovered.
* :meth:`recover_download` is the one explicit side effect: once reconcile
  proves a single new download is ready, it moves it into ``local_dir``
  (never overwriting) through the public, filesystem-only
  ``NetdiskClient.resume_download``/``resume_download_dir`` with GUI access
  forbidden, then re-reads the result.  Planned or unknown transfers are never
  retried here.

Records live only under the store's own ``root``; file names are a hash of
the id; history is append-only; errors carry codes, never exception text.

Every change (``record`` validation + append, recovery's receipt and final)
is one read-modify-write under the record's lock, so concurrent threads or
processes never lose a receipt or rebind a different ledger key.  Recovery is
additionally serialised by its own ``.recover.lock``; lock order is always
recover lock, then record lock, and ``record`` takes only the record lock.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path, PurePosixPath

from filelock import FileLock

from .ledger import TransferLedger, transfer_key
from .local_files import validate_file_name, validate_remote_dir
from .operations_evidence import reconcile_record, review, verified_evidence

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_ID_RE = re.compile(r"^[\w.:@+\-]{1,128}$")
_RECEIPT_KEYS = {"status", "next", "key", "state", "collect_status", "verification", "content_verified",
                 "target_path", "sha256"}


class OperationConflict(ValueError):
    """The operation id already holds a different request or ledger key."""


class GuiForbidden(RuntimeError):
    """Raised by the recovery guard if anything tries to reach the desktop."""


def _sha256(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        hint = "（疑似 MD5，不能当 SHA-256）" if isinstance(value, str) and _MD5_RE.match(value) else ""
        raise ValueError(f"{field} 必须是 64 位十六进制 SHA-256{hint}")
    return value.upper()


def _size(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} 必须是非负整数")
    return value


def _identity(request: dict) -> dict:
    out = {}
    for name in ("resource_id", "resource_version"):
        value = request.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int))):
            raise ValueError(f"{name} 必须是字符串或整数")
        out[name] = value
    return out


def _manifest(manifest: object) -> dict | None:
    if manifest is None:
        return None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise ValueError("expected_manifest 须为 {files:[{rel,size,sha256?}]}")
    files, seen = [], set()
    for item in manifest["files"]:
        rel = item.get("rel") if isinstance(item, dict) else None
        if not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/"):
            raise ValueError("expected_manifest rel 必须是相对 POSIX 路径")
        for part in rel.split("/"):
            if part in {"", ".", ".."}:
                raise ValueError("expected_manifest rel 含空段、. 或 ..")
            validate_file_name(part)
        if rel.casefold() in seen:
            raise ValueError("expected_manifest rel 重复或大小写冲突")
        seen.add(rel.casefold())
        files.append({"rel": rel, "size": _size(item.get("size"), "expected size"),
                      "sha256": _sha256(item.get("sha256"), "expected sha256")})
    return {"files": sorted(files, key=lambda f: f["rel"]), **_identity(manifest)}


def normalize_request(request: dict) -> dict:
    """Canonical download request; the stored fingerprint is computed from this."""
    if not isinstance(request, dict) or request.get("kind") not in {"download", "download_dir"}:
        raise ValueError("request.kind 必须是 download 或 download_dir")
    remote = validate_remote_dir(request.get("remote_path"))
    name = PurePosixPath(remote).name
    validate_file_name(name)
    local = request.get("local_dir")
    if not isinstance(local, (str, os.PathLike)) or not Path(local).is_absolute():
        raise ValueError("local_dir 必须是绝对路径")
    out = {"kind": request["kind"], "remote_path": remote, "name": name,
           "local_dir": os.fspath(Path(local).resolve()), **_identity(request)}
    if request["kind"] == "download":
        out["expected_size"] = _size(request.get("expected_size"), "expected_size")
        out["expected_sha256"] = _sha256(request.get("expected_sha256"), "expected_sha256")
    else:
        out["expected_manifest"] = _manifest(request.get("expected_manifest"))
    return out


def native_download_keys(request: dict) -> list[dict]:
    """Ledger keys the existing provider may hold for ``request``, most exact first.

    Download: content identity (``download_file``), then the legacy identity-only
    key, then the hash-less key of the same size.  Folder: ``download_dir``.
    """
    req = normalize_request(request)
    identity = {"remote_path": req["remote_path"], "local_dir": req["local_dir"]}
    if req["kind"] == "download_dir":
        return [{"variant": "exact", "key": transfer_key("download_dir", **identity)}]
    content = {"expected_size": req["expected_size"], "expected_sha256": req["expected_sha256"]}
    keys = [{"variant": "exact", "key": transfer_key("download", **identity, **content)},
            {"variant": "legacy", "key": transfer_key("download", **identity)}]
    if req["expected_sha256"]:
        keys.append({"variant": "hashless", "key": transfer_key("download", **identity, expected_size=req["expected_size"],
                                                                 expected_sha256=None)})
    return keys


def trusted_content(request: dict) -> bool:
    """A trusted SHA-256 for the file, or for every file of a non-empty folder manifest."""
    if request["kind"] == "download":
        return bool(request["expected_sha256"])
    files = (request["expected_manifest"] or {}).get("files") or []
    return bool(files) and all(f["sha256"] for f in files)


def provenance(request: dict) -> dict:
    """The request's source identity as stored in ledger facts (strings or None)."""
    rid, version = request.get("resource_id"), request.get("resource_version")
    return {"resource_id": None if rid is None else str(rid),
            "resource_version": None if version is None else str(version)}


def provenance_problem(request: dict, facts: dict, *, own: bool = False) -> str | None:
    """Does a native record prove the request's resource?  None, or a refusal code.

    Trusted SHA-256 content is graded by content, so it needs no source identity.
    Otherwise recorded ``resource_id``/``resource_version`` must equal the
    request's exactly; a record without them proves nothing unless ``own``
    (bound to this operation by its own receipt).
    """
    if trusted_content(request):
        return None
    if "resource_id" not in facts:
        return None if own else "prior_provenance_unknown"
    wanted = provenance(request)
    return None if {k: facts.get(k) for k in wanted} == wanted else "prior_resource_mismatch"


def _fingerprint(request: dict) -> str:
    raw = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class _NoGuiDriver:
    def __getattr__(self, name: str):
        raise GuiForbidden("recovery is filesystem only")


def _forbid_gui() -> None:
    raise GuiForbidden("recovery is filesystem only")


class OperationStore:
    def __init__(self, root: str | os.PathLike, ledger: TransferLedger, *, settle: float = 0.2,
                 poll_attempts: int = 3):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger
        self.settle, self.poll_attempts = settle, poll_attempts

    # -- storage ---------------------------------------------------------------

    def _path(self, operation_id: str) -> Path:
        if not isinstance(operation_id, str) or not _ID_RE.match(operation_id):
            raise ValueError("operation_id 须为 1-128 位字母数字或 ._:@+-")
        return self.root / f"op-{hashlib.sha256(operation_id.encode('utf-8')).hexdigest()[:32]}.json"

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

    def begin(self, operation_id: str, request: dict, ledger_keys: list[str] | None = None) -> tuple[dict, bool]:
        """Persist the intent before any GUI action; ``created=False`` means do not start again."""
        req = normalize_request(request)
        candidates = native_download_keys(req)
        known = {c["key"] for c in candidates}
        if ledger_keys is not None and not set(ledger_keys) <= known:
            raise ValueError("ledger_keys 必须属于该请求的原生候选键")
        path, fingerprint = self._path(operation_id), _fingerprint(req)
        with self._lock(path):
            existing = self._read(path)
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    raise OperationConflict("operation_id 已绑定不同请求")
                return existing, False
            now = time.time()
            record = {"operation_id": operation_id, "request": req, "fingerprint": fingerprint,
                      "ledger_candidates": candidates, "ledger_key": None, "state": "intent",
                      "receipts": [], "final": None, "created_at": now,
                      "history": [{"event": "begin", "at": now}]}
            self._write(path, record)
            return record, True

    def inspect(self, operation_id: str) -> dict | None:
        """Stored metadata only; never looks at the ledger, GUI or network."""
        return self._read(self._path(operation_id))

    def record(self, operation_id: str, receipt: dict, ledger_key: str | None = None) -> dict:
        """Attach a business receipt; its ledger key must be one of the stored candidates.

        Candidate check, bound-key check and append are one locked transaction.
        A receipt after the final result keeps the final state.
        """
        self._path(operation_id)
        if not isinstance(receipt, dict) or not isinstance(receipt.get("status"), str):
            raise ValueError("receipt 须含字符串 status")
        key = ledger_key or receipt.get("key")
        clean = {k: v for k, v in receipt.items() if k in _RECEIPT_KEYS and (v is None or isinstance(v, (str, int, bool)))}

        def change(op: dict) -> dict:
            if key is not None and key not in {c["key"] for c in op["ledger_candidates"]}:
                raise OperationConflict("ledger_key 不属于该操作的候选键")
            if key and op["ledger_key"] and key != op["ledger_key"]:
                raise OperationConflict("operation 已绑定其他 ledger_key")
            entry = {**clean, "at": time.time()}
            op["receipts"].append(entry)
            op["ledger_key"] = key or op["ledger_key"]
            if op["final"] is None:
                op["state"] = "recorded"
            return {"event": "record", "receipt": entry, "ledger_key": op["ledger_key"], "state": op["state"]}

        return self._transact(operation_id, change)

    # -- read-only reconcile ------------------------------------------------------

    def _ledger_record(self, op: dict) -> tuple[dict | None, str | None, dict | None]:
        """(ledger record, variant, problem); a bound key wins, else exact, else a single related one."""
        if op["ledger_key"]:
            variant = next(c["variant"] for c in op["ledger_candidates"] if c["key"] == op["ledger_key"])
            record = self.ledger.get(op["ledger_key"])
            return (record, variant, None) if record else (None, None, review("needs_review", "ledger_record_missing"))
        found = [(c["variant"], r) for c in op["ledger_candidates"] if (r := self.ledger.get(c["key"]))]
        live = [(v, r) for v, r in found if r["state"] not in {"failed", "abandoned"}]
        pool = live or found
        if not pool:
            return None, None, review("needs_review", "ledger_record_missing")
        exact = [(v, r) for v, r in pool if v == "exact"]
        if exact:
            return exact[0][1], "exact", None
        if len(pool) > 1:
            return None, None, review("ambiguous", "multiple_ledger_records", variants=[v for v, _ in pool])
        return pool[0][1], pool[0][0], None

    def reconcile(self, operation_id: str) -> dict:
        """Read-only evidence; never clicks, navigates, resubmits or moves files."""
        op = self.inspect(operation_id)
        if op is None:
            return {"operation_id": operation_id, **review("unknown_operation", "operation_not_begun")}
        record, variant, problem = self._ledger_record(op)
        base = {"operation_id": operation_id, "ledger_key": record["key"] if record else None,
                "ledger_variant": variant, "ledger_state": record["state"] if record else None,
                "request": {k: op["request"].get(k) for k in ("kind", "remote_path", "resource_id", "resource_version")}}
        if (not problem and record["state"] not in {"failed", "abandoned"}
                and op["request"].get("resource_id") is not None):
            # Same path and size is not the same resource: an untrusted record must prove this intent's identity.
            refusal = provenance_problem(op["request"], record.get("facts") or {},
                                         own=op["ledger_key"] == record["key"])
            problem = review("needs_review", refusal) if refusal else None
        try:
            evidence = problem or reconcile_record(op["request"], record)
        except (OSError, ValueError) as exc:
            evidence = review("needs_review", "filesystem_error", error_type=type(exc).__name__)
        return {**base, **evidence}

    # -- explicit filesystem recovery ------------------------------------------------

    def recover_download(self, operation_id: str) -> dict:
        """Move this operation's proven new download into ``local_dir`` (no overwrite, never GUI)."""
        path = self._path(operation_id)
        with FileLock(os.fspath(path) + ".recover.lock"):
            evidence = self.reconcile(operation_id)
            if evidence["status"] == "verified" or not evidence["recoverable"]:
                if evidence["status"] == "verified":
                    self._transact(operation_id, lambda op: _settle(op, evidence["ledger_key"], evidence))
                return {**evidence, "recovered": False}
            from .client import NetdiskClient  # filesystem-only resume methods of the public client
            key = evidence["ledger_key"]
            reserved = []

            def reserve(current: dict) -> dict | None:
                """Bind the selected key before any move; a key bound since reconcile wins."""
                if current["ledger_key"] and current["ledger_key"] != key:
                    return None
                reserved.append(key)
                if current["ledger_key"] == key:
                    return None
                current["ledger_key"] = key
                return {"event": "reserve", "ledger_key": key, "state": current["state"]}

            self._transact(operation_id, reserve)
            if not reserved:
                return {**evidence, **review("needs_review", "ledger_key_conflict"), "recovered": False}
            client = NetdiskClient(ledger=self.ledger, lease_check=_forbid_gui, driver=_NoGuiDriver(),
                                   settle=self.settle, poll_attempts=self.poll_attempts)
            op = self.inspect(operation_id)
            try:
                resume = client.resume_download if op["request"]["kind"] == "download" else client.resume_download_dir
                outcome = resume(key)
            except GuiForbidden:
                outcome = None
                status = "gui_forbidden"
            except Exception as exc:  # noqa: BLE001 - reported as a code, never as text
                outcome, status = None, f"recovery_error:{type(exc).__name__}"
            if outcome is not None:
                status = outcome.status
            receipt = {"status": status, "key": key, "next": getattr(outcome, "next", None)}
            ledger_record = self.ledger.get(key)
            if outcome is not None and ledger_record and ledger_record["state"] == "verified":
                final = verified_evidence(op["request"], ledger_record.get("facts") or {})
            else:
                final = review("needs_review", "recovery_not_completed", resume_status=status)
            settled = {}

            def change(current: dict) -> dict:
                entry = {**receipt, "at": time.time()}
                current["receipts"].append(entry)
                settled.update(_conflict_or(current, key, final))
                current["final"] = _final(settled)
                current["state"] = "verified" if settled["status"] == "verified" else "needs_review"
                return {"event": "recover", "receipt": entry, "ledger_key": current["ledger_key"],
                        "final": current["final"], "state": current["state"]}

            self._transact(operation_id, change)
            return {**evidence, **settled, "recovered": settled["status"] == "verified", "resume_status": status}


def _conflict_or(op: dict, key: str, evidence: dict) -> dict:
    """Guard only: the key is reserved before resume and ``record`` rejects any other key."""
    if op["ledger_key"] and op["ledger_key"] != key:
        return review("needs_review", "ledger_key_conflict")
    return evidence


def _settle(op: dict, key: str | None, evidence: dict) -> dict | None:
    """Store the first verified final under the lock; later calls change nothing."""
    if op["final"] is not None:
        return None
    if key and op["ledger_key"] and op["ledger_key"] != key:
        return None  # reconcile used the bound key, so this only happens if it changed meanwhile
    op["ledger_key"] = op["ledger_key"] or key
    op["final"], op["state"] = _final(evidence), "verified"
    return {"event": "verified", "ledger_key": op["ledger_key"], "final": op["final"], "state": "verified"}


def _final(evidence: dict) -> dict:
    facts = evidence.get("evidence") or {}
    return {"status": evidence["status"], "verification_level": facts.get("verification_level"),
            "local_sha256": facts.get("local_sha256"), "target_path": facts.get("target_path"),
            "manifest_sha256": (facts.get("check") or {}).get("manifest_sha256"), "at": time.time()}
