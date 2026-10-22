"""Durable queue from repeated sanitized automation failures to local AI maintenance.

:class:`MaintenanceQueue` stores one case per ``operation``/``code``/``error_type``
signature under ``root``.  ``enqueue`` accepts only the typed fields of an
``AutomationFailure`` receipt (``case_id``, ``operation``, ``code``, ``error_type``,
``at`` and hex ``evidence`` ids/hashes); everything else - exception text, local
paths, OCR, image bytes, hook errors - is dropped.  A repeated source ``case_id``
is idempotent.  ``enqueue`` never calls a model.

``dispatch`` is only ever called explicitly.  Once a case reaches ``threshold``
it persists a claim *before* calling the public ``dispatch_agent_task`` exactly
once with ``pair="cc"``, ``mode="repair"`` in a caller-supplied separate Git
worktree.  The task is technical only: generic source/tests, no GUI, private
data, screenshots, state, env, browser, worker or pinned SDK.  A claimed case is
never dispatched again: a known dispatch id is observed through
``inspect_agent_dispatch``; a claim without one (crash or failed launch) stays
``needs_review``.  No provider fallback.  This module never imports
``automation`` (it is wired through ``on_failure`` by the caller).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Callable

from filelock import FileLock

from backend.core.agents.contracts import AgentTaskRequest

_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_HEX = re.compile(r"^[0-9a-f]{16,64}$")
_CASE = re.compile(r"^[0-9a-f]{32}$")
_EVIDENCE_KEYS = ("capture_id", "capture_sha256", "evidence_capture_id", "evidence_capture_sha256",
                  "observation_id")
_MAX_IDS = 20
_REVIEW = "needs_review"


def sanitize_receipt(receipt: object) -> dict:
    """Strict whitelist of an ``AutomationFailure`` receipt; invalid required fields raise ``ValueError``."""
    if not isinstance(receipt, dict):
        raise ValueError("receipt_not_mapping")
    for key, pattern in (("case_id", _HEX), ("operation", _CODE), ("code", _CODE), ("error_type", _TYPE)):
        value = receipt.get(key)
        if not isinstance(value, str) or not pattern.match(value):
            raise ValueError(f"receipt_{key}_invalid")  # Never echo the rejected value.
    at = receipt.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at):
        raise ValueError("receipt_at_invalid")
    evidence = receipt.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    return {"case_id": receipt["case_id"], "operation": receipt["operation"], "code": receipt["code"],
            "error_type": receipt["error_type"], "at": float(at),
            "evidence": {k: evidence[k] for k in _EVIDENCE_KEYS
                         if isinstance(evidence.get(k), str) and _HEX.match(evidence[k])}}


def _signature(clean: dict) -> str:
    raw = "\0".join((clean["operation"], clean["code"], clean["error_type"]))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _default_dispatcher(request, **kwargs):
    from backend.core.agents.dispatch import dispatch_agent_task
    return dispatch_agent_task(request, **kwargs)


def _default_inspector(dispatch_id: str):
    from backend.core.agents.dispatch import inspect_agent_dispatch
    return inspect_agent_dispatch(dispatch_id)


def maintenance_request(case: dict) -> AgentTaskRequest:
    """Sanitized technical CC repair task built only from the case signature and counters."""
    sig = f"operation={case['operation']} code={case['code']} error_type={case['error_type']}"
    return AgentTaskRequest(
        title=f"Baidu Netdisk repeated failure: {case['operation']}/{case['code']}",
        problem=(f"The Baidu Netdisk automation provider failed {case['count']} times with the same "
                 f"sanitized signature ({sig}). Only these typed codes are available."),
        objective=("Find the engineering cause of this failure signature in the provider source and fix it "
                   "with focused tests, or report why it is an expected refusal."),
        suggested_focus=("backend/core/baidu_netdisk", "tests/backend"),
        evidence=(sig, f"count={case['count']}", f"maintenance_case={case['case_id']}"),
        constraints=(
            "Work only on generic source and tests inside the supplied worktree.",
            "Do not operate any GUI, window, browser, account, worker or native client.",
            "Do not read runtime state, receipts, screenshots, env files, data directories or private assets.",
            "Do not change pinned SDKs or vendored provider binaries.",
            "Use only fake/generated data in tests; never call real services or models.",
        ),
        completion_criteria=("Focused tests reproduce the signature with fakes and pass after the fix.",),
        pair="cc", mode="repair")


class MaintenanceQueue:
    """Idempotent, file-locked repeated-failure cases with explicit single CC dispatch."""

    def __init__(self, root, threshold: int = 2, *, dispatcher: Callable | None = None,
                 inspector: Callable | None = None):
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
            raise ValueError("threshold must be a positive int")
        self.root, self.threshold = Path(root), threshold
        self.dispatcher = dispatcher or _default_dispatcher
        self.inspector = inspector or _default_inspector
        self.cases = self.root / "cases"
        self.cases.mkdir(parents=True, exist_ok=True)

    # -- storage -----------------------------------------------------------
    def _lock(self) -> FileLock:
        return FileLock(str(self.root / "queue.lock"), timeout=30)

    def _path(self, case_id: str) -> Path:
        if not isinstance(case_id, str) or not _CASE.match(case_id):
            raise ValueError("case_id_invalid")
        return self.cases / f"{case_id}.json"

    def _read(self, case_id: str) -> dict | None:
        path = self._path(case_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def _write(self, case: dict) -> None:
        path = self._path(case["case_id"])
        tmp = path.with_name(f".{case['case_id']}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(case, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    # -- public ------------------------------------------------------------
    def enqueue(self, receipt: dict) -> dict:
        """Record one sanitized failure; a repeated source ``case_id`` changes nothing."""
        clean = sanitize_receipt(receipt)
        key = _signature(clean)
        with self._lock():
            case = self._read(key) or {
                "case_id": key, "operation": clean["operation"], "code": clean["code"],
                "error_type": clean["error_type"], "count": 0, "first_seen": clean["at"],
                "last_seen": clean["at"], "source_case_ids": [], "evidence": [], "dispatch": None}
            if clean["case_id"] not in case["source_case_ids"]:
                case["count"] += 1
                case["first_seen"] = min(case["first_seen"], clean["at"])
                case["last_seen"] = max(case["last_seen"], clean["at"])
                # Unbounded on purpose: every source id must stay idempotent forever.
                case["source_case_ids"] = case["source_case_ids"] + [clean["case_id"]]
                if clean["evidence"] and clean["evidence"] not in case["evidence"]:
                    case["evidence"] = (case["evidence"] + [clean["evidence"]])[-_MAX_IDS:]
                self._write(case)
        return self._public(case)

    def inspect(self, case_id: str) -> dict | None:
        case = self._read(case_id)
        return None if case is None else self._public(case, observe=True)

    def list(self) -> list[dict]:
        out = []
        for path in sorted(self.cases.glob("*.json")):
            if _CASE.match(path.stem):
                out.append(self._public(json.loads(path.read_text(encoding="utf-8"))))
        return out

    def dispatch(self, case_id: str, workspace_dir) -> dict:
        """Claim, then dispatch once to CC in a separate Git worktree; never again for this case."""
        workspace = Path(workspace_dir).resolve(strict=True)
        marker = workspace / ".git"
        if not marker.is_file() or not marker.read_text(encoding="utf-8").startswith("gitdir:"):
            raise ValueError("workspace_not_separate_git_worktree")
        claim = uuid.uuid4().hex
        with self._lock():
            case = self._read(case_id)
            if case is None:
                raise KeyError("case_unknown")
            if case["dispatch"] is not None:
                return self._public(case, observe=True)
            if case["count"] < self.threshold:
                return {**self._public(case), "dispatch": {"state": "below_threshold"}}
            case["dispatch"] = {"state": "claimed", "claim_id": claim, "claimed_at": time.time(),
                                "dispatch_id": None, "provider": None}
            self._write(case)
        request = maintenance_request(case)
        try:
            result = self.dispatcher(request, workspace_dir=str(workspace))
        except Exception as exc:  # Launch may or may not have happened: never retry automatically.
            return self._settle(case_id, claim, state=_REVIEW, error_type=type(exc).__name__)
        dispatch_id, provider = getattr(result, "dispatch_id", None), getattr(result, "provider", None)
        if not isinstance(dispatch_id, str) or not _HEX.match(dispatch_id) or provider != "claude":
            return self._settle(case_id, claim, state=_REVIEW, error_type="unexpected_dispatch_receipt")
        return self._settle(case_id, claim, state="dispatched", dispatch_id=dispatch_id, provider=provider)

    # -- internals ---------------------------------------------------------
    def _settle(self, case_id: str, claim: str, **changes) -> dict:
        with self._lock():
            case = self._read(case_id)
            if case["dispatch"] and case["dispatch"].get("claim_id") == claim:
                case["dispatch"].update(changes)
                self._write(case)
        return self._public(case, observe=True)

    def _public(self, case: dict, *, observe: bool = False) -> dict:
        out = {k: case[k] for k in ("case_id", "operation", "code", "error_type", "count",
                                     "first_seen", "last_seen")}
        out["evidence"] = [dict(e) for e in case["evidence"]]
        out["ready"] = case["count"] >= self.threshold
        claim = case.get("dispatch")
        if claim is None:
            out["dispatch"] = None
            return out
        info = {"state": claim["state"], "dispatch_id": claim.get("dispatch_id"), "provider": claim.get("provider")}
        if claim.get("error_type"):
            info["error_type"] = claim["error_type"]
        if claim["state"] == "claimed":
            info["state"] = _REVIEW  # Claim without a known dispatch id: outcome uncertain.
        if observe and info["dispatch_id"]:
            try:
                status = self.inspector(info["dispatch_id"])
                info["status"] = getattr(status, "status", None)
                info["acceptance"] = getattr(status, "acceptance", None)
            except Exception as exc:
                info["status"], info["status_error_type"] = "status_unavailable", type(exc).__name__
        out["dispatch"] = info
        return out
