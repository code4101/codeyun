"""Durable, never-replayed remote directory ``ensure`` intents.

``DirectoryOperationStore(root)`` keeps one JSON record per ``operation_id``
under ``root/operations`` (atomic replace, serialised by ``root/.operations.lock``).
The immutable intent and its fingerprint are claimed *before* any account
mutation; the public ``web_directories.ensure_directory`` is called at most
once per operation id, only after ``listing.resolve_remote`` proved the anchor's
exact id and folder type.  A repeated id never mutates: same fingerprint only
reconciles (read-back), a different one is a ``conflict``.  Anything uncertain
after the claim is ``needs_review`` with ``do_not_replay``; there are no
automatic retries or resets.  The first proven target id is recorded and later
read-backs require that id, so a replacement at the same path is refused.
Records hold only paths, ids, status/reason codes and whitelisted provider
cleanup receipts -- never exception text.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from filelock import FileLock, Timeout

from . import listing, web_directories
from .local_files import validate_file_name, validate_remote_dir

_OP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_ROW_ID = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ERROR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_TAB_CLEANUP = re.compile(r"^(closed|still_open|not_run|unverified|failed:[A-Za-z_][A-Za-z0-9_]{0,63})$")
_LISTENER = re.compile(r"^(not_started|starting|started|stopped|stop_failed:[A-Za-z_][A-Za-z0-9_]{0,63})$")
_LOCK_TIMEOUT = 30
_OK_PROVIDER = {"created", "already_exists"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _code(value) -> str | None:
    return value if isinstance(value, str) and _CODE.match(value) else None


def _row_id(value) -> str | None:
    return value if isinstance(value, str) and _ROW_ID.match(value) else None


def _error(exc: BaseException) -> str:
    name = type(exc).__name__
    return name if _ERROR.match(name) else "Exception"


def _fingerprint(intent: dict) -> str:
    blob = json.dumps(intent, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _intent(path, anchor_path, expected_anchor_id) -> dict | str:
    """The canonical intent, or a reason code when the arguments are refused."""
    try:
        if not isinstance(path, str) or not isinstance(anchor_path, str):
            raise ValueError
        if validate_remote_dir(path) != path or validate_remote_dir(anchor_path) != anchor_path:
            raise ValueError  # no silent strip / '//' collapse
    except ValueError:
        return "invalid_path"
    if anchor_path == "/":
        if expected_anchor_id is not None:
            return "root_has_no_id"
    elif _row_id(expected_anchor_id) is None:
        return "expected_anchor_id_required"
    prefix = anchor_path.rstrip("/") + "/"
    if path == anchor_path:
        return "nothing_to_create"
    if not path.startswith(prefix):
        return "outside_anchor"
    try:
        for name in path[len(prefix):].split("/"):
            validate_file_name(name)
    except ValueError:
        return "invalid_path"
    return {"path": path, "anchor_path": anchor_path, "expected_anchor_id": expected_anchor_id}


def _provider_receipt(result) -> dict:
    """Whitelisted ``ensure_directory`` receipt: codes, intent paths, row ids, tab cleanup."""
    if not isinstance(result, dict):
        return {"status": "malformed"}
    out = {"status": _code(result.get("status")) or "malformed"}
    for key in ("reason", "error"):
        value = result.get(key)
        if isinstance(value, str) and (_CODE.match(value) or (key == "error" and _ERROR.match(value))):
            out[key] = value
    if _row_id(result.get("id")):
        out["id"] = result["id"]
    if isinstance(result.get("tab_cleanup"), str) and _TAB_CLEANUP.match(result["tab_cleanup"]):
        out["tab_cleanup"] = result["tab_cleanup"]
    steps = result.get("steps") if isinstance(result.get("steps"), list) else []
    out["steps"] = [{"status": _code(s.get("status")), "id": _row_id(s.get("id"))}
                    for s in steps[:64] if isinstance(s, dict)]
    return out


def _listing_cleanup(result) -> dict | None:
    """Whitelisted ``resolve_remote`` cleanup (listener/tab state, no record path)."""
    cleanup = result.get("cleanup") if isinstance(result, dict) else None
    if not isinstance(cleanup, dict):
        return None
    out = {}
    if isinstance(cleanup.get("listener"), str) and _LISTENER.match(cleanup["listener"]):
        out["listener"] = cleanup["listener"]
    tab = cleanup.get("tab")
    if isinstance(tab, str) and (_TAB_CLEANUP.match(tab) or re.match(r"^close_(not_run|failed:\w{1,64})$", tab)):
        out["tab"] = tab
    if isinstance(cleanup.get("before_preserved"), bool):
        out["before_preserved"] = cleanup["before_preserved"]
    return out


class _Unreadable(Exception):
    pass


class DirectoryOperationStore:
    """Durable ``ensure`` intents under ``root`` (see module docstring)."""

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)

    def _record_path(self, operation_id: str) -> Path:
        return self.root / "operations" / f"{operation_id}.json"

    def _lock(self) -> FileLock:
        self.root.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self.root / ".operations.lock"), timeout=_LOCK_TIMEOUT)

    def _load(self, operation_id: str) -> dict | None:
        path = self._record_path(operation_id)
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            raise _Unreadable from None
        if not isinstance(record, dict) or record.get("operation_id") != operation_id:
            raise _Unreadable
        return record

    def _write(self, record: dict) -> None:
        path = self._record_path(record["operation_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    def _update(self, operation_id: str, change: Callable[[dict], None]) -> dict:
        """Read-modify-write one existing record under the store lock."""
        with self._lock():
            record = self._load(operation_id)
            if record is None:
                raise _Unreadable
            change(record)
            record["updated_at"] = _now()
            self._write(record)
            return record

    def inspect(self, operation_id: str) -> dict:
        """Public metadata of one operation; never touches the account."""
        if not isinstance(operation_id, str) or not _OP_ID.match(operation_id):
            return {"status": "invalid", "reason": "invalid_operation_id"}
        try:
            record = self._load(operation_id)
        except _Unreadable:
            return {"status": "needs_review", "operation_id": operation_id,
                    "reason": "record_unreadable", "do_not_replay": True}
        if record is None:
            return {"status": "not_found", "operation_id": operation_id}
        return {"status": "found", **record}

    @staticmethod
    def _result(record: dict) -> dict:
        check = record.get("last_check") or {}
        if record["phase"] == "anchor_refused":
            anchor = record.get("anchor_check") or {}
            status, reason = anchor.get("status", "refused"), anchor.get("reason")
        else:
            status, reason = check.get("status", "needs_review"), check.get("reason", "not_verified")
        return {"status": status, "reason": reason, "operation_id": record["operation_id"],
                "phase": record["phase"], "target_id": record.get("target_id"),
                "do_not_replay": True, "provider": record.get("provider"), "last_check": check or None}

    def _readback(self, record: dict, context, pace) -> dict:
        """Prove target path/folder type/id and the anchor's exact id in its chain."""
        intent, target_id = record["intent"], record.get("target_id")
        try:
            got = listing.resolve_remote(intent["path"], target_id, want_dir=True, context=context, pace=pace)
        except Exception as exc:  # noqa: BLE001 - reported by type only, never retried
            return {"status": "needs_review", "reason": "readback_exception", "error": _error(exc)}
        check = {"cleanup": _listing_cleanup(got)}
        status = got.get("status") if isinstance(got, dict) else None
        if status == "missing":
            return {**check, "status": "needs_review", "reason": "target_missing"}
        if status == "id_mismatch" and target_id is not None:
            return {**check, "status": "needs_review", "reason": "target_replaced"}
        if status == "type_mismatch":
            return {**check, "status": "needs_review", "reason": "target_not_directory"}
        if status != "resolved":
            return {**check, "status": "needs_review", "reason": f"readback_{_code(status) or 'unknown'}"}
        if got.get("path") != intent["path"] or got.get("is_dir") is not True or _row_id(got.get("id")) is None:
            return {**check, "status": "needs_review", "reason": "target_not_proven"}
        if target_id is not None and got["id"] != target_id:
            return {**check, "status": "needs_review", "reason": "target_replaced"}
        anchor, anchor_id = intent["anchor_path"], intent["expected_anchor_id"]
        if anchor != "/":
            links = [c for c in got.get("chain") or () if isinstance(c, dict) and c.get("path") == anchor]
            if len(links) != 1 or links[0].get("id") != anchor_id:
                return {**check, "status": "needs_review", "reason": "anchor_not_proven"}
        provider_id = (record.get("provider") or {}).get("id")
        if (record.get("provider") or {}).get("status") in _OK_PROVIDER and provider_id != got["id"]:
            return {**check, "status": "needs_review", "reason": "provider_id_mismatch", "id": got["id"]}
        return {**check, "status": "completed", "reason": "target_proven", "id": got["id"]}

    def _record_check(self, operation_id: str, check: dict) -> dict:
        def change(record: dict) -> None:
            proven = check.get("id") if check["status"] == "completed" else None
            if record.get("target_id") is None and proven is not None:
                record["target_id"] = proven  # first proof only; never overwritten
            elif proven is not None and proven != record["target_id"]:
                check.update(status="needs_review", reason="target_replaced")
            record["last_check"] = {**{k: v for k, v in check.items() if k != "id"}, "checked_at": _now()}
        return self._result(self._update(operation_id, change))

    def reconcile(self, operation_id: str, *, context: Callable[[], object] | None = None,
                  pace: Callable[[], None] | None = None) -> dict:
        """Read back one stored intent; never calls the mutator, never resets the record."""
        if not isinstance(operation_id, str) or not _OP_ID.match(operation_id):
            return {"status": "invalid", "reason": "invalid_operation_id"}
        try:
            record = self._load(operation_id)
            if record is None:
                return {"status": "not_found", "operation_id": operation_id}
            if record.get("phase") == "anchor_refused":  # nothing was submitted
                return self._result(record)
            return self._record_check(operation_id, self._readback(record, context, pace))
        except (_Unreadable, Timeout, OSError, KeyError, TypeError):
            return {"status": "needs_review", "operation_id": operation_id,
                    "reason": "record_unreadable", "do_not_replay": True}

    def _claim(self, operation_id: str, intent: dict) -> tuple[str, dict | None]:
        """``("new", record)`` for the single claimer, else ``("duplicate"|"conflict", None)``."""
        fingerprint = _fingerprint(intent)
        with self._lock():
            existing = self._load(operation_id)
            if existing is not None:
                return ("duplicate" if existing.get("fingerprint") == fingerprint else "conflict"), None
            now = _now()
            record = {"operation_id": operation_id, "fingerprint": fingerprint, "intent": intent,
                      "phase": "claimed", "do_not_replay": True, "target_id": None,
                      "anchor_check": None, "provider": None, "last_check": None,
                      "created_at": now, "updated_at": now}
            self._write(record)
            return "new", record

    def _prove_anchor(self, intent: dict, context, pace) -> dict:
        anchor, anchor_id = intent["anchor_path"], intent["expected_anchor_id"]
        try:
            got = listing.resolve_remote(anchor, anchor_id, want_dir=True, context=context, pace=pace)
        except Exception as exc:  # noqa: BLE001 - nothing submitted; type only
            return {"status": "needs_review", "reason": "anchor_exception", "error": _error(exc)}
        check = {"cleanup": _listing_cleanup(got)}
        status = got.get("status") if isinstance(got, dict) else None
        if status != "resolved":
            return {**check, "status": "refused", "reason": f"anchor_{_code(status) or 'unknown'}"}
        if got.get("path") != anchor or got.get("is_dir") is not True or got.get("id") != anchor_id:
            return {**check, "status": "refused", "reason": "anchor_not_proven"}
        return {**check, "status": "ok"}

    def ensure(self, operation_id: str, path: str, anchor_path: str, expected_anchor_id: str | None,
               context: Callable[[], object] | None = None, pace: Callable[[], None] | None = None) -> dict:
        """Ensure ``path`` below the proven anchor, mutating at most once per ``operation_id``.

        ``pace`` paces only the ``listing.resolve_remote`` proofs; the provider
        keeps its own pacing.  Statuses: ``completed`` (target proven, id
        recorded), ``needs_review`` (uncertain; never replayed), ``refused``
        (anchor not proven, nothing submitted), ``conflict`` (id reused for
        another intent), ``invalid``."""
        if not isinstance(operation_id, str) or not _OP_ID.match(operation_id):
            return {"status": "invalid", "reason": "invalid_operation_id"}
        intent = _intent(path, anchor_path, expected_anchor_id)
        if isinstance(intent, str):
            return {"status": "invalid", "reason": intent, "operation_id": operation_id}
        try:
            claim, record = self._claim(operation_id, intent)
        except (_Unreadable, Timeout, OSError):
            return {"status": "needs_review", "operation_id": operation_id,
                    "reason": "record_unreadable", "do_not_replay": True}
        if claim == "conflict":
            return {"status": "conflict", "reason": "operation_id_reused", "operation_id": operation_id,
                    "do_not_replay": True}
        if claim == "duplicate":
            return self.reconcile(operation_id, context=context, pace=pace)
        return self._run_claimed(operation_id, intent, context, pace)

    def _run_claimed(self, operation_id: str, intent: dict, context, pace) -> dict:
        try:
            anchor = self._prove_anchor(intent, context, pace)
            if anchor["status"] != "ok":
                def refuse(record: dict) -> None:
                    record.update(phase="anchor_refused", anchor_check={**anchor, "checked_at": _now()})
                return self._result(self._update(operation_id, refuse))

            def submitting(record: dict) -> None:
                record.update(phase="submitting", anchor_check={**anchor, "checked_at": _now()})
            self._update(operation_id, submitting)  # durable before the single mutation
        except (_Unreadable, Timeout, OSError):
            return {"status": "needs_review", "operation_id": operation_id,
                    "reason": "record_unreadable", "do_not_replay": True}

        try:
            receipt = _provider_receipt(web_directories.ensure_directory(
                intent["path"], intent["anchor_path"], intent["expected_anchor_id"], context=context))
            phase = "submitted"
        except Exception as exc:  # noqa: BLE001 - may have mutated: review, never retry
            receipt, phase = {"status": "exception", "error": _error(exc), "steps": []}, "submit_error"
        try:
            record = self._update(operation_id, lambda r: r.update(phase=phase, provider=receipt))
            return self._record_check(operation_id, self._readback(record, context, pace))
        except (_Unreadable, Timeout, OSError):
            return {"status": "needs_review", "operation_id": operation_id, "reason": "record_unreadable",
                    "do_not_replay": True, "provider": receipt}
