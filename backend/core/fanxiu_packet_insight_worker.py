from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

from backend.core.fanxiu_packet_insights import decode_and_sync_fanxiu_runtime_capture
from backend.core.fanxiu_tcp_flow import resolve_fanxiu_tcp_live_capture_dir, resolve_fanxiu_tcp_store_root


DEFAULT_SCAN_INTERVAL_SECONDS = 15.0
DEFAULT_STABLE_SECONDS = 8.0
MIN_PCAP_BYTES = 24


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _worker_state_path(data_dir: str | Path | None = None) -> Path:
    return resolve_fanxiu_tcp_store_root(data_dir).parent / "packet-insights" / "live_capture_worker_state.json"


def _decoded_capture_digests(data_dir: str | Path | None = None) -> set[str]:
    root = resolve_fanxiu_tcp_store_root(data_dir)
    digests: set[str] = set()
    if not root.is_dir():
        return digests
    for meta_path in root.glob("*/meta.json"):
        meta = _load_json(meta_path, {})
        if isinstance(meta, dict) and meta.get("capture_sha256"):
            digests.add(str(meta["capture_sha256"]))
    return digests


def _iter_stable_live_pcaps(
    *,
    data_dir: str | Path | None = None,
    stable_seconds: float = DEFAULT_STABLE_SECONDS,
    now: float | None = None,
) -> list[Path]:
    live_dir = resolve_fanxiu_tcp_live_capture_dir(data_dir)
    if not live_dir.is_dir():
        return []
    now_value = time.time() if now is None else now
    rows: list[Path] = []
    for path in sorted(live_dir.glob("*.pcap"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size <= MIN_PCAP_BYTES:
            continue
        if now_value - stat.st_mtime < max(1.0, float(stable_seconds)):
            continue
        rows.append(path)
    return rows


def sync_fanxiu_live_capture_backlog(
    *,
    data_dir: str | Path | None = None,
    stable_seconds: float = DEFAULT_STABLE_SECONDS,
    limit: int = 8,
) -> dict[str, Any]:
    """Decode stable live captures that were not already persisted into tcp-flow records."""
    decoded_digests = _decoded_capture_digests(data_dir)
    previous_state = _load_json(_worker_state_path(data_dir), {})
    failed_digests = {
        str(item.get("digest") or "")
        for item in (previous_state.get("errors") or [])
        if isinstance(item, dict) and item.get("digest")
    } if isinstance(previous_state, dict) else set()
    scanned = 0
    decoded: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for path in _iter_stable_live_pcaps(data_dir=data_dir, stable_seconds=stable_seconds):
        if scanned >= max(1, int(limit)):
            break
        scanned += 1
        try:
            digest = _sha256_file(path)
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})
            continue
        if digest in decoded_digests:
            skipped.append({"path": str(path), "reason": "already_decoded"})
            continue
        if digest in failed_digests:
            skipped.append({"path": str(path), "reason": "previous_error"})
            continue
        try:
            result = decode_and_sync_fanxiu_runtime_capture(path, data_dir=data_dir)
            decoded_digests.add(digest)
            decoded.append(
                {
                    "path": str(path),
                    "decoded_count": result.get("decoded_count") or 0,
                    "runtime_protocol_count": result.get("runtime_protocol_count") or 0,
                    "worship_protocol_count": result.get("worship_protocol_count") or 0,
                    "packet_runtime_sync": result.get("packet_runtime_sync") or {},
                }
            )
        except Exception as exc:
            errors.append({"path": str(path), "digest": digest, "error": str(exc)})

    payload = {
        "ok": not errors,
        "updated_at": _now_text(),
        "scanned": scanned,
        "decoded_count": len(decoded),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "decoded": decoded,
        "skipped": skipped[-20:],
        "errors": errors[-20:],
    }
    _write_json(_worker_state_path(data_dir), payload)
    return payload


class FanxiuPacketInsightWorker:
    def __init__(
        self,
        *,
        scan_interval_seconds: float = DEFAULT_SCAN_INTERVAL_SECONDS,
        stable_seconds: float = DEFAULT_STABLE_SECONDS,
    ) -> None:
        self.scan_interval_seconds = max(3.0, float(scan_interval_seconds))
        self.stable_seconds = max(1.0, float(stable_seconds))
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_result: dict[str, Any] = {}

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, name="fanxiu-packet-insight-worker", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._last_result)

    def scan_once(self) -> dict[str, Any]:
        result = sync_fanxiu_live_capture_backlog(stable_seconds=self.stable_seconds)
        with self._lock:
            self._last_result = result
        return result

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self.scan_interval_seconds):
            try:
                self.scan_once()
            except Exception as exc:
                with self._lock:
                    self._last_result = {"ok": False, "updated_at": _now_text(), "error": str(exc)}


fanxiu_packet_insight_worker = FanxiuPacketInsightWorker()
