from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pyxllib.prog import process_runtime
from sqlalchemy import text

from backend.core.fanxiu.packet.insight_worker import fanxiu_packet_insight_worker
from backend.core.fanxiu.runtime.capture_runtime import (
    FANXIU_CAPTURE_RUNTIME_SERVICE_KEY,
    FANXIU_CAPTURE_RUNTIME_WATCHDOG_REASON,
    fanxiu_capture_runtime_service,
)
from backend.core.runtime.process_launcher import popen_python_module_service
from backend.core.settings import ROOT_DIR, get_settings


FANXIU_PACKET_SERVICE_MODULE = "backend.services.fanxiu_packet_daemon"
FANXIU_PACKET_SERVICE_TITLE = "凡修抓包"
PYTHON_PROCESS_NAMES = {"py.exe", "py", "python.exe", "python", "pythonw.exe", "pythonw"}


class FanxiuPacketServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class FanxiuPacketServiceProcess:
    pid: int
    parent_pid: int | None
    name: str
    cmdline: str
    started_at: float | None = None


def get_fanxiu_packet_service_log_path() -> Path:
    configured = (os.getenv("FX_PACKET_SERVICE_LOG") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return (get_settings().data_dir / "logs" / "fanxiu-packet-service.log").resolve(strict=False)


def get_fanxiu_packet_service_state_path() -> Path:
    configured = (os.getenv("FX_PACKET_SERVICE_STATE") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return (get_settings().data_dir / "fanxiu" / "packet-insights" / "packet_service_state.json").resolve(strict=False)


def _capture_watchdog_interval_seconds() -> float:
    try:
        return float(os.getenv("FX_CAPTURE_RUNTIME_WATCHDOG_INTERVAL_SECONDS") or 60)
    except (TypeError, ValueError):
        return 60.0


def _safe_cmdline(proc: Any) -> list[str]:
    try:
        return [str(part) for part in proc.cmdline()]
    except Exception:
        return []


def _safe_name(proc: Any) -> str:
    try:
        return str(proc.name() or "")
    except Exception:
        return ""


def _safe_ppid(proc: Any) -> int | None:
    try:
        return int(proc.ppid())
    except Exception:
        return None


def _safe_started_at(proc: Any) -> float | None:
    try:
        return float(proc.create_time())
    except Exception:
        return None


def _matches_fanxiu_packet_service_process(proc: Any) -> bool:
    cmdline = _safe_cmdline(proc)
    if not cmdline:
        return False
    for index, part in enumerate(cmdline[:-1]):
        if part == "-m" and cmdline[index + 1] == FANXIU_PACKET_SERVICE_MODULE:
            return True
    return FANXIU_PACKET_SERVICE_MODULE in " ".join(cmdline)


def list_fanxiu_packet_service_processes() -> list[dict[str, Any]]:
    current_pid = os.getpid()
    items: list[FanxiuPacketServiceProcess] = []
    for proc in process_runtime.process_candidates_by_name(PYTHON_PROCESS_NAMES):
        if int(proc.pid) == current_pid:
            continue
        if not _matches_fanxiu_packet_service_process(proc):
            continue
        items.append(
            FanxiuPacketServiceProcess(
                pid=int(proc.pid),
                parent_pid=_safe_ppid(proc),
                name=_safe_name(proc),
                cmdline=" ".join(_safe_cmdline(proc)),
                started_at=_safe_started_at(proc),
            )
        )
    matched_pids = {item.pid for item in items}
    items = [item for item in items if item.parent_pid not in matched_pids]
    items.sort(key=lambda item: (item.started_at or 0, item.pid))
    return [asdict(item) for item in items]


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        if not path.is_file():
            return fallback
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _parse_local_datetime(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except (OSError, ValueError):
            return None
    text_value = str(value or "").strip()
    if not text_value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text_value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text_value)
    except ValueError:
        return None


def _age_seconds(value: Any) -> float | None:
    parsed = _parse_local_datetime(value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now() - parsed).total_seconds())


def _worker_substate_age_seconds(worker: dict[str, Any], key: str) -> float | None:
    substate = worker.get(key) if isinstance(worker.get(key), dict) else {}
    return _age_seconds(substate.get("updated_at"))


def _worker_substate_stale(worker: dict[str, Any], key: str, interval_key: str, minimum_age_seconds: float) -> bool:
    age_seconds = _worker_substate_age_seconds(worker, key)
    if age_seconds is None:
        return False
    try:
        interval_seconds = float(worker.get(interval_key) or 0.0)
    except (TypeError, ValueError):
        interval_seconds = 0.0
    stale_after = max(float(minimum_age_seconds), interval_seconds * 2.0 if interval_seconds > 0 else 0.0)
    return age_seconds > stale_after


def _latest_live_capture_summary(capture_runtime: dict[str, Any]) -> dict[str, Any]:
    current_path = Path(str(capture_runtime.get("current_pcap_path") or ""))
    if current_path.is_file():
        try:
            stat = current_path.stat()
            return {
                "path": str(current_path),
                "name": current_path.name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "mtime_text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                "age_seconds": max(0.0, time.time() - stat.st_mtime),
                "source": "current_capture",
            }
        except OSError:
            pass
    live_dir = get_settings().data_dir / "fanxiu" / "tcp-flow" / "live-captures"
    try:
        candidates = [path for path in live_dir.glob("*.pcap") if path.is_file()]
    except OSError:
        candidates = []
    if not candidates:
        return {"path": "", "name": "", "size": 0, "mtime": 0.0, "mtime_text": "", "age_seconds": None}
    latest_with_stat = None
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        if latest_with_stat is None or stat.st_mtime > latest_with_stat[1].st_mtime:
            latest_with_stat = (path, stat)
    if latest_with_stat is None:
        return {"path": "", "name": "", "size": 0, "mtime": 0.0, "mtime_text": "", "age_seconds": None}
    latest, stat = latest_with_stat
    try:
        stat = latest.stat()
    except OSError:
        return {"path": str(latest), "name": latest.name, "size": 0, "mtime": 0.0, "mtime_text": "", "age_seconds": None}
    return {
        "path": str(latest),
        "name": latest.name,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "mtime_text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
        "age_seconds": max(0.0, time.time() - stat.st_mtime),
        "source": "latest_live_dir",
    }


def _mail_database_freshness() -> dict[str, Any]:
    try:
        from backend.db import engine

        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='fanxiumailrecord'")
            ).first()
            if not exists:
                return {"exists": False, "record_count": 0}
            row = conn.execute(
                text(
                    "SELECT COUNT(*) AS record_count, "
                    "MAX(last_seen_capture_at) AS latest_seen_capture_at, "
                    "MAX(updated_at) AS latest_updated_at "
                    "FROM fanxiumailrecord"
                )
            ).mappings().first()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    latest_seen = row.get("latest_seen_capture_at") if row else ""
    latest_updated = row.get("latest_updated_at") if row else ""
    return {
        "ok": True,
        "exists": True,
        "record_count": int((row or {}).get("record_count") or 0),
        "latest_seen_capture_at": latest_seen or "",
        "latest_seen_age_seconds": _age_seconds(latest_seen),
        "latest_updated_at": latest_updated or "",
        "latest_updated_age_seconds": _age_seconds(latest_updated),
    }


