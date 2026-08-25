from __future__ import annotations

"""Background producer for the latest equipped spirit-artifact database fact."""

import logging
import os
import subprocess
import time

from filelock import FileLock, Timeout as FileLockTimeout
from sqlmodel import Session

from backend.db import engine
from backend.core.services.launcher import popen_python_module_service
from backend.core.settings import ROOT_DIR, get_settings

from .spirit_artifact import read_spirit_artifact_hall_runtime
from .spirit_artifact_store import upsert_spirit_artifact_runtime_snapshot


logger = logging.getLogger(__name__)
SPIRIT_ARTIFACT_COLLECTOR_MODULE = "backend.services.fanxiu_spirit_artifact_collector"


def _interval_seconds() -> float:
    try:
        return max(10.0, float(os.getenv("FANXIU_SPIRIT_ARTIFACT_COLLECT_INTERVAL_SECONDS") or 60.0))
    except (TypeError, ValueError):
        return 60.0


def collect_spirit_artifact_snapshot_once() -> dict:
    snapshot = read_spirit_artifact_hall_runtime(force=True)
    with Session(engine) as session:
        upsert_spirit_artifact_runtime_snapshot(session, snapshot)
    return snapshot


def _lock_path():
    path = get_settings().data_dir / "fanxiu" / "instrumentation" / "spirit-artifact-collector.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def run_spirit_artifact_collector_loop() -> None:
    lock = FileLock(str(_lock_path()))
    try:
        lock.acquire(timeout=0)
    except FileLockTimeout:
        return
    interval = _interval_seconds()
    try:
        while True:
            try:
                collect_spirit_artifact_snapshot_once()
            except Exception as exc:
                # 游戏未运行或插桩暂不可用时保留数据库中的上一份有效快照。
                logger.debug("Spirit artifact runtime collection skipped: %s", exc)
            time.sleep(interval)
    finally:
        lock.release()


def ensure_spirit_artifact_collector_service() -> int:
    process = popen_python_module_service(
        SPIRIT_ARTIFACT_COLLECTOR_MODULE,
        preferred_root=ROOT_DIR,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return int(process.pid)
