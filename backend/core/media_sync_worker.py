from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from filelock import FileLock

from backend.core.settings import get_settings
from backend.core.services.launcher import popen_python_module_service
from backend.core.temp_paths import codeyun_temp_root


def _state_root() -> Path:
    root = get_settings().data_dir / "private-media-sync" / "workers" / "media-sync"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _scope_token(scope_key: str) -> str:
    readable = "".join(char if char.isalnum() or char in "._-" else "_" for char in scope_key)
    readable = readable.strip("._-")[:40] or "default"
    digest = hashlib.sha256(scope_key.encode("utf-8")).hexdigest()[:12]
    return f"{readable}-{digest}"


def worker_state_path(user_id: int, scope_key: str) -> Path:
    return _state_root() / f"user-{int(user_id)}-{_scope_token(str(scope_key or 'default'))}.json"


def media_sync_worker_lane(config: dict[str, Any]) -> str:
    """Return the concurrency lane used by one media-sync worker.

    Different platforms own different directory trees and may run concurrently.
    Within one platform, known discovery and curation workflows keep their
    existing independent lanes; workflows that touch several platform resources
    use a platform-wide lane instead of blocking unrelated platforms globally.
    Truly mixed or unknown workflows remain globally exclusive.
    """

    sources = {
        str(source or "").strip()
        for source in config.get("requested_sources", [])
        if str(source or "").strip()
    }
    discovery_sources = {"pixiv_download", "pinterest_collect_ids"}
    curation_sources = {"pixiv_curate", "pinterest_curate", "video_curate"}

    def source_platform(source: str) -> str | None:
        for platform in ("pixiv", "pinterest", "video"):
            if source == platform or source.startswith(f"{platform}_"):
                return platform
        return None

    platforms = {source_platform(source) for source in sources}
    if not sources or None in platforms or len(platforms) != 1:
        return "exclusive"

    platform = next(iter(platforms))
    if sources <= discovery_sources:
        return f"discovery:{platform}"
    if sources <= curation_sources:
        return f"curation:{platform}"
    return f"platform:{platform}"


def _state_paths_for_scope(user_id: int, scope_key: str) -> list[Path]:
    legacy_path = worker_state_path(user_id, scope_key)
    return [
        path
        for path in _state_root().glob(f"{legacy_path.stem}*.json")
        if (_read_json(path) or {}).get("scope_key", scope_key) == scope_key
    ]


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _locked_update(path: Path, update: dict[str, Any]) -> dict[str, Any]:
    with FileLock(str(path) + ".lock"):
        payload = _read_json(path) or {}
        payload.update(copy.deepcopy(update))
        payload["heartbeat_at"] = time.time()
        _write_json(path, payload)
        return payload


