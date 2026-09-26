"""Durable verified opening receipts, independent of SQLite availability.

These are completed business facts, never resumable GUI steps. A failed DB
projection is replayed by action_key without repeating any game input.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock
from sqlmodel import Session

from backend.core.settings import get_settings
from backend.core.fanxiu.storage_bag_usage import record_storage_bag_open_event


def _directory(directory: Path | None, *, create: bool = True) -> Path:
    root = directory if directory is not None else get_settings().data_dir / "fanxiu" / "storage-bag-receipts"
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            name = stream.name
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def _project(path: Path, session_factory: Callable[[], Session]) -> bool:
    with FileLock(str(path) + ".lock", timeout=30):
        document = json.loads(path.read_text(encoding="utf-8"))
        if document["state"] == "committed":
            return False
        with session_factory() as session:
            record_storage_bag_open_event(session, **document["receipt"])
            session.commit()
        # A crash after commit leaves pending: DB action_key deduplication
        # makes the next replay safe. Keep the receipt for future audits.
        document["state"] = "committed"
        _atomic_json(path, document)
        return True


def persist_storage_bag_open_receipt(
    receipt: dict[str, Any], *, session_factory: Callable[[], Session],
    directory: Path | None = None,
) -> None:
    """Durably save a verified fact before attempting its DB projection.

    Failure is propagated with the receipt still pending; reusing a key with
    different evidence is rejected. No game operation is performed here.
    """
    key = str(receipt.get("action_key") or "").strip()
    if not key:
        raise ValueError("收益凭证缺少 action_key")
    normalized = json.loads(json.dumps(receipt, ensure_ascii=False))
    path = _directory(directory) / (hashlib.sha256(key.encode()).hexdigest() + ".json")
    with FileLock(str(path) + ".lock", timeout=30):
        if path.exists():
            previous = json.loads(path.read_text(encoding="utf-8"))
            if previous["receipt"] != normalized:
                raise ValueError("同一 action_key 的收益凭证不一致")
        else:
            _atomic_json(path, {"version": 1, "state": "pending", "receipt": normalized})
    _project(path, session_factory)


def replay_storage_bag_open_receipts(
    *, session_factory: Callable[[], Session], directory: Path | None = None,
) -> int:
    """Project pending verified receipts before a new bag operation starts."""
    return sum(_project(path, session_factory) for path in sorted(_directory(directory).glob("*.json")))


def list_storage_bag_open_receipts(*, directory: Path | None = None) -> list[dict[str, Any]]:
    """Read verified receipts and projection states without touching game or DB."""
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(_directory(directory, create=False).glob("*.json"))]