def build_fanxiu_packet_service_health(status: dict[str, Any]) -> dict[str, Any]:
    capture = status.get("capture_runtime") if isinstance(status.get("capture_runtime"), dict) else {}
    worker = status.get("packet_worker") if isinstance(status.get("packet_worker"), dict) else {}
    latest_capture = _latest_live_capture_summary(capture)
    mail = _mail_database_freshness()
    issues: list[str] = []
    warnings: list[str] = []
    if not status.get("running"):
        issues.append("daemon_not_running")
    if not capture.get("game_running"):
        issues.append("game_not_running")
    if capture.get("game_running") and not capture.get("tcpdump_ready"):
        issues.append("tcpdump_not_ready")
    if latest_capture.get("age_seconds") is None:
        issues.append("no_live_pcap")
    elif float(latest_capture.get("age_seconds") or 0) > 120:
        issues.append("live_pcap_stale")
    if worker and worker.get("ok") is False:
        issues.append("packet_worker_error")
    if worker and not worker.get("realtime_running"):
        issues.append("realtime_worker_not_running")
    if worker and worker.get("updated_at") and (_age_seconds(worker.get("updated_at")) or 0) > 120:
        issues.append("worker_state_stale")
    if worker and _worker_substate_stale(worker, "realtime", "realtime_interval_seconds", 120.0):
        issues.append("realtime_result_stale")
    if worker and _worker_substate_stale(worker, "maintenance", "maintenance_interval_seconds", 600.0):
        issues.append("maintenance_result_stale")
    if worker and worker.get("skipped") and worker.get("skip_reason"):
        warnings.append(f"worker_skipped:{worker.get('skip_reason')}")
    mail_seen_age = mail.get("latest_seen_age_seconds") if isinstance(mail, dict) else None
    if isinstance(mail_seen_age, (int, float)) and mail_seen_age > 1800:
        warnings.append("mail_database_stale")
    return {
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
        "latest_live_capture": latest_capture,
        "mail_database": mail,
        "worker_updated_age_seconds": _age_seconds(worker.get("updated_at")) if worker else None,
        "worker_realtime_age_seconds": _worker_substate_age_seconds(worker, "realtime") if worker else None,
        "worker_maintenance_age_seconds": _worker_substate_age_seconds(worker, "maintenance") if worker else None,
        "capture_watchdog_age_seconds": _age_seconds(capture.get("watchdog_last_check_at")) if capture else None,
    }


