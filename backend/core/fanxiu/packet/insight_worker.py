from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import queue
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from backend.core.fanxiu.packet.activity_sync import sync_fanxiu_activity_packets
from backend.core.fanxiu.packet.insights import (
    decode_and_sync_fanxiu_runtime_capture,
    sync_fanxiu_packet_business_for_decode_result,
    sync_fanxiu_packet_player_profiles,
    sync_fanxiu_packet_runtime_insights_for_decode_result,
    sync_fanxiu_packet_runtime_insights,
)
from backend.core.fanxiu.packet.decoded_store import sync_fanxiu_decoded_record_backlog
from backend.core.fanxiu.packet.tcp_flow import (
    DEFAULT_FANXIU_SERVER_HOST,
    _iter_fanxiu_tcp_decoded_sources,
    list_tcp_streams_with_tshark,
    resolve_fanxiu_tcp_live_capture_dir,
    resolve_fanxiu_tcp_store_root,
)
from backend.core.fanxiu.runtime.capture_runtime import (
    FANXIU_CAPTURE_RUNTIME_PACKET_WORKER_REASON,
    ensure_fanxiu_capture_runtime_backstop,
    latest_fanxiu_live_capture_summary,
)


DEFAULT_SCAN_INTERVAL_SECONDS = 15.0
DEFAULT_MAINTENANCE_INTERVAL_SECONDS = 30 * 60.0
DEFAULT_STABLE_SECONDS = 8.0
DEFAULT_FAILED_RETRY_SECONDS = 600.0
DEFAULT_LIVE_CAPTURE_MAX_AGE_SECONDS = 24 * 60 * 60
DEFAULT_MAINTENANCE_DECODE_LIMIT = 32
DEFAULT_DECODE_MAX_STREAMS = 4
DEFAULT_LIVE_CAPTURE_SCAN_MULTIPLIER = 40
DEFAULT_DECODE_TIMEOUT_SECONDS = 30.0
DEFAULT_CAPTURE_BACKSTOP_MAX_PCAP_AGE_SECONDS = 5 * 60.0
MIN_PCAP_BYTES = 24
PACKET_INSIGHT_WORKER_SCHEMA_VERSION = 3
PACKET_DECODE_ALLOW_UNDER_COMMIT_PRESSURE_ENV = "CODEYUN_PACKET_DECODE_ALLOW_UNDER_COMMIT_PRESSURE"
PACKET_DECODE_COMMIT_PRESSURE_PERCENT = 90.0
PACKET_DECODE_COMMIT_PRESSURE_AVAILABLE_MB = 8 * 1024
MAIL_SOURCE_PROTOCOL_NAMES = {"SM_MailBox", "SM_NewMail"}
MAIL_ACTION_PROTOCOL_NAMES = {"CM_ReadMail", "SM_ReadMail", "CM_GetMailReward", "SM_GetMailReward", "CM_DeleteMail", "SM_DeleteMail"}


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


def _host_commit_pressure_for_packet_decode() -> dict[str, Any]:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return {"skip": False}
    if os.getenv(PACKET_DECODE_ALLOW_UNDER_COMMIT_PRESSURE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
        return {"skip": False}
    try:
        try:
            from backend.core.runtime.ocr_service import _windows_commit_snapshot

            commit = _windows_commit_snapshot()
        except (ImportError, AttributeError):
            from backend.core.fanxiu.runtime.mumu_control import _collect_windows_commit_snapshot

            commit = _collect_windows_commit_snapshot()
    except Exception:
        return {"skip": False}
    if not commit:
        return {"skip": False}
    commit_percent = float(commit.get("commit_percent") or 0.0)
    commit_available_mb = int(commit.get("commit_available_mb") or 0)
    skip = commit_percent >= PACKET_DECODE_COMMIT_PRESSURE_PERCENT or commit_available_mb < PACKET_DECODE_COMMIT_PRESSURE_AVAILABLE_MB
    return {
        "skip": skip,
        "reason": "host_commit_pressure",
        "commit": commit,
    }


def _capture_backstop_max_pcap_age_seconds() -> float:
    try:
        return max(
            60.0,
            float(os.getenv("FX_CAPTURE_BACKSTOP_MAX_PCAP_AGE_SECONDS") or DEFAULT_CAPTURE_BACKSTOP_MAX_PCAP_AGE_SECONDS),
        )
    except (TypeError, ValueError):
        return DEFAULT_CAPTURE_BACKSTOP_MAX_PCAP_AGE_SECONDS


def _ensure_capture_runtime_from_packet_worker(*, data_dir: str | Path | None = None) -> dict[str, Any]:
    summary = latest_fanxiu_live_capture_summary(data_dir=data_dir)
    max_age = _capture_backstop_max_pcap_age_seconds()
    latest_age = summary.get("latest_age_seconds")
    should_ensure = latest_age is None or float(latest_age) > max_age
    if not should_ensure:
        return {
            "ok": True,
            "ensured": False,
            "reason": "recent_live_capture_present",
            "max_age_seconds": max_age,
            "live_capture": summary,
        }
    result = ensure_fanxiu_capture_runtime_backstop(
        FANXIU_CAPTURE_RUNTIME_PACKET_WORKER_REASON,
    )
    result["max_age_seconds"] = max_age
    result["live_capture"] = summary
    return result


def _worker_state_path(data_dir: str | Path | None = None) -> Path:
    return resolve_fanxiu_tcp_store_root(data_dir).parent / "packet-insights" / "live_capture_worker_state.json"


def _maintenance_state_path(data_dir: str | Path | None = None) -> Path:
    return resolve_fanxiu_tcp_store_root(data_dir).parent / "packet-insights" / "maintenance_worker_state.json"


def _mail_business_backlog_state_path(data_dir: str | Path | None = None) -> Path:
    return resolve_fanxiu_tcp_store_root(data_dir).parent / "packet-insights" / "mail_business_backlog_state.json"


def _decoded_business_backlog_state_path(data_dir: str | Path | None = None) -> Path:
    return resolve_fanxiu_tcp_store_root(data_dir).parent / "packet-insights" / "decoded_business_backlog_state.json"


def _decoded_source_key(source: dict[str, Any]) -> str:
    return "|".join(
        [
            str(source.get("decoded_path") or ""),
            str(source.get("stream") or 0),
            str(source.get("record_id") or ""),
        ]
    )


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


def _decoded_capture_streams_by_digest(data_dir: str | Path | None = None) -> dict[str, set[int]]:
    root = resolve_fanxiu_tcp_store_root(data_dir)
    streams_by_digest: dict[str, set[int]] = {}
    if not root.is_dir():
        return streams_by_digest
    for meta_path in root.glob("*/meta.json"):
        meta = _load_json(meta_path, {})
        if not isinstance(meta, dict):
            continue
        digest = str(meta.get("capture_sha256") or "").strip()
        if not digest:
            continue
        if "stream" not in meta:
            streams_by_digest.setdefault(digest, set())
            continue
        try:
            stream = int(meta.get("stream") or 0)
        except (TypeError, ValueError):
            stream = 0
        streams_by_digest.setdefault(digest, set()).add(stream)
    return streams_by_digest


def _decoded_capture_sources_by_digest(data_dir: str | Path | None = None) -> dict[str, list[dict[str, Any]]]:
    root = resolve_fanxiu_tcp_store_root(data_dir)
    sources_by_digest: dict[str, list[dict[str, Any]]] = {}
    if not root.is_dir():
        return sources_by_digest
    for meta_path in root.glob("*/meta.json"):
        meta = _load_json(meta_path, {})
        if not isinstance(meta, dict):
            continue
        digest = str(meta.get("capture_sha256") or "").strip()
        if not digest:
            continue
        record_dir = meta_path.parent
        decoded_path = Path(str(meta.get("decoded_path") or record_dir / "decoded.json"))
        if not decoded_path.is_file():
            continue
        sources_by_digest.setdefault(digest, []).append(
            {
                "decoded_path": str(decoded_path),
                "record_id": meta.get("record_id") or record_dir.name,
                "pcap_name": meta.get("pcap_name") or "",
                "created_at": meta.get("created_at") or "",
                "source_kind": "record",
                "source_pcap": meta.get("source_pcap") or "",
                "stored_pcap": meta.get("stored_pcap") or "",
                "stream": int(meta.get("stream") or 0),
            }
        )
    return sources_by_digest


def _target_stream_ids_for_pcap(path: Path, *, max_streams: int, server_host: str = DEFAULT_FANXIU_SERVER_HOST) -> list[int]:
    try:
        rows = list_tcp_streams_with_tshark(path, host=server_host)
    except Exception:
        return list(range(max(1, int(max_streams))))
    if not rows:
        return list(range(max(1, int(max_streams))))
    stream_ids: list[int] = []
    for row in rows[: max(1, int(max_streams))]:
        stream_value = row.get("stream")
        if stream_value is None or str(stream_value).strip() == "":
            continue
        try:
            stream_ids.append(int(stream_value))
        except (TypeError, ValueError):
            continue
    return stream_ids


def _capture_streams_fully_decoded(
    decoded_digests: set[str],
    decoded_streams_by_digest: dict[str, set[int]],
    digest: str,
    target_stream_ids: list[int],
) -> bool:
    if digest not in decoded_digests:
        return False
    if not target_stream_ids:
        return True
    decoded_streams = decoded_streams_by_digest.get(digest) or set()
    if not decoded_streams:
        return True
    return set(target_stream_ids).issubset(decoded_streams)


def _decoded_stream_ids_from_result(result: dict[str, Any]) -> set[int]:
    stream_ids: set[int] = set()
    for item in result.get("decoded") or []:
        if not isinstance(item, dict) or not item.get("output_path"):
            continue
        try:
            stream_ids.add(int(item.get("stream") or 0))
        except (TypeError, ValueError):
            continue
    return stream_ids


def _decoded_sources_from_result(path: Path, digest: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "decoded_path": item.get("output_path") or "",
            "record_id": item.get("record_id") or "",
            "pcap_name": path.name,
            "created_at": _now_text(),
            "source_kind": "runtime",
            "source_pcap": str(path),
            "stream": item.get("stream"),
            "capture_sha256": digest,
        }
        for item in (result.get("decoded") or [])
        if isinstance(item, dict) and item.get("output_path")
    ]


