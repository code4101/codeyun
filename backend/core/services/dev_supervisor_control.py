from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


def _default_root_dir() -> Path:
    return Path(__file__).resolve().parents[3]


def backend_restart_request_path(root_dir: str | os.PathLike[str] | None = None) -> Path:
    root = Path(root_dir or _default_root_dir()).resolve()
    digest = hashlib.sha1(os.path.normcase(os.fspath(root)).encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "codeyun" / f"dev-backend-restart-{digest}.json"


def request_backend_restart(
    *,
    source: str,
    root_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    path = backend_restart_request_path(root_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "request_id": uuid.uuid4().hex,
        "requested_at": time.time(),
        "source": str(source or "manual"),
    }
    temporary_path = path.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary_path, path)
    return payload


def read_backend_restart_request(
    root_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    path = backend_restart_request_path(root_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not payload.get("request_id"):
        return None
    return payload
