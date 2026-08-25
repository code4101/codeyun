from __future__ import annotations

import argparse
import json
import msvcrt
import os
import time
import traceback
from pathlib import Path

from backend.plugins.modules.media_sync.sources import (
    count_local_media_files,
    count_pending_source_candidates,
    pinterest_related_root,
    run_pinterest_candidate_download,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="断点续传 Pinterest 候选库存。")
    parser.add_argument("--user-id", type=int, default=2)
    parser.add_argument("--root-dir", default=r"E:\data\m2510mn")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-stagnant-rounds", type=int, default=3)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.environ["TEMP"]) / "codeyun" / "media-sync",
    )
    return parser.parse_args()


def write_json(path: Path, payload: dict[str, object]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(path)


def main() -> int:
    args = parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    status_path = args.state_dir / "pinterest-backlog-status.json"
    heartbeat_path = args.state_dir / "pinterest-backlog-heartbeat.json"
    lock_path = args.state_dir / "pinterest-backlog.lock"
    started_at = time.time()
    worker_pid = os.getpid()

    lock_file = lock_path.open("a+b")
    lock_file.seek(0)
    if lock_file.read(1) == b"":
        lock_file.write(b"0")
        lock_file.flush()
    lock_file.seek(0)
    try:
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("已有 Pinterest 库存下载进程，本次触发跳过。", flush=True)
        return 0

    state: dict[str, object] = {
        "state": "starting",
        "started_at": started_at,
        "updated_at": started_at,
        "worker_pid": worker_pid,
        "root_dir": args.root_dir,
    }

    def persist_status(**updates: object) -> None:
        state.update(updates)
        state["updated_at"] = time.time()
        write_json(status_path, state)

    def emit(message: str) -> None:
        now = time.time()
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)
        write_json(
            heartbeat_path,
            {
                "updated_at": now,
                "worker_pid": worker_pid,
                "message": message,
            },
        )

    try:
        initial_pending = count_pending_source_candidates(user_id=args.user_id, platform="pinterest")
        stagnant_rounds = 0
        round_index = 0
        persist_status(state="running", initial_pending=initial_pending, pending=initial_pending)
        emit(f"Pinterest 全量库存续传启动：待下载 {initial_pending} 条，目标 {args.root_dir}\\3、pinterest。")

        while True:
            before_pending = count_pending_source_candidates(user_id=args.user_id, platform="pinterest")
            before_files = count_local_media_files(pinterest_related_root(args.root_dir))
            if before_pending <= 0:
                persist_status(state="completed", pending=0, reservoir_files=before_files)
                emit(f"Pinterest 全量库存续传完成：库存 {before_files} 张。")
                return 0

            round_index += 1
            limit = min(max(args.batch_size, 1), before_pending)
            persist_status(
                state="running",
                round=round_index,
                pending=before_pending,
                reservoir_files=before_files,
            )
            emit(f"第 {round_index} 批开始：待下载 {before_pending}，本批上限 {limit}，库存 {before_files}。")
            error = None
            try:
                run_pinterest_candidate_download(
                    user_id=args.user_id,
                    root_dir=args.root_dir,
                    download_limit=limit,
                    log=emit,
                    headless=True,
                )
            except Exception as exc:  # noqa: BLE001 - 守护任务必须保留断点后继续
                error = f"{type(exc).__name__}: {exc}"
                emit(f"第 {round_index} 批中断，将从断点继续：{error}")
                traceback.print_exc()

            after_pending = count_pending_source_candidates(user_id=args.user_id, platform="pinterest")
            after_files = count_local_media_files(pinterest_related_root(args.root_dir))
            progressed = after_pending < before_pending or after_files > before_files
            stagnant_rounds = 0 if progressed else stagnant_rounds + 1
            persist_status(
                state="running" if stagnant_rounds < args.max_stagnant_rounds else "blocked",
                round=round_index,
                pending=after_pending,
                reservoir_files=after_files,
                last_error=error,
                stagnant_rounds=stagnant_rounds,
            )
            emit(
                f"第 {round_index} 批结束：待下载 {after_pending}，库存 {after_files}，"
                f"本轮新增 {max(after_files - before_files, 0)}。"
            )
            if stagnant_rounds >= args.max_stagnant_rounds:
                emit("连续多批没有进展，已安全停止并保留断点，等待下次计划任务重试。")
                return 2
            time.sleep(5 if progressed else 30)
    except BaseException as exc:
        persist_status(state="failed", last_error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        lock_file.close()


if __name__ == "__main__":
    raise SystemExit(main())