def _decode_result_from_source(source: dict[str, Any]) -> dict[str, Any] | None:
    decoded_path = Path(str(source.get("decoded_path") or ""))
    decoded = _load_json(decoded_path, {})
    if not isinstance(decoded, dict):
        return None
    decoded.update(
        {
            "record_id": source.get("record_id") or decoded.get("record_id") or "",
            "created_at": source.get("created_at") or decoded.get("created_at") or "",
            "pcap_name": source.get("pcap_name") or decoded.get("pcap_name") or "",
            "source_pcap": source.get("source_pcap") or decoded.get("pcap") or "",
            "stored_pcap": source.get("stored_pcap") or decoded.get("stored_pcap") or "",
            "stream": int(source.get("stream") or decoded.get("stream") or 0),
            "stored_decoded_path": str(decoded_path),
            "output_path": str(decoded_path),
        }
    )
    return decoded


def _mail_source_protocol_probe(sources: list[dict[str, Any]]) -> dict[str, Any]:
    protocol_counts: dict[str, int] = {}
    source_samples: list[dict[str, Any]] = []
    action_samples: list[dict[str, Any]] = []
    for source in sources:
        decoded_path = Path(str(source.get("decoded_path") or ""))
        decoded = _load_json(decoded_path, {})
        frames = decoded.get("frames") if isinstance(decoded, dict) else None
        if not isinstance(frames, list):
            continue
        for index, frame in enumerate(frames):
            if not isinstance(frame, dict):
                continue
            name = str(frame.get("name") or "")
            if name not in MAIL_SOURCE_PROTOCOL_NAMES and name not in MAIL_ACTION_PROTOCOL_NAMES:
                continue
            protocol_counts[name] = protocol_counts.get(name, 0) + 1
            sample = {
                "protocol": name,
                "decoded_path": str(decoded_path),
                "record_id": source.get("record_id") or decoded.get("record_id") or "",
                "pcap_name": source.get("pcap_name") or decoded.get("pcap_name") or "",
                "frame_index": index,
            }
            if name in MAIL_SOURCE_PROTOCOL_NAMES and len(source_samples) < 8:
                source_samples.append(sample)
            elif name in MAIL_ACTION_PROTOCOL_NAMES and len(action_samples) < 8:
                action_samples.append(sample)
    return {
        "source_count": len(sources),
        "protocol_counts": protocol_counts,
        "has_mailbox_source": bool(protocol_counts.get("SM_MailBox")),
        "has_new_mail_source": bool(protocol_counts.get("SM_NewMail")),
        "has_any_mail_source": any(protocol_counts.get(name) for name in MAIL_SOURCE_PROTOCOL_NAMES),
        "has_mail_action": any(protocol_counts.get(name) for name in MAIL_ACTION_PROTOCOL_NAMES),
        "source_samples": source_samples,
        "action_samples": action_samples,
    }


def _current_runtime_capture_path() -> Path | None:
    try:
        from backend.core.fanxiu.runtime.capture_runtime import fanxiu_capture_runtime_service

        status = fanxiu_capture_runtime_service.status()
    except Exception:
        return None
    path = str(status.get("current_pcap_path") or "").strip()
    if not path:
        return None
    return Path(path)


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return str(left) == str(right)


def _is_transient_capture_access_error(exc: BaseException) -> bool:
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 32:
        return True
    text = str(exc)
    return "[WinError 32]" in text or "另一个程序正在使用此文件" in text


