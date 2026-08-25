from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from backend.core.fanxiu.catalog.guide_video_research import parse_guide_video_play_count
from backend.core.fanxiu.catalog.guide_videos import load_guide_video_snapshot
from backend.core.media_download import download_bilibili_media, download_douyin_media
from backend.core.settings import get_settings


GUIDE_VIDEO_DOWNLOAD_SCHEMA_VERSION = 1
DEFAULT_VIDEO_ROOT = Path(r"E:\data\m2510mn")
DEFAULT_POLL_SECONDS = 300.0
DEFAULT_MIN_FREE_GB = 0.0


def guide_video_download_snapshot_path() -> Path:
    return get_settings().data_dir / "fanxiu" / "guide-videos" / "downloads.json"


def guide_video_download_stop_path() -> Path:
    return get_settings().data_dir / "fanxiu" / "guide-videos" / "download-worker.stop"


def guide_video_download_lock_path() -> Path:
    return get_settings().data_dir / "fanxiu" / "guide-videos" / "download-worker.lock"


def _empty_snapshot() -> dict[str, Any]:
    return {
        "schema_version": GUIDE_VIDEO_DOWNLOAD_SCHEMA_VERSION,
        "status": "idle",
        "target_count": 0,
        "done_count": 0,
        "failed_count": 0,
        "current_item_id": "",
        "started_at": 0.0,
        "updated_at": 0.0,
        "worker_pid": 0,
        "error": "",
        "items": [],
    }


def load_guide_video_download_snapshot(path: str | Path | None = None) -> dict[str, Any]:
    snapshot_path = Path(path) if path is not None else guide_video_download_snapshot_path()
    if not snapshot_path.is_file():
        return _empty_snapshot()
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_snapshot()
    if not isinstance(payload, dict):
        return _empty_snapshot()
    snapshot = _empty_snapshot()
    snapshot.update(payload)
    snapshot["schema_version"] = GUIDE_VIDEO_DOWNLOAD_SCHEMA_VERSION
    snapshot["items"] = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    return snapshot


def save_guide_video_download_snapshot(
    snapshot: dict[str, Any], path: str | Path | None = None
) -> Path:
    snapshot_path = Path(path) if path is not None else guide_video_download_snapshot_path()
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _empty_snapshot()
    normalized.update(snapshot)
    normalized["schema_version"] = GUIDE_VIDEO_DOWNLOAD_SCHEMA_VERSION
    normalized["items"] = [item for item in snapshot.get("items") or [] if isinstance(item, dict)]
    normalized["done_count"] = sum(item.get("status") == "done" for item in normalized["items"])
    normalized["failed_count"] = sum(item.get("status") == "error" for item in normalized["items"])
    normalized["updated_at"] = time.time()
    temporary = snapshot_path.with_name(
        f".{snapshot_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    temporary.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, snapshot_path)
    return snapshot_path


def download_record_by_item_id(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("item_id") or ""): item
        for item in snapshot.get("items") or []
        if str(item.get("item_id") or "")
    }


def guide_video_download_priority(item: dict[str, Any]) -> tuple[int, int, int, int]:
    role_rank = {"original": 4, "clip": 3, "guide": 2, "official": 1}.get(
        str(item.get("source_role") or ""), 0
    )
    return (
        1 if item.get("is_pinned") else 0,
        parse_guide_video_play_count(item.get("play_text")),
        role_rank,
        int(item.get("published_at") or 0),
    )


def rank_guide_video_download_candidates(
    catalog_items: Iterable[dict[str, Any]],
    download_items: Iterable[dict[str, Any]] = (),
    *,
    now: float | None = None,
) -> list[dict[str, Any]]:
    current_time = float(now if now is not None else time.time())
    records = {
        str(item.get("item_id") or ""): item
        for item in download_items
        if str(item.get("item_id") or "")
    }
    candidates = []
    for item in catalog_items:
        item_id = str(item.get("item_id") or "")
        record = records.get(item_id, {})
        if not item_id or record.get("status") == "done":
            continue
        if float(record.get("retry_after") or 0) > current_time:
            continue
        if str(item.get("platform") or "") not in {"bilibili", "douyin"}:
            continue
        candidates.append(item)
    candidates.sort(key=guide_video_download_priority, reverse=True)
    return candidates