def _pid_is_alive(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        import psutil

        return psutil.pid_exists(value) and psutil.Process(value).is_running()
    except Exception:
        try:
            os.kill(value, 0)
        except OSError:
            return False
        return True


def _mark_stale(payload: dict[str, Any], path: Path) -> dict[str, Any]:
    launch_age = time.time() - float(payload.get("created_at") or 0)
    if (
        not payload.get("running")
        or _pid_is_alive(payload.get("pid"))
        or (not payload.get("pid") and launch_age <= 10)
    ):
        return payload
    now = time.time()
    stale = {
        **payload,
        "running": False,
        "cancel_requested": False,
        "stage": "error",
        "message": "独立媒体采集 Worker 已退出，任务未正常收尾。",
        "finished_at": payload.get("finished_at") or now,
        "updated_at": now,
        "error": payload.get("error") or "媒体采集 Worker 进程异常退出。",
    }
    _locked_update(path, stale)
    return stale


def read_worker_snapshot(user_id: int, scope_key: str | None = None) -> dict[str, Any] | None:
    if scope_key is not None:
        candidates: list[tuple[float, Path, dict[str, Any]]] = []
        for path in _state_paths_for_scope(user_id, scope_key):
            payload = _read_json(path)
            if payload:
                candidates.append((float(payload.get("updated_at") or 0), path, payload))
        if not candidates:
            return None
        _, path, payload = max(candidates, key=lambda item: item[0])
        return _mark_stale(payload, path)

    candidates: list[tuple[float, Path, dict[str, Any]]] = []
    for path in _state_root().glob(f"user-{int(user_id)}-*.json"):
        payload = _read_json(path)
        if payload:
            candidates.append((float(payload.get("updated_at") or 0), path, payload))
    if not candidates:
        return None
    _, path, payload = max(candidates, key=lambda item: item[0])
    return _mark_stale(payload, path)


def _running_workers() -> list[tuple[Path, dict[str, Any]]]:
    running: list[tuple[Path, dict[str, Any]]] = []
    for path in _state_root().glob("user-*.json"):
        payload = _read_json(path)
        if not payload:
            continue
        payload = _mark_stale(payload, path)
        if payload.get("running"):
            running.append((path, payload))
    return running


def _worker_lanes_conflict(left: str, right: str) -> bool:
    def parse_lane(value: str) -> tuple[str, frozenset[str]]:
        mode, separator, platform_text = str(value or "").partition(":")
        if not separator or mode not in {"discovery", "curation", "platform"}:
            return "exclusive", frozenset()
        platforms = frozenset(item for item in platform_text.split("+") if item)
        if not platforms:
            return "exclusive", frozenset()
        return mode, platforms

    left_mode, left_platforms = parse_lane(left)
    right_mode, right_platforms = parse_lane(right)
    if left_mode == "exclusive" or right_mode == "exclusive":
        return True
    if left_platforms.isdisjoint(right_platforms):
        return False
    if left_mode == "platform" or right_mode == "platform":
        return True
    return left_mode == right_mode


def prepare_media_sync_worker(config: dict[str, Any], initial_state: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    with FileLock(str(_state_root() / "launch.lock")):
        worker_lane = media_sync_worker_lane(config)
        conflicting = [
            payload
            for _path, payload in _running_workers()
            if _worker_lanes_conflict(
                worker_lane,
                str(payload.get("worker_lane") or media_sync_worker_lane(payload.get("config") or {})),
            )
        ]
        if conflicting:
            raise RuntimeError(f"已有同通道媒体任务正在运行：{worker_lane}。")

        user_id = int(config["user_id"])
        scope_key = str(config.get("scope_key") or "default")
        run_id = uuid.uuid4().hex
        legacy_path = worker_state_path(user_id, scope_key)
        path = legacy_path.with_name(f"{legacy_path.stem}-{_scope_token(worker_lane)}.json")
        now = time.time()
        payload = {
            **copy.deepcopy(initial_state),
            "run_id": run_id,
            "worker_kind": "media-sync",
            "user_id": user_id,
            "scope_key": scope_key,
            "worker_lane": worker_lane,
            "config": copy.deepcopy(config),
            "pid": None,
            "created_at": now,
            "heartbeat_at": now,
        }
        with FileLock(str(path) + ".lock"):
            _write_json(path, payload)
        return path, payload


def launch_media_sync_legacy_worker(config: dict[str, Any], initial_state: dict[str, Any]) -> dict[str, Any]:
    path, payload = prepare_media_sync_worker(config, initial_state)

    log_root = codeyun_temp_root("media-sync", "worker")
    log_root.mkdir(parents=True, exist_ok=True)
    stdout_path = log_root / f"{payload['run_id']}.stdout.log"
    stderr_path = log_root / f"{payload['run_id']}.stderr.log"
    repo_root = Path(__file__).resolve().parents[2]
    with stdout_path.open("ab") as stdout_file, stderr_path.open("ab") as stderr_file:
        process = popen_python_module_service(
            "backend.core.media_sync_worker",
            "--state",
            str(path),
            preferred_root=repo_root,
            cwd=repo_root,
            stdout=stdout_file,
            stderr=stderr_file,
        )
    return _locked_update(
        path,
        {
            "pid": process.pid,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "updated_at": time.time(),
        },
    )


def launch_media_sync_local_job(config: dict[str, Any], initial_state: dict[str, Any]) -> dict[str, Any]:
    from backend.core.jobs.local_runtime import submit_local_job

    path, _payload = prepare_media_sync_worker(config, initial_state)
    try:
        local_run = submit_local_job(
            job_type="media.sync",
            user_id=int(config["user_id"]),
            payload={"state_path": str(path)},
            resource_key=f"resource:media-sync:{media_sync_worker_lane(config)}",
        )
    except Exception as exc:
        _locked_update(
            path,
            {
                "running": False,
                "stage": "error",
                "message": "无法启动媒体采集本地任务。",
                "error": str(exc),
                "finished_at": time.time(),
            },
        )
        raise
    return _locked_update(
        path,
        {
            "local_job_run_id": local_run.id,
            "updated_at": time.time(),
        },
    )


def launch_media_sync_worker(config: dict[str, Any], initial_state: dict[str, Any]) -> dict[str, Any]:
    """Compatibility entrypoint; active media runs now use the generic Local Job layer."""

    return launch_media_sync_local_job(config, initial_state)


def request_worker_cancel(user_id: int, scope_key: str | None = None) -> bool:
    targets: list[Path]
    if scope_key is not None:
        targets = sorted(
            _state_paths_for_scope(user_id, scope_key),
            key=lambda path: float((_read_json(path) or {}).get("updated_at") or 0),
            reverse=True,
        )
    else:
        targets = list(_state_root().glob(f"user-{int(user_id)}-*.json"))
    for path in targets:
        payload = _read_json(path)
        if not payload or not _mark_stale(payload, path).get("running"):
            continue
        if payload.get("cancel_requested"):
            raise RuntimeError("已经请求停止，等待当前步骤安全退出。")
        stamp = time.strftime("%H:%M:%S")
        logs = list(payload.get("logs") or [])
        logs.append(f"[{stamp}] 已请求停止独立媒体采集 Worker。")
        _locked_update(
            path,
            {
                "cancel_requested": True,
                "message": "已请求停止，等待当前步骤安全退出。",
                "updated_at": time.time(),
                "logs": logs[-300:],
            },
        )
        return True
    return False


def _enqueue_membership_followups(
    path: Path,
    config: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Hand local curation off without sharing its execution lock."""

    if snapshot.get("stage") != "finished" or snapshot.get("error"):
        return {}
    requested_sources = {
        str(source or "").strip() for source in config.get("requested_sources", [])
    }
    summary = snapshot.get("summary") or {}
    followups: dict[str, Any] = {}
    for platform in ("pixiv", "pinterest"):
        curate_source = f"{platform}_curate"
        if curate_source not in requested_sources or curate_source not in summary:
            continue
        try:
            from backend.core.media_membership_reconcile import enqueue_media_membership_reconcile

            followup = enqueue_media_membership_reconcile(
                user_id=int(config["user_id"]),
                platform=platform,
                root_dir=str(config.get("root_dir") or ""),
            )
            followups[platform] = followup
            action = "已创建" if followup["queued"] else "已复用"
            logs = list((_read_json(path) or {}).get("logs") or [])
            logs.append(
                f"[{time.strftime('%H:%M:%S')}] {platform.title()} 站内喜好补齐任务{action}："
                f"{followup['local_job_run_id']}"
            )
            _locked_update(path, {"membership_followups": copy.deepcopy(followups), "logs": logs[-300:]})
        except Exception as exc:
            # The daily scheduler is the second durable reconciliation path.
            followups[platform] = {"queued": False, "error": str(exc)}
            _locked_update(path, {"membership_followups": copy.deepcopy(followups)})
    return followups


def _run_worker(path: Path) -> int:
    payload = _read_json(path)
    if not payload:
        return 2
    config = payload.get("config")
    if not isinstance(config, dict):
        _locked_update(path, {"running": False, "stage": "error", "error": "Worker 配置缺失。"})
        return 2

    from backend.plugins.modules.media_sync.runtime import JobState, SyncJobManager

    class PersistentWorkerManager(SyncJobManager):
        def _persist_snapshot(self, user_id: int, scope_key: str | None = None) -> None:
            state_key = self._existing_state_key(user_id, scope_key)
            with self._lock:
                state = self._states.get(state_key) or JobState()
                snapshot = copy.deepcopy(state.__dict__)
            persisted = _read_json(path) or {}
            if persisted.get("cancel_requested"):
                snapshot["cancel_requested"] = True
                snapshot["message"] = str(
                    persisted.get("message") or "已请求停止，等待当前步骤安全退出。"
                )
            _locked_update(path, {**snapshot, "pid": os.getpid(), "updated_at": time.time()})

        def _set_state(self, user_id: int, *, scope_key: str | None = None, **changes: Any) -> None:
            super()._set_state(user_id, scope_key=scope_key, **changes)
            self._persist_snapshot(user_id, scope_key)

        def _append_log(self, user_id: int, message: str, *, scope_key: str | None = None) -> None:
            super()._append_log(user_id, message, scope_key=scope_key)
            self._persist_snapshot(user_id, scope_key)

        def _raise_if_cancel_requested(self, user_id: int) -> None:
            persisted = _read_json(path) or {}
            if persisted.get("cancel_requested"):
                state_key = self._existing_state_key(user_id)
                with self._lock:
                    state = self._states.setdefault(state_key, JobState())
                    state.running = True
                    state.cancel_requested = True
            super()._raise_if_cancel_requested(user_id)

    manager = PersistentWorkerManager()
    scope_key = str(config.get("scope_key") or "default")
    state_fields = JobState.__dataclass_fields__
    state_payload = {key: copy.deepcopy(payload[key]) for key in state_fields if key in payload}
    manager._states[manager._state_key(int(config["user_id"]), scope_key)] = JobState(**state_payload)
    _locked_update(path, {"pid": os.getpid(), "updated_at": time.time()})
    manager._run_job(config)
    manager._persist_snapshot(int(config["user_id"]), scope_key)
    final_snapshot = _read_json(path) or {}
    _enqueue_membership_followups(path, config, final_snapshot)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a detached media-sync job.")
    parser.add_argument("--state", type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="只验证独立 Worker 的运行依赖，不连接 dev.py 或启动采集。",
    )
    args = parser.parse_args(argv)
    if args.check:
        from backend.plugins.modules.media_sync.runtime import JobState, SyncJobManager

        print(
            json.dumps(
                {
                    "ok": True,
                    "worker_kind": "media-sync",
                    "manager": SyncJobManager.__name__,
                    "state": JobState.__name__,
                    "pid": os.getpid(),
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.state is None:
        parser.error("--state is required unless --check is used")
    return _run_worker(args.state.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
