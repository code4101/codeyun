"""Submission ledger: a transfer whose outcome is unknown is never resubmitted.

A key is derived from the explicit business inputs (operation, content hash,
remote and local locations).  ``begin`` creates the record once; a later call
with the same key returns the existing record so the caller queries client
tasks instead of clicking again.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from filelock import FileLock

TERMINAL_STATES = {"verified", "failed", "abandoned"}


def transfer_key(operation: str, **inputs: object) -> str:
    if "op" in inputs:
        raise ValueError("inputs 不能包含保留键 op")
    canonical = json.dumps({"op": operation, **inputs}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


class TransferLedger:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(os.fspath(self.path) + ".lock")

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, key: str) -> dict | None:
        with self._lock:
            return self._read().get(key)

    def begin(self, operation: str, **inputs: object) -> tuple[dict, bool]:
        """Return ``(record, created)``; ``created=False`` means do not submit again."""

        key = transfer_key(operation, **inputs)
        with self._lock:
            data = self._read()
            if key in data and data[key]["state"] not in {"failed", "abandoned"}:
                return data[key], False
            record = {"key": key, "operation": operation, "inputs": inputs, "state": "planned",
                      "history": [{"state": "planned", "at": time.time()}]}
            data[key] = record
            self._write(data)
            return record, True

    def update(self, key: str, state: str, **facts: object) -> dict:
        with self._lock:
            data = self._read()
            record = data[key]
            if record["state"] in TERMINAL_STATES and state != record["state"]:
                raise ValueError(f"记录已终结为 {record['state']}，不能改为 {state}")
            record["state"] = state
            record.setdefault("facts", {}).update(facts)
            record["history"].append({"state": state, "at": time.time(), **({"facts": facts} if facts else {})})
            self._write(data)
            return record