def _upsert_record(snapshot: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    item_id = str(record.get("item_id") or "")
    records = download_record_by_item_id(snapshot)
    records[item_id] = {**records.get(item_id, {}), **record, "item_id": item_id}
    return {**snapshot, "items": list(records.values())}


def _download_item(
    item: dict[str, Any],
    *,
    root_dir: Path,
    browser: Any,
    log: Callable[[str], None],
) -> Any:
    platform = str(item.get("platform") or "")
    url = str(item.get("url") or "")
    if platform == "bilibili":
        return download_bilibili_media(url, root_dir=root_dir, browser=browser, log=log)
    if platform == "douyin":
        return download_douyin_media(url, root_dir=root_dir, browser=browser, log=log)
    raise ValueError(f"不支持的平台：{platform}")


@contextmanager
def _single_worker_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            if stream.tell() == 0 and path.stat().st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("攻略视频下载 worker 已在运行") from exc
        yield
    finally:
        if os.name == "nt":
            try:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        stream.close()


def run_serial_guide_video_downloads(
    *,
    root_dir: str | Path = DEFAULT_VIDEO_ROOT,
    continuous: bool = False,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    min_free_gb: float = DEFAULT_MIN_FREE_GB,
    max_items: int | None = None,
    browser: Any | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    root = Path(root_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    snapshot_path = guide_video_download_snapshot_path()
    stop_path = guide_video_download_stop_path()
    stop_path.unlink(missing_ok=True)
    processed = 0

    with _single_worker_lock(guide_video_download_lock_path()):
        if browser is None:
            from DrissionPage import Chromium

            browser = Chromium()
        snapshot = load_guide_video_download_snapshot(snapshot_path)
        snapshot.update(
            status="running",
            started_at=float(snapshot.get("started_at") or time.time()),
            worker_pid=os.getpid(),
            error="",
        )
        save_guide_video_download_snapshot(snapshot, snapshot_path)

        while True:
            if stop_path.exists():
                snapshot.update(status="stopped", current_item_id="", worker_pid=0)
                save_guide_video_download_snapshot(snapshot, snapshot_path)
                return snapshot
            free_gb = shutil.disk_usage(root).free / (1024**3)
            if free_gb < float(min_free_gb):
                snapshot.update(
                    status="paused_low_disk",
                    current_item_id="",
                    worker_pid=0,
                    error=f"剩余磁盘 {free_gb:.1f} GB，低于保护线 {min_free_gb:.1f} GB",
                )
                save_guide_video_download_snapshot(snapshot, snapshot_path)
                return snapshot

            catalog = load_guide_video_snapshot()
            snapshot["target_count"] = len(catalog.get("items") or [])
            candidates = rank_guide_video_download_candidates(
                catalog.get("items") or [], snapshot.get("items") or []
            )
            if not candidates:
                snapshot.update(status="waiting" if continuous else "done", current_item_id="")
                save_guide_video_download_snapshot(snapshot, snapshot_path)
                if not continuous:
                    return snapshot
                time.sleep(max(float(poll_seconds), 1.0))
                snapshot = load_guide_video_download_snapshot(snapshot_path)
                snapshot.update(status="running", worker_pid=os.getpid(), error="")
                continue

            item = candidates[0]
            item_id = str(item.get("item_id") or "")
            previous = download_record_by_item_id(snapshot).get(item_id, {})
            attempts = int(previous.get("attempts") or 0) + 1
            snapshot.update(status="running", current_item_id=item_id, error="")
            snapshot = _upsert_record(
                snapshot,
                {
                    "item_id": item_id,
                    "status": "running",
                    "attempts": attempts,
                    "started_at": time.time(),
                    "title": str(item.get("title") or ""),
                    "url": str(item.get("url") or ""),
                    "platform": str(item.get("platform") or ""),
                },
            )
            save_guide_video_download_snapshot(snapshot, snapshot_path)
            log(f"开始下载 {item_id} {item.get('title') or ''}")
            try:
                result = _download_item(item, root_dir=root, browser=browser, log=log)
                snapshot = _upsert_record(
                    snapshot,
                    {
                        "item_id": item_id,
                        "status": "done",
                        "finished_at": time.time(),
                        "retry_after": 0,
                        "error": "",
                        "local_video_path": str(result.video_path),
                        "duration": float(result.duration),
                        "width": int(result.width),
                        "height": int(result.height),
                        "reused": bool(result.reused),
                    },
                )
                log(f"完成 {item_id} -> {result.video_path}")
            except Exception as exc:
                retry_seconds = min(3600.0 * (2 ** min(attempts - 1, 4)), 86400.0)
                snapshot = _upsert_record(
                    snapshot,
                    {
                        "item_id": item_id,
                        "status": "error",
                        "finished_at": time.time(),
                        "retry_after": time.time() + retry_seconds,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                log(f"失败 {item_id}: {type(exc).__name__}: {exc}")
            finally:
                snapshot["current_item_id"] = ""
                save_guide_video_download_snapshot(snapshot, snapshot_path)
            processed += 1
            if max_items is not None and processed >= max(int(max_items), 0):
                snapshot.update(status="stopped_after_limit", current_item_id="", worker_pid=0)
                save_guide_video_download_snapshot(snapshot, snapshot_path)
                return snapshot


def request_guide_video_download_stop() -> Path:
    path = guide_video_download_stop_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time()), encoding="utf-8")
    return path


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="串行下载凡修图鉴攻略视频")
    parser.add_argument("--root-dir", default=str(DEFAULT_VIDEO_ROOT))
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--min-free-gb", type=float, default=DEFAULT_MIN_FREE_GB)
    parser.add_argument("--max-items", type=int)
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if args.stop:
        print(request_guide_video_download_stop())
        return
    run_serial_guide_video_downloads(
        root_dir=args.root_dir,
        continuous=args.continuous,
        poll_seconds=args.poll_seconds,
        min_free_gb=args.min_free_gb,
        max_items=args.max_items,
    )


if __name__ == "__main__":
    main()