def write_fanxiu_packet_service_state(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "ok": True,
        "service_key": FANXIU_CAPTURE_RUNTIME_SERVICE_KEY,
        "title": FANXIU_PACKET_SERVICE_TITLE,
        "module": FANXIU_PACKET_SERVICE_MODULE,
        "pid": os.getpid(),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "capture_runtime": fanxiu_capture_runtime_service.status(),
        "packet_worker": fanxiu_packet_insight_worker.status(),
    }
    if extra:
        payload.update(extra)
    _write_json(get_fanxiu_packet_service_state_path(), payload)
    return payload


def get_fanxiu_packet_service_status() -> dict[str, Any]:
    processes = list_fanxiu_packet_service_processes()
    running = bool(processes)
    state_payload = _read_json(get_fanxiu_packet_service_state_path(), {})
    if not isinstance(state_payload, dict):
        state_payload = {}
    state = "running" if running else "stopped"
    capture_runtime = state_payload.get("capture_runtime") if isinstance(state_payload.get("capture_runtime"), dict) else {}
    packet_worker = state_payload.get("packet_worker") if isinstance(state_payload.get("packet_worker"), dict) else {}
    status = {
        "key": FANXIU_CAPTURE_RUNTIME_SERVICE_KEY,
        "title": FANXIU_PACKET_SERVICE_TITLE,
        "running": running,
        "state": state,
        "state_label": "运行中" if running else "已停止",
        "module": FANXIU_PACKET_SERVICE_MODULE,
        "cwd": os.fspath(ROOT_DIR),
        "log_path": os.fspath(get_fanxiu_packet_service_log_path()),
        "state_path": os.fspath(get_fanxiu_packet_service_state_path()),
        "process_count": len(processes),
        "processes": processes,
        "pids": [item["pid"] for item in processes if item.get("pid") is not None],
        "updated_at": state_payload.get("updated_at") or "",
        "capture_runtime": capture_runtime,
        "packet_worker": packet_worker,
        "external": True,
        "controllable": True,
    }
    status["health"] = build_fanxiu_packet_service_health(status)
    return status


def get_fanxiu_packet_worker_status() -> dict[str, Any]:
    status = get_fanxiu_packet_service_status()
    worker = status.get("packet_worker") if isinstance(status.get("packet_worker"), dict) else {}
    payload = dict(worker)
    payload.setdefault("running", bool(status.get("running")))
    payload.setdefault("service_running", bool(status.get("running")))
    payload.setdefault("service_state", status.get("state") or "stopped")
    payload.setdefault("service_state_label", status.get("state_label") or "")
    payload.setdefault("process_count", status.get("process_count") or 0)
    payload.setdefault("pids", status.get("pids") or [])
    payload.setdefault("state_path", status.get("state_path") or "")
    payload.setdefault("log_path", status.get("log_path") or "")
    return payload


