from __future__ import annotations

import threading
import time
from typing import Any

import psutil
from sqlmodel import Session

from backend.core.devices.device import get_device_id
from backend.models import AppSetting

SYSTEM_METRICS_SAMPLE_INTERVAL_SECONDS = 60
SYSTEM_METRICS_RETENTION_SECONDS = 72 * 60 * 60
SYSTEM_METRICS_DEFAULT_HISTORY_HOURS = 24
SYSTEM_METRICS_SETTING_PREFIX = "system_metrics:"

_monitor_lock = threading.Lock()
_monitor_thread: threading.Thread | None = None
_monitor_stop_event: threading.Event | None = None


def _setting_key(device_id: str) -> str:
    return f"{SYSTEM_METRICS_SETTING_PREFIX}{device_id}"


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if result != result:
        return default
    return result


def _read_current_system_metric_sample(now: float | None = None) -> dict[str, Any]:
    current_time = time.time() if now is None else float(now)
    memory = psutil.virtual_memory()
    return {
        "sampled_at": current_time,
        "cpu_percent": round(max(0.0, min(100.0, _coerce_float(psutil.cpu_percent(interval=None)))), 2),
        "memory_percent": round(max(0.0, min(100.0, _coerce_float(memory.percent))), 2),
        "memory_used": int(getattr(memory, "used", 0) or 0),
        "memory_available": int(getattr(memory, "available", 0) or 0),
        "memory_total": int(getattr(memory, "total", 0) or 0),
    }


def _normalize_sample(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    sampled_at = _coerce_float(raw.get("sampled_at"), -1)
    if sampled_at <= 0:
        return None
    return {
        "sampled_at": sampled_at,
        "cpu_percent": round(max(0.0, min(100.0, _coerce_float(raw.get("cpu_percent")))), 2),
        "memory_percent": round(max(0.0, min(100.0, _coerce_float(raw.get("memory_percent")))), 2),
        "memory_used": max(0, int(_coerce_float(raw.get("memory_used")))),
        "memory_available": max(0, int(_coerce_float(raw.get("memory_available")))),
        "memory_total": max(0, int(_coerce_float(raw.get("memory_total")))),
    }


def _normalize_samples(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    samples = []
    for raw in value.get("samples") or []:
        sample = _normalize_sample(raw)
        if sample is not None:
            samples.append(sample)
    samples.sort(key=lambda sample: sample["sampled_at"])
    return samples


def _prune_samples(samples: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    min_time = now - SYSTEM_METRICS_RETENTION_SECONDS
    pruned = [sample for sample in samples if sample["sampled_at"] >= min_time]
    max_samples = int(SYSTEM_METRICS_RETENTION_SECONDS / SYSTEM_METRICS_SAMPLE_INTERVAL_SECONDS) + 10
    if len(pruned) > max_samples:
        pruned = pruned[-max_samples:]
    return pruned


def record_system_metric_sample(
    session: Session,
    *,
    device_id: str | None = None,
    sample: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target_device_id = device_id or get_device_id()
    current_sample = _normalize_sample(sample or _read_current_system_metric_sample())
    if current_sample is None:
        raise RuntimeError("Invalid system metric sample")

    with _monitor_lock:
        setting = session.get(AppSetting, _setting_key(target_device_id))
        value = dict(setting.value) if setting and isinstance(setting.value, dict) else {}
        samples = _normalize_samples(value)
        samples.append(current_sample)
        samples = _prune_samples(samples, current_sample["sampled_at"])
        payload = {
            "device_id": target_device_id,
            "interval_seconds": SYSTEM_METRICS_SAMPLE_INTERVAL_SECONDS,
            "retention_seconds": SYSTEM_METRICS_RETENTION_SECONDS,
            "samples": samples,
        }

        if setting is None:
            setting = AppSetting(key=_setting_key(target_device_id), value=payload)
        else:
            setting.value = payload
            setting.updated_at = time.time()
        session.add(setting)
        session.commit()
    return current_sample


def _latest_sample(samples: list[dict[str, Any]]) -> dict[str, Any] | None:
    return samples[-1] if samples else None


def _should_collect_now(samples: list[dict[str, Any]], now: float) -> bool:
    latest = _latest_sample(samples)
    if latest is None:
        return True
    return now - float(latest["sampled_at"]) >= SYSTEM_METRICS_SAMPLE_INTERVAL_SECONDS * 0.9


def get_system_metric_history(
    session: Session,
    *,
    device_id: str | None = None,
    hours: int = SYSTEM_METRICS_DEFAULT_HISTORY_HOURS,
    limit: int = 2000,
    collect_if_stale: bool = True,
) -> dict[str, Any]:
    target_device_id = device_id or get_device_id()
    now = time.time()
    setting = session.get(AppSetting, _setting_key(target_device_id))
    samples = _normalize_samples(setting.value if setting else {})

    if collect_if_stale and _should_collect_now(samples, now):
        record_system_metric_sample(session, device_id=target_device_id)
        setting = session.get(AppSetting, _setting_key(target_device_id))
        samples = _normalize_samples(setting.value if setting else {})

    history_hours = max(1, min(int(hours or SYSTEM_METRICS_DEFAULT_HISTORY_HOURS), 72))
    min_time = now - history_hours * 60 * 60
    visible_samples = [sample for sample in samples if sample["sampled_at"] >= min_time]
    max_limit = max(1, min(int(limit or 2000), 5000))
    if len(visible_samples) > max_limit:
        visible_samples = visible_samples[-max_limit:]

    return {
        "device_id": target_device_id,
        "interval_seconds": SYSTEM_METRICS_SAMPLE_INTERVAL_SECONDS,
        "retention_seconds": SYSTEM_METRICS_RETENTION_SECONDS,
        "history_hours": history_hours,
        "latest": _latest_sample(samples),
        "samples": visible_samples,
    }


def _system_metrics_monitor_loop(stop_event: threading.Event, sample_interval: int) -> None:
    from backend.db import engine

    while not stop_event.is_set():
        try:
            with Session(engine) as session:
                record_system_metric_sample(session)
        except Exception as exc:
            print(f"System metrics monitor failed: {exc}")
        stop_event.wait(sample_interval)


def start_system_metrics_monitor(sample_interval: int = SYSTEM_METRICS_SAMPLE_INTERVAL_SECONDS) -> None:
    global _monitor_stop_event, _monitor_thread
    with _monitor_lock:
        if _monitor_thread and _monitor_thread.is_alive():
            return
        psutil.cpu_percent(interval=None)
        stop_event = threading.Event()
        thread = threading.Thread(
            target=_system_metrics_monitor_loop,
            args=(stop_event, max(5, int(sample_interval or SYSTEM_METRICS_SAMPLE_INTERVAL_SECONDS))),
            name="codeyun-system-metrics-monitor",
            daemon=True,
        )
        _monitor_stop_event = stop_event
        _monitor_thread = thread
        thread.start()


def shutdown_system_metrics_monitor() -> None:
    global _monitor_stop_event, _monitor_thread
    with _monitor_lock:
        stop_event = _monitor_stop_event
        thread = _monitor_thread
        _monitor_stop_event = None
        _monitor_thread = None
    if stop_event is not None:
        stop_event.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=2)