def _iter_stable_live_pcaps(
    *,
    data_dir: str | Path | None = None,
    stable_seconds: float = DEFAULT_STABLE_SECONDS,
    max_age_seconds: float | None = None,
    min_mtime: float = 0.0,
    newest_first: bool = False,
    now: float | None = None,
) -> list[Path]:
    live_dir = resolve_fanxiu_tcp_live_capture_dir(data_dir)
    if not live_dir.is_dir():
        return []
    now_value = time.time() if now is None else now
    active_capture_path = _current_runtime_capture_path()
    rows: list[Path] = []
    for path in sorted(live_dir.glob("*.pcap"), key=lambda item: item.stat().st_mtime, reverse=bool(newest_first)):
        if active_capture_path and _same_path(path, active_capture_path):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size <= MIN_PCAP_BYTES:
            continue
        if min_mtime and stat.st_mtime <= min_mtime:
            continue
        if now_value - stat.st_mtime < max(1.0, float(stable_seconds)):
            continue
        if max_age_seconds is not None and max_age_seconds > 0 and now_value - stat.st_mtime > float(max_age_seconds):
            continue
        rows.append(path)
    return rows


def _previous_errors_by_digest(state: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(state, dict):
        return {}
    output: dict[str, dict[str, Any]] = {}
    for item in state.get("errors") or []:
        if not isinstance(item, dict):
            continue
        digest = str(item.get("digest") or "").strip()
        if digest:
            output[digest] = item
    return output


def _pcap_state_item(path: Path, digest: str, *, status: str, reason: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        stat = path.stat()
        mtime = float(stat.st_mtime)
        size = int(stat.st_size)
    except OSError:
        mtime = 0.0
        size = 0
    item: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "digest": digest,
        "status": status,
        "reason": reason,
        "mtime": mtime,
        "size": size,
        "updated_at": _now_text(),
    }
    if extra:
        item.update(extra)
    return item


def _merge_pcap_states(previous_state: Any, updates: list[dict[str, Any]], *, limit: int = 200) -> list[dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    if isinstance(previous_state, dict):
        for item in previous_state.get("pcap_states") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("digest") or item.get("path") or "").strip()
            if key:
                states[key] = item
    for item in updates:
        key = str(item.get("digest") or item.get("path") or "").strip()
        if key:
            states[key] = item
    return sorted(states.values(), key=lambda item: float(item.get("mtime") or 0), reverse=True)[:limit]


def _sync_business_after_decoded(
    decoded: list[dict[str, Any]],
    *,
    data_dir: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not decoded:
        return {}, {}
    runtime_sync: dict[str, Any] = {
        "ok": True,
        "changed": False,
        "mode": "incremental_decoded_business_sync",
        "source_count": 0,
        "sync_count": 0,
        "error_count": 0,
        "errors": [],
    }
    decoded_sources = [
        source
        for item in decoded
        for source in (item.get("decoded_sources") or [])
        if isinstance(source, dict)
    ]
    for source in decoded_sources:
        result = _decode_result_from_source(source)
        if result is None:
            continue
        runtime_sync["source_count"] += 1
        try:
            sync_result = sync_fanxiu_packet_business_for_decode_result(
                result,
                data_dir=data_dir,
            )
            if sync_result is not None:
                runtime_sync["sync_count"] += 1
                runtime_sync["changed"] = bool(runtime_sync["changed"] or sync_result.get("changed"))
        except Exception as exc:
            runtime_sync["ok"] = False
            runtime_sync["error_count"] += 1
            runtime_sync["errors"].append({"decoded_path": str(source.get("decoded_path") or ""), "error": str(exc)})
    try:
        from sqlmodel import Session

        from backend.core.fanxiu.mail.packet_sync import sync_fanxiu_mail_packets
        from backend.db import engine

        with Session(engine) as session:
            mail_sync = sync_fanxiu_mail_packets(
                session,
                data_dir=data_dir,
                clear_existing=False,
                decoded_sources=decoded_sources,
            )
    except Exception as exc:
        mail_sync = {"ok": False, "error": str(exc)}
    for item in decoded:
        item["batch_packet_runtime_sync"] = {
            "changed": bool(runtime_sync.get("changed")) if isinstance(runtime_sync, dict) else False,
            "snapshot_path": str(runtime_sync.get("snapshot_path") or "") if isinstance(runtime_sync, dict) else "",
        }
        item["batch_mail_packet_sync"] = mail_sync
    return runtime_sync, mail_sync


def _decode_runtime_capture_direct(
    path: Path,
    *,
    data_dir: str | Path | None = None,
    max_streams: int,
) -> dict[str, Any]:
    return decode_and_sync_fanxiu_runtime_capture(
        path,
        data_dir=data_dir,
        max_streams=max(1, int(max_streams)),
        sync_business=False,
    )


def _decode_runtime_capture_process_entry(
    path_text: str,
    data_dir_text: str,
    max_streams: int,
    result_queue: Any,
) -> None:
    try:
        result = _decode_runtime_capture_direct(
            Path(path_text),
            data_dir=Path(data_dir_text) if data_dir_text else None,
            max_streams=max_streams,
        )
    except BaseException as exc:  # noqa: BLE001 - send child failures back to parent.
        result_queue.put(
            {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=20),
            }
        )
        return
    result_queue.put({"ok": True, "result": result})


def _decode_timeout_uses_process() -> bool:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    mode = os.getenv("FX_PACKET_DECODE_TIMEOUT_MODE", "process").strip().lower()
    return mode not in {"thread", "threads", "legacy"}


def _decode_runtime_capture_with_thread_timeout(
    path: Path,
    *,
    data_dir: str | Path | None = None,
    max_streams: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["result"] = _decode_runtime_capture_direct(
                path,
                data_dir=data_dir,
                max_streams=max_streams,
            )
        except BaseException as exc:  # noqa: BLE001 - propagate through worker state.
            box["error"] = exc

    thread = threading.Thread(target=_target, name="fanxiu-pcap-decode-once", daemon=True)
    thread.start()
    thread.join(timeout=max(1.0, float(timeout_seconds)))
    if thread.is_alive():
        raise TimeoutError(f"pcap 解码超时 {timeout_seconds:.0f}s：{path}")
    if "error" in box:
        raise box["error"]
    result = box.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"pcap 解码未返回有效结果：{path}")
    return result


def _decode_runtime_capture_with_process_timeout(
    path: Path,
    *,
    data_dir: str | Path | None = None,
    max_streams: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    context = mp.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_decode_runtime_capture_process_entry,
        args=(str(path), str(data_dir or ""), max(1, int(max_streams)), result_queue),
        daemon=True,
    )
    process.start()
    process.join(timeout=max(1.0, float(timeout_seconds)))
    if process.is_alive():
        process.terminate()
        process.join(timeout=2.0)
        raise TimeoutError(f"pcap 解码超时 {timeout_seconds:.0f}s：{path}")
    try:
        payload = result_queue.get_nowait()
    except queue.Empty:
        raise RuntimeError(f"pcap 解码子进程未返回有效结果：{path}，exitcode={process.exitcode}")
    if isinstance(payload, dict) and payload.get("ok") is True and isinstance(payload.get("result"), dict):
        return payload["result"]
    if isinstance(payload, dict):
        error = str(payload.get("error") or "")
        error_type = str(payload.get("error_type") or "RuntimeError")
        raise RuntimeError(f"pcap 解码失败：{path}：{error_type}: {error}")
    raise RuntimeError(f"pcap 解码未返回有效结果：{path}")


def _decode_runtime_capture_with_timeout(
    path: Path,
    *,
    data_dir: str | Path | None = None,
    max_streams: int,
    timeout_seconds: float = DEFAULT_DECODE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if _decode_timeout_uses_process():
        return _decode_runtime_capture_with_process_timeout(
            path,
            data_dir=data_dir,
            max_streams=max_streams,
            timeout_seconds=timeout_seconds,
        )
    return _decode_runtime_capture_with_thread_timeout(
        path,
        data_dir=data_dir,
        max_streams=max_streams,
        timeout_seconds=timeout_seconds,
    )


def _sync_historical_business_backlog(
    *,
    data_dir: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime_sync: dict[str, Any] = {"ok": True, "mode": "historical_business_backlog"}
    decoded_business_sync = sync_fanxiu_decoded_business_backlog(data_dir=data_dir)
    try:
        packet_runtime_sync = sync_fanxiu_packet_runtime_insights(data_dir=data_dir, force=False)
    except Exception as exc:
        packet_runtime_sync = {"ok": False, "error": str(exc)}
    try:
        player_profile_sync = sync_fanxiu_packet_player_profiles(data_dir=data_dir, force=False)
    except Exception as exc:
        player_profile_sync = {"ok": False, "error": str(exc)}
    runtime_sync["decoded_business_sync"] = decoded_business_sync
    runtime_sync["packet_runtime_sync"] = packet_runtime_sync
    runtime_sync["player_profile_sync"] = player_profile_sync
    runtime_sync["ok"] = bool(
        decoded_business_sync.get("ok", True)
        and packet_runtime_sync.get("ok", True)
        and player_profile_sync.get("ok", True)
    )
    runtime_sync["changed"] = bool(
        decoded_business_sync.get("changed")
        or packet_runtime_sync.get("changed")
        or player_profile_sync.get("changed")
    )

    try:
        mail_sync = sync_fanxiu_mail_business_backlog(data_dir=data_dir)
    except Exception as exc:
        mail_sync = {
            "ok": False,
            "changed": False,
            "mode": "mail_business_backlog",
            "error": str(exc),
        }
        runtime_sync["ok"] = False
    return runtime_sync, mail_sync


def _select_backlog_sources(
    sources: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    latest_limit: int,
    historical_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    recent_done = {
        str(item)
        for item in (state.get("recent_processed_source_keys") or [])
        if str(item or "").strip()
    } if isinstance(state, dict) else set()
    latest_sources: list[dict[str, Any]] = []
    for source in sources[: max(int(latest_limit) * 8, int(latest_limit), 1)]:
        key = _decoded_source_key(source)
        if key in recent_done:
            continue
        latest_sources.append(source)
        if len(latest_sources) >= max(0, int(latest_limit)):
            break

    oldest_sources = list(reversed(sources))
    cursor_key = str(state.get("historical_cursor_source_key") or "") if isinstance(state, dict) else ""
    start_index = 0
    if cursor_key:
        for index, source in enumerate(oldest_sources):
            if _decoded_source_key(source) == cursor_key:
                start_index = index + 1
                break
    if start_index >= len(oldest_sources):
        start_index = 0
    historical_sources = oldest_sources[start_index: start_index + max(0, int(historical_limit))]
    return latest_sources, historical_sources


def sync_fanxiu_decoded_business_backlog(
    *,
    data_dir: str | Path | None = None,
    latest_limit: int = 64,
    historical_limit: int = 128,
) -> dict[str, Any]:
    """Incrementally backfill decoded business facts through the primary ingestor."""
    sources = _iter_fanxiu_tcp_decoded_sources(data_dir)
    state_path = _decoded_business_backlog_state_path(data_dir)
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    latest_sources, historical_sources = _select_backlog_sources(
        sources,
        state,
        latest_limit=latest_limit,
        historical_limit=historical_limit,
    )
    selected_sources = latest_sources + historical_sources
    processed_keys: list[str] = []
    sync_count = 0
    changed = False
    errors: list[dict[str, Any]] = []
    domain_counts: dict[str, int] = {}
    for source in selected_sources:
        key = _decoded_source_key(source)
        result = _decode_result_from_source(source)
        if result is None:
            processed_keys.append(key)
            continue
        try:
            sync_result = sync_fanxiu_packet_business_for_decode_result(result, data_dir=data_dir)
        except Exception as exc:
            errors.append({"source_key": key, "decoded_path": str(source.get("decoded_path") or ""), "error": str(exc)})
            continue
        processed_keys.append(key)
        if sync_result is None:
            continue
        sync_count += 1
        changed = bool(changed or sync_result.get("changed"))
        for protocol in sync_result.get("protocols") or []:
            protocol_text = str(protocol or "")
            if protocol_text:
                domain_counts[protocol_text] = domain_counts.get(protocol_text, 0) + 1

    recent_processed = list(dict.fromkeys(processed_keys + [
        str(item)
        for item in (state.get("recent_processed_source_keys") or [])
        if str(item or "").strip()
    ]))[:1000]
    historical_cursor = _decoded_source_key(historical_sources[-1]) if historical_sources else str(state.get("historical_cursor_source_key") or "")
    payload = {
        "schema_version": PACKET_INSIGHT_WORKER_SCHEMA_VERSION,
        "ok": not errors,
        "updated_at": _now_text(),
        "source_count": len(sources),
        "selected_count": len(selected_sources),
        "latest_selected_count": len(latest_sources),
        "historical_selected_count": len(historical_sources),
        "processed_count": len(processed_keys),
        "sync_count": sync_count,
        "changed": changed,
        "protocol_counts": domain_counts,
        "error_count": len(errors),
        "errors": errors[:20],
        "recent_processed_source_keys": recent_processed,
        "historical_cursor_source_key": historical_cursor,
    }
    _write_json(state_path, payload)
    return {key: value for key, value in payload.items() if key not in {"recent_processed_source_keys"}}


def sync_fanxiu_mail_business_backlog(
    *,
    data_dir: str | Path | None = None,
    latest_limit: int = 32,
    historical_limit: int = 64,
) -> dict[str, Any]:
    """Incrementally backfill decoded mail facts without rescanning every decoded JSON."""
    sources = _iter_fanxiu_tcp_decoded_sources(data_dir)
    state_path = _mail_business_backlog_state_path(data_dir)
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    latest_sources, historical_sources = _select_backlog_sources(
        sources,
        state,
        latest_limit=latest_limit,
        historical_limit=historical_limit,
    )

    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    for source in [*latest_sources, *historical_sources]:
        key = _decoded_source_key(source)
        if not key or key in selected_keys:
            continue
        selected_keys.add(key)
        selected.append(source)

    if not selected:
        payload = {
            "ok": True,
            "mode": "mail_business_backlog",
            "source_count": len(sources),
            "selected_count": 0,
            "latest_selected_count": 0,
            "historical_selected_count": 0,
            "mail_packet_sync": {"ok": True, "skipped": True, "reason": "no_decoded_sources"},
            "mail_source_probe": _mail_source_protocol_probe([]),
            "updated_at": _now_text(),
        }
        _write_json(state_path, {**(state if isinstance(state, dict) else {}), **payload})
        return payload

    try:
        from sqlmodel import Session

        from backend.core.fanxiu.mail.packet_sync import sync_fanxiu_mail_packets
        from backend.db import engine

        with Session(engine) as session:
            mail_sync = sync_fanxiu_mail_packets(
                session,
                data_dir=data_dir,
                clear_existing=False,
                decoded_sources=selected,
            )
    except Exception as exc:
        mail_sync = {"ok": False, "error": str(exc)}

    historical_cursor_source_key = (
        _decoded_source_key(historical_sources[-1])
        if historical_sources
        else str(state.get("historical_cursor_source_key") or "")
    )
    previous_recent = [
        str(item)
        for item in (state.get("recent_processed_source_keys") or [])
        if str(item or "").strip()
    ]
    recent_processed = list(dict.fromkeys([*[_decoded_source_key(source) for source in latest_sources], *previous_recent]))[:2000]
    payload = {
        "ok": bool(mail_sync.get("ok", True)),
        "mode": "mail_business_backlog",
        "source_count": len(sources),
        "selected_count": len(selected),
        "latest_selected_count": len(latest_sources),
        "historical_selected_count": len(historical_sources),
        "historical_cursor_source_key": historical_cursor_source_key,
        "historical_cursor_index": len(historical_sources),
        "recent_processed_source_keys": recent_processed,
        "mail_packet_sync": mail_sync,
        "mail_source_probe": _mail_source_protocol_probe(selected),
        "updated_at": _now_text(),
    }
    _write_json(state_path, payload)
    return payload


def sync_fanxiu_capture_paths(
    paths: list[str | Path],
    *,
    data_dir: str | Path | None = None,
    max_streams: int = DEFAULT_DECODE_MAX_STREAMS,
) -> dict[str, Any]:
    """Decode specific sealed pcaps and update business facts once.

    Behavior-tree jobs use this for short, recent capture segments. It avoids
    walking the historical live-capture backlog while keeping decode/upsert
    responsibilities inside the packet service.
    """
    pressure = _host_commit_pressure_for_packet_decode()
    if pressure.get("skip"):
        return {
            "schema_version": PACKET_INSIGHT_WORKER_SCHEMA_VERSION,
            "ok": True,
            "updated_at": _now_text(),
            "input_count": len(paths),
            "decoded_count": 0,
            "new_decode_count": 0,
            "business_backfill_count": 0,
            "skipped_count": len(paths),
            "error_count": 0,
            "decoded": [],
            "skipped": [
                {"path": str(Path(raw_path).expanduser()), "reason": "host_commit_pressure"}
                for raw_path in paths
            ],
            "errors": [],
            "mail_packet_sync": {"ok": True, "skipped": True, "reason": "host_commit_pressure"},
            "host_commit_pressure": pressure,
        }

    decoded_digests = _decoded_capture_digests(data_dir)
    decoded_streams_by_digest = _decoded_capture_streams_by_digest(data_dir)
    decoded_sources_by_digest = _decoded_capture_sources_by_digest(data_dir)
    decoded: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            skipped.append({"path": str(path), "reason": "missing"})
            continue
        try:
            if path.stat().st_size <= MIN_PCAP_BYTES:
                skipped.append({"path": str(path), "reason": "too_small"})
                continue
            digest = _sha256_file(path)
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})
            continue
        target_stream_ids = _target_stream_ids_for_pcap(path, max_streams=max_streams)
        backfill_sources = decoded_sources_by_digest.get(digest) or []
        if (
            _capture_streams_fully_decoded(decoded_digests, decoded_streams_by_digest, digest, target_stream_ids)
            or (digest in decoded_digests and bool(backfill_sources))
        ):
            skipped.append(
                {
                    "path": str(path),
                    "reason": "already_decoded",
                    "target_stream_ids": target_stream_ids,
                    "decoded_stream_ids": sorted(decoded_streams_by_digest.get(digest) or set()),
                    "business_backfill_source_count": len(backfill_sources),
                }
            )
            if backfill_sources:
                decoded.append(
                    {
                        "path": str(path),
                        "decoded_count": 0,
                        "runtime_protocol_count": 0,
                        "worship_protocol_count": 0,
                        "already_decoded": True,
                        "decoded_sources": backfill_sources,
                    }
                )
            continue
        try:
            result = _decode_runtime_capture_with_timeout(
                path,
                data_dir=data_dir,
                max_streams=max(1, int(max_streams)),
            )
            actual_decoded_stream_ids = _decoded_stream_ids_from_result(result)
            if (
                not actual_decoded_stream_ids
                and int(result.get("decoded_count") or 0) > 0
                and not result.get("decoded")
            ):
                actual_decoded_stream_ids = set(target_stream_ids)
            missing_stream_ids = sorted(set(target_stream_ids) - actual_decoded_stream_ids)
            decoded.append(
                {
                    "path": str(path),
                    "decoded_count": result.get("decoded_count") or 0,
                    "runtime_protocol_count": result.get("runtime_protocol_count") or 0,
                    "worship_protocol_count": result.get("worship_protocol_count") or 0,
                    "packet_runtime_sync": result.get("packet_runtime_sync") or {},
                    "mail_packet_sync": result.get("mail_packet_sync") or {},
                    "target_stream_ids": target_stream_ids,
                    "decoded_stream_ids": sorted(actual_decoded_stream_ids),
                    "missing_stream_ids": missing_stream_ids,
                    "decoded_sources": _decoded_sources_from_result(path, digest, result),
                }
            )
            decoded_digests.add(digest)
            decoded_streams_by_digest.setdefault(digest, set()).update(actual_decoded_stream_ids)
        except Exception as exc:
            errors.append({"path": str(path), "digest": digest, "error": str(exc)})
    _runtime_sync, mail_sync = _sync_business_after_decoded(decoded, data_dir=data_dir)
    payload = {
        "schema_version": PACKET_INSIGHT_WORKER_SCHEMA_VERSION,
        "ok": not errors,
        "updated_at": _now_text(),
        "input_count": len(paths),
        "decoded_count": len(decoded),
        "new_decode_count": sum(1 for item in decoded if not item.get("already_decoded")),
        "business_backfill_count": sum(1 for item in decoded if item.get("already_decoded")),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "decoded": decoded,
        "skipped": skipped,
        "errors": errors,
        "mail_packet_sync": mail_sync,
    }
    return payload


def sync_fanxiu_live_capture_backlog(
    *,
    data_dir: str | Path | None = None,
    stable_seconds: float = DEFAULT_STABLE_SECONDS,
    retry_failed_after_seconds: float = DEFAULT_FAILED_RETRY_SECONDS,
    max_capture_age_seconds: float | None = DEFAULT_LIVE_CAPTURE_MAX_AGE_SECONDS,
    max_streams: int = 2,
    use_cursor: bool = True,
    limit: int = 8,
    newest_first: bool = False,
) -> dict[str, Any]:
    """Decode stable live captures and push decoded facts into business writers.

    This worker is the packet service's independent ingestion path. It must not
    depend on UI pages or behavior-tree jobs to refresh mail/bag/activity data.
    """
    decoded_digests = _decoded_capture_digests(data_dir)
    decoded_streams_by_digest = _decoded_capture_streams_by_digest(data_dir)
    decoded_sources_by_digest = _decoded_capture_sources_by_digest(data_dir)
    previous_state = _load_json(_worker_state_path(data_dir), {})
    previous_errors = _previous_errors_by_digest(previous_state)
    previous_cursor_mtime = float(
        previous_state.get("confirmed_cursor_mtime") or previous_state.get("cursor_mtime") or 0
    ) if isinstance(previous_state, dict) else 0.0
    retry_seconds = max(0.0, float(retry_failed_after_seconds))
    now_epoch = time.time()
    scanned = 0
    decoded: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    pcap_state_updates: list[dict[str, Any]] = []
    decode_attempts = 0
    business_backfill_attempts = 0
    max_scanned = max(
        max(1, int(limit)) * DEFAULT_LIVE_CAPTURE_SCAN_MULTIPLIER,
        max(1, int(limit)),
    )
    confirmed_cursor_mtime = previous_cursor_mtime
    confirmed_cursor_pcap = str(
        previous_state.get("confirmed_cursor_pcap") or previous_state.get("cursor_pcap") or ""
    ) if isinstance(previous_state, dict) else ""
    latest_scanned_mtime = float(previous_state.get("latest_scanned_mtime") or previous_cursor_mtime) if isinstance(previous_state, dict) else previous_cursor_mtime
    latest_scanned_pcap = str(previous_state.get("latest_scanned_pcap") or "") if isinstance(previous_state, dict) else ""
    cursor_blocked = False
    pressure = _host_commit_pressure_for_packet_decode()
    if pressure.get("skip"):
        payload = {
            "schema_version": PACKET_INSIGHT_WORKER_SCHEMA_VERSION,
            "ok": True,
            "updated_at": _now_text(),
            "skipped": True,
            "skip_reason": "host_commit_pressure",
            "host_commit_pressure": pressure,
            "cursor_mtime": confirmed_cursor_mtime,
            "cursor_pcap": confirmed_cursor_pcap,
            "confirmed_cursor_mtime": confirmed_cursor_mtime,
            "confirmed_cursor_pcap": confirmed_cursor_pcap,
            "latest_scanned_mtime": latest_scanned_mtime,
            "latest_scanned_pcap": latest_scanned_pcap,
            "has_unconfirmed_gap": bool(previous_errors),
            "scanned": 0,
            "decode_attempts": 0,
            "business_backfill_attempts": 0,
            "decoded_count": 0,
            "new_decode_count": 0,
            "business_backfill_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "known_error_count": len(previous_errors),
            "decoded": [],
            "errors": list(previous_errors.values())[-20:],
            "pcap_states": previous_state.get("pcap_states", []) if isinstance(previous_state, dict) else [],
        }
        _write_json(_worker_state_path(data_dir), payload)
        return payload

    decode_limit = max(1, int(limit))
    # A few bad or slow recent pcaps should leave an auditable gap, but they
    # should not consume the whole success budget for this pass.
    max_decode_attempts = decode_limit * 3

    for path in _iter_stable_live_pcaps(
        data_dir=data_dir,
        stable_seconds=stable_seconds,
        max_age_seconds=max_capture_age_seconds,
        min_mtime=previous_cursor_mtime if use_cursor else 0.0,
        newest_first=newest_first,
        now=now_epoch,
    ):
        if len(decoded) >= decode_limit or decode_attempts >= max_decode_attempts or scanned >= max_scanned:
            break
        scanned += 1
        try:
            path_mtime = float(path.stat().st_mtime)
            latest_scanned_mtime = max(path_mtime, latest_scanned_mtime)
            latest_scanned_pcap = str(path)
        except OSError:
            path_mtime = 0.0
        try:
            digest = _sha256_file(path)
        except OSError as exc:
            if _is_transient_capture_access_error(exc):
                skipped_item = {"path": str(path), "reason": "locked_active_capture", "error": str(exc)}
                skipped.append(skipped_item)
                pcap_state_updates.append(
                    _pcap_state_item(path, "", status="skipped", reason="locked_active_capture", extra=skipped_item)
                )
                continue
            errors.append({"path": str(path), "error": str(exc)})
            pcap_state_updates.append(_pcap_state_item(path, "", status="failed", reason="hash_error", extra={"error": str(exc)}))
            cursor_blocked = True
            continue
        target_stream_ids = _target_stream_ids_for_pcap(path, max_streams=max_streams)
        if not target_stream_ids:
            skipped_item = {"path": str(path), "reason": "no_target_stream"}
            skipped.append(skipped_item)
            pcap_state_updates.append(_pcap_state_item(path, digest, status="skipped", reason="no_target_stream"))
            if not cursor_blocked and path_mtime:
                confirmed_cursor_mtime = max(path_mtime, confirmed_cursor_mtime)
                confirmed_cursor_pcap = str(path)
            continue
        backfill_sources = decoded_sources_by_digest.get(digest) or []
        if (
            _capture_streams_fully_decoded(decoded_digests, decoded_streams_by_digest, digest, target_stream_ids)
            or (digest in decoded_digests and bool(backfill_sources))
        ):
            should_backfill = bool(backfill_sources) and business_backfill_attempts < decode_limit
            skipped.append(
                {
                    "path": str(path),
                    "reason": "already_decoded",
                    "target_stream_ids": target_stream_ids,
                    "decoded_stream_ids": sorted(decoded_streams_by_digest.get(digest) or set()),
                    "business_backfill_source_count": len(backfill_sources),
                    "business_backfill_queued": should_backfill,
                }
            )
            if should_backfill:
                business_backfill_attempts += 1
                decoded.append(
                    {
                        "path": str(path),
                        "decoded_count": 0,
                        "runtime_protocol_count": 0,
                        "worship_protocol_count": 0,
                        "already_decoded": True,
                        "decoded_sources": backfill_sources,
                    }
            )
            pcap_state_updates.append(_pcap_state_item(path, digest, status="already_decoded", reason="digest_seen"))
            if not cursor_blocked and path_mtime:
                confirmed_cursor_mtime = max(path_mtime, confirmed_cursor_mtime)
                confirmed_cursor_pcap = str(path)
            continue
        previous_error = previous_errors.get(digest)
        if previous_error:
            last_error_at = float(previous_error.get("last_error_at_epoch") or 0)
            if retry_seconds and now_epoch - last_error_at < retry_seconds:
                retry_after = round(retry_seconds - (now_epoch - last_error_at), 3)
                skipped_item = {
                    "path": str(path),
                    "reason": "recent_error",
                    "retry_after_seconds": retry_after,
                    "attempts": int(previous_error.get("attempts") or 1),
                }
                skipped.append(skipped_item)
                pcap_state_updates.append(
                    _pcap_state_item(path, digest, status="recent_error", reason="retry_backoff", extra=skipped_item)
                )
                cursor_blocked = True
                continue
        decode_attempts += 1
        try:
            result = _decode_runtime_capture_with_timeout(
                path,
                data_dir=data_dir,
                max_streams=max(1, int(max_streams)),
            )
            actual_decoded_stream_ids = _decoded_stream_ids_from_result(result)
            if (
                not actual_decoded_stream_ids
                and int(result.get("decoded_count") or 0) > 0
                and not result.get("decoded")
            ):
                actual_decoded_stream_ids = set(target_stream_ids)
            missing_stream_ids = sorted(set(target_stream_ids) - actual_decoded_stream_ids)
            decoded_digests.add(digest)
            decoded_streams_by_digest.setdefault(digest, set()).update(actual_decoded_stream_ids)
            if not missing_stream_ids:
                previous_errors.pop(digest, None)
            decoded.append(
                {
                    "path": str(path),
                    "decoded_count": result.get("decoded_count") or 0,
                    "runtime_protocol_count": result.get("runtime_protocol_count") or 0,
                    "worship_protocol_count": result.get("worship_protocol_count") or 0,
                    "packet_runtime_sync": result.get("packet_runtime_sync") or {},
                    "mail_packet_sync": result.get("mail_packet_sync") or {},
                    "target_stream_ids": target_stream_ids,
                    "decoded_stream_ids": sorted(actual_decoded_stream_ids),
                    "missing_stream_ids": missing_stream_ids,
                    "decoded_sources": _decoded_sources_from_result(path, digest, result),
                }
            )
            if missing_stream_ids:
                attempts = int((previous_error or {}).get("attempts") or 0) + 1
                error_item = {
                    "path": str(path),
                    "digest": digest,
                    "error": "pcap 部分 stream 未完成解码",
                    "target_stream_ids": target_stream_ids,
                    "decoded_stream_ids": sorted(actual_decoded_stream_ids),
                    "missing_stream_ids": missing_stream_ids,
                    "attempts": attempts,
                    "first_error_at": (previous_error or {}).get("first_error_at") or _now_text(),
                    "last_error_at": _now_text(),
                    "last_error_at_epoch": now_epoch,
                    "parser_version": PACKET_INSIGHT_WORKER_SCHEMA_VERSION,
                }
                errors.append(error_item)
                previous_errors[digest] = error_item
                pcap_state_updates.append(
                    _pcap_state_item(path, digest, status="partial_decoded", reason="missing_streams", extra=error_item)
                )
                cursor_blocked = True
                continue
            pcap_state_updates.append(
                _pcap_state_item(
                    path,
                    digest,
                    status="decoded",
                    extra={
                        "decoded_count": result.get("decoded_count") or 0,
                        "runtime_protocol_count": result.get("runtime_protocol_count") or 0,
                        "worship_protocol_count": result.get("worship_protocol_count") or 0,
                        "target_stream_ids": target_stream_ids,
                        "decoded_stream_ids": sorted(actual_decoded_stream_ids),
                    },
                )
            )
            if not cursor_blocked and path_mtime:
                confirmed_cursor_mtime = max(path_mtime, confirmed_cursor_mtime)
                confirmed_cursor_pcap = str(path)
        except Exception as exc:
            if _is_transient_capture_access_error(exc):
                skipped_item = {"path": str(path), "reason": "locked_active_capture", "error": str(exc)}
                skipped.append(skipped_item)
                pcap_state_updates.append(
                    _pcap_state_item(path, digest, status="skipped", reason="locked_active_capture", extra=skipped_item)
                )
                continue
            attempts = int((previous_error or {}).get("attempts") or 0) + 1
            error_item = {
                "path": str(path),
                "digest": digest,
                "error": str(exc),
                "attempts": attempts,
                "first_error_at": (previous_error or {}).get("first_error_at") or _now_text(),
                "last_error_at": _now_text(),
                "last_error_at_epoch": now_epoch,
                "parser_version": PACKET_INSIGHT_WORKER_SCHEMA_VERSION,
            }
            errors.append(error_item)
            pcap_state_updates.append(_pcap_state_item(path, digest, status="failed", reason="decode_error", extra=error_item))
            cursor_blocked = True

    _batch_runtime_sync, batch_mail_sync = _sync_business_after_decoded(decoded, data_dir=data_dir)

    merged_errors = {**previous_errors}
    for error in errors:
        digest = str(error.get("digest") or "").strip()
        if digest:
            merged_errors[digest] = error
    payload = {
        "schema_version": PACKET_INSIGHT_WORKER_SCHEMA_VERSION,
        "ok": not errors,
        "updated_at": _now_text(),
        "cursor_mtime": confirmed_cursor_mtime,
        "cursor_pcap": confirmed_cursor_pcap,
        "confirmed_cursor_mtime": confirmed_cursor_mtime,
        "confirmed_cursor_pcap": confirmed_cursor_pcap,
        "latest_scanned_mtime": latest_scanned_mtime,
        "latest_scanned_pcap": latest_scanned_pcap,
        "has_unconfirmed_gap": cursor_blocked or bool(merged_errors),
        "scanned": scanned,
        "decode_attempts": decode_attempts,
        "business_backfill_attempts": business_backfill_attempts,
        "decoded_count": len(decoded),
        "new_decode_count": sum(1 for item in decoded if not item.get("already_decoded")),
        "business_backfill_count": sum(1 for item in decoded if item.get("already_decoded")),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "known_error_count": len(merged_errors),
        "decoded": decoded,
        "skipped": skipped[-20:],
        "errors": list(merged_errors.values())[-20:],
        "pcap_states": _merge_pcap_states(previous_state, pcap_state_updates),
    }
    _write_json(_worker_state_path(data_dir), payload)
    return payload


def sync_fanxiu_capture_maintenance_backlog(
    *,
    data_dir: str | Path | None = None,
    stable_seconds: float = DEFAULT_STABLE_SECONDS,
    retry_failed_after_seconds: float = 0.0,
    max_streams: int = DEFAULT_DECODE_MAX_STREAMS,
    limit: int = DEFAULT_MAINTENANCE_DECODE_LIMIT,
    include_decoded_record_backlog: bool = False,
    include_activity_packet_sync: bool = False,
    include_historical_business_backlog: bool = True,
    include_mail_business_backlog: bool = True,
) -> dict[str, Any]:
    """Run the non-urgent catch-up pass over historical live captures.

    The live worker is optimized for low latency and recent captures. This
    maintenance pass intentionally ignores the realtime cursor and age window so
    old missed captures, failed gaps, and newly fixed parser rules can be
    retried without depending on UI refreshes or behavior-tree jobs.
    """
    result = sync_fanxiu_live_capture_backlog(
        data_dir=data_dir,
        stable_seconds=stable_seconds,
        retry_failed_after_seconds=retry_failed_after_seconds,
        max_capture_age_seconds=None,
        max_streams=max_streams,
        use_cursor=False,
        newest_first=True,
        limit=limit,
    )
    if include_decoded_record_backlog:
        decoded_record_sync = sync_fanxiu_decoded_record_backlog(data_dir=data_dir, limit=max(64, int(limit) * 4))
    else:
        decoded_record_sync = {
            "ok": True,
            "skipped": True,
            "reason": "default_maintenance_keeps_latest_pcap_backfill_bounded",
        }
    if include_activity_packet_sync:
        activity_sync = sync_fanxiu_activity_packets(data_dir=data_dir, force=False)
    else:
        activity_sync = {
            "ok": True,
            "skipped": True,
            "reason": "default_maintenance_keeps_latest_pcap_backfill_bounded",
        }
    if include_historical_business_backlog:
        historical_runtime_sync, historical_mail_sync = _sync_historical_business_backlog(data_dir=data_dir)
    else:
        historical_runtime_sync = {
            "ok": True,
            "mode": "historical_business_backlog",
            "skipped": True,
            "reason": "default_maintenance_keeps_latest_pcap_backfill_bounded",
        }
        historical_mail_sync = {
            "ok": True,
            "skipped": True,
            "reason": "default_maintenance_keeps_latest_pcap_backfill_bounded",
        }
    if include_mail_business_backlog:
        bounded_mail_sync = sync_fanxiu_mail_business_backlog(data_dir=data_dir)
    else:
        bounded_mail_sync = {
            "ok": True,
            "mode": "mail_business_backlog",
            "skipped": True,
            "reason": "disabled",
        }
    payload = {
        **result,
        "mode": "maintenance",
        "updated_at": _now_text(),
        "decoded_record_db_sync": decoded_record_sync,
        "activity_packet_sync": activity_sync,
        "historical_runtime_business_sync": historical_runtime_sync,
        "historical_mail_packet_sync": historical_mail_sync,
        "bounded_mail_packet_sync": bounded_mail_sync,
    }
    _write_json(_maintenance_state_path(data_dir), payload)
    return payload


class FanxiuPacketInsightWorker:
    def __init__(
        self,
        *,
        scan_interval_seconds: float = DEFAULT_SCAN_INTERVAL_SECONDS,
        maintenance_interval_seconds: float = DEFAULT_MAINTENANCE_INTERVAL_SECONDS,
        stable_seconds: float = DEFAULT_STABLE_SECONDS,
    ) -> None:
        self.scan_interval_seconds = max(3.0, float(scan_interval_seconds))
        self.maintenance_interval_seconds = max(60.0, float(maintenance_interval_seconds))
        self.stable_seconds = max(1.0, float(stable_seconds))
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._maintenance_stop_event = threading.Event()
        self._maintenance_thread: threading.Thread | None = None
        self._last_realtime_result: dict[str, Any] = {}
        self._last_maintenance_result: dict[str, Any] = {}

    def start(self) -> None:
        with self._lock:
            self._stop_event.clear()
            self._maintenance_stop_event.clear()
            try:
                startup_backstop = _ensure_capture_runtime_from_packet_worker()
            except Exception as exc:
                startup_backstop = {"ok": False, "ensured": False, "error": str(exc)}
            self._last_realtime_result = {
                "ok": bool(startup_backstop.get("ok", True)),
                "updated_at": _now_text(),
                "mode": "packet_worker_startup",
                "capture_runtime_backstop": startup_backstop,
            }
            _write_json(_worker_state_path(), self._last_realtime_result)
            if not (self._thread and self._thread.is_alive()):
                self._thread = threading.Thread(target=self._run_loop, name="fanxiu-packet-realtime-worker", daemon=True)
                self._thread.start()
            if not (self._maintenance_thread and self._maintenance_thread.is_alive()):
                self._maintenance_thread = threading.Thread(
                    target=self._maintenance_loop,
                    name="fanxiu-packet-maintenance-worker",
                    daemon=True,
                )
                self._maintenance_thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            self._maintenance_stop_event.set()
            thread = self._thread
            maintenance_thread = self._maintenance_thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        if maintenance_thread and maintenance_thread.is_alive():
            maintenance_thread.join(timeout=2.0)

    def status(self) -> dict[str, Any]:
        with self._lock:
            realtime = dict(self._last_realtime_result)
            maintenance = dict(self._last_maintenance_result)
            return {
                **realtime,
                "schema_version": PACKET_INSIGHT_WORKER_SCHEMA_VERSION,
                "ok": bool(
                    realtime.get("ok", True)
                    and maintenance.get("ok", True)
                ),
                "updated_at": _now_text(),
                "realtime_running": bool(self._thread and self._thread.is_alive()),
                "maintenance_running": bool(self._maintenance_thread and self._maintenance_thread.is_alive()),
                "realtime_interval_seconds": self.scan_interval_seconds,
                "maintenance_interval_seconds": self.maintenance_interval_seconds,
                "realtime": realtime,
                "maintenance": maintenance,
            }

    def scan_once(self) -> dict[str, Any]:
        capture_backstop = _ensure_capture_runtime_from_packet_worker()
        result = sync_fanxiu_live_capture_backlog(
            stable_seconds=self.stable_seconds,
            use_cursor=True,
            newest_first=True,
            limit=2,
        )
        result["capture_runtime_backstop"] = capture_backstop
        result["mail_business_backlog_sync"] = sync_fanxiu_mail_business_backlog(
            latest_limit=16,
            historical_limit=0,
        )
        result["decoded_record_db_sync"] = {
            "ok": True,
            "skipped": True,
            "reason": "realtime_scan_only_handles_recent_live_capture",
        }
        result["activity_packet_sync"] = {
            "ok": True,
            "skipped": True,
            "reason": "realtime_scan_only_handles_recent_live_capture",
        }
        with self._lock:
            self._last_realtime_result = result
        return result

    def maintenance_once(self) -> dict[str, Any]:
        try:
            capture_backstop = _ensure_capture_runtime_from_packet_worker()
        except Exception as exc:
            capture_backstop = {"ok": False, "ensured": False, "error": str(exc)}
        result = sync_fanxiu_capture_maintenance_backlog(
            stable_seconds=self.stable_seconds,
            include_historical_business_backlog=True,
            include_mail_business_backlog=True,
        )
        result["capture_runtime_backstop"] = capture_backstop
        with self._lock:
            self._last_maintenance_result = result
        return result

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.scan_once()
            except Exception as exc:
                with self._lock:
                    self._last_realtime_result = {"ok": False, "updated_at": _now_text(), "error": str(exc)}
            self._stop_event.wait(self.scan_interval_seconds)

    def _maintenance_loop(self) -> None:
        while not self._maintenance_stop_event.is_set():
            try:
                self.maintenance_once()
            except Exception as exc:
                with self._lock:
                    self._last_maintenance_result = {"ok": False, "updated_at": _now_text(), "error": str(exc)}
            self._maintenance_stop_event.wait(self.maintenance_interval_seconds)


fanxiu_packet_insight_worker = FanxiuPacketInsightWorker()