def start_fanxiu_packet_service(wait_seconds: float = 3.0) -> dict[str, Any]:
    status = get_fanxiu_packet_service_status()
    if status.get("running"):
        return {"status": "started", "service": status}

    log_path = get_fanxiu_packet_service_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    state_path = get_fanxiu_packet_service_state_path()
    env = os.environ.copy()
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "FX_PACKET_SERVICE_LOG": os.fspath(log_path),
            "FX_PACKET_SERVICE_STATE": os.fspath(state_path),
        }
    )
    try:
        with log_path.open("ab") as log_file:
            log_file.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] start Fanxiu packet service\n".encode("utf-8"))
            proc = popen_python_module_service(
                FANXIU_PACKET_SERVICE_MODULE,
                preferred_root=ROOT_DIR,
                cwd=os.fspath(ROOT_DIR),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
    except OSError as exc:
        raise FanxiuPacketServiceError(f"启动凡修抓包服务失败：{exc}") from exc

    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    while time.monotonic() <= deadline:
        status = get_fanxiu_packet_service_status()
        if status.get("running"):
            status["started_pid"] = proc.pid
            return {"status": "started", "service": status}
        if proc.poll() is not None:
            break
        time.sleep(0.2)

    status = get_fanxiu_packet_service_status()
    status["started_pid"] = proc.pid
    if status.get("process_count"):
        return {"status": "starting", "service": status}
    raise FanxiuPacketServiceError(f"已启动凡修抓包服务 PID {proc.pid}，但进程未保持运行。")


def stop_fanxiu_packet_service(timeout: float = 5.0) -> dict[str, Any]:
    processes = list_fanxiu_packet_service_processes()
    for item in processes:
        pid = item.get("pid")
        if pid is None:
            continue
        process_runtime.terminate_process_tree(int(pid), timeout=timeout)
    time.sleep(0.2)
    return {
        "status": "stopped",
        "stopped_pids": [item["pid"] for item in processes if item.get("pid") is not None],
        "service": get_fanxiu_packet_service_status(),
    }


def build_fanxiu_packet_service_log_lines(limit: int = 200) -> list[str]:
    status = get_fanxiu_packet_service_status()
    worker = status.get("packet_worker") if isinstance(status.get("packet_worker"), dict) else {}
    capture = status.get("capture_runtime") if isinstance(status.get("capture_runtime"), dict) else {}
    lines = [
        f"名称：{FANXIU_PACKET_SERVICE_TITLE}",
        f"状态：{status.get('state_label') or '-'}",
        f"模块：{FANXIU_PACKET_SERVICE_MODULE}",
        f"状态文件：{status.get('state_path') or '-'}",
        f"日志：{status.get('log_path') or '-'}",
        (
            "抓包："
            f"{capture.get('state') or '-'}；"
            f"tcpdump={'是' if capture.get('running') else '否'}；"
            f"watchdog={'是' if capture.get('watchdog_running') else '否'}"
        ),
        (
            "解析："
            f"{worker.get('updated_at') or '-'}；"
            f"实时={'是' if worker.get('realtime_running') else '否'}；"
            f"维护={'是' if worker.get('maintenance_running') else '否'}"
        ),
    ]
    pids = status.get("pids") or []
    if pids:
        lines.append(f"PID：{', '.join(str(pid) for pid in pids)}")
    log_path = Path(str(status.get("log_path") or ""))
    if log_path.is_file():
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, int(limit)) :]
        except OSError:
            tail = []
        if tail:
            lines.extend(["", "最近日志：", *tail])
    return lines


def run_fanxiu_packet_service_loop(*, state_interval_seconds: float = 15.0) -> None:
    fanxiu_capture_runtime_service.start_watchdog(interval_seconds=_capture_watchdog_interval_seconds())
    fanxiu_packet_insight_worker.start()
    try:
        while True:
            try:
                write_fanxiu_packet_service_state()
            except Exception as exc:
                print(f"[fanxiu-packet-service] write state failed: {exc}", flush=True)
            time.sleep(max(1.0, float(state_interval_seconds)))
    finally:
        try:
            fanxiu_capture_runtime_service.stop_watchdog()
        finally:
            fanxiu_packet_insight_worker.stop()
