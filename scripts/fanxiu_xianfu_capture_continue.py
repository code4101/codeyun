from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.fanxiu.runtime.behavior_tree import (
    DEFAULT_FANXIU_ENTRY_ID,
    create_fanxiu_runtime_runner,
    data_annotation_asset_tree_path,
)
from backend.core.fanxiu.data_annotation.runtime_runner import (
    _parse_xianfu_visit_cd_seconds,
)
from backend.core.fanxiu.data_annotation.runtime_control import read_scheduler_tasks
from backend.core.temp_paths import codeyun_temp_root
from scripts.fanxiu_xianfu_migration_probe import (
    DEFAULT_OLD_XIANFU_ROOT,
    DEFAULT_SCREENSHOT_DIR,
    audit_xianfu_assets,
    build_candidates,
    install_continue_visit_image,
)


class _LocalEntry:
    mode = "local"


def _print_json(payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    encoding = sys.stdout.encoding or "utf-8"
    sys.stdout.write(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))
    sys.stdout.write("\n")


def _write_frame_data_url(frame: str, path: Path) -> None:
    _header, _sep, encoded = frame.partition(",")
    path.write_bytes(base64.b64decode(encoded or frame))


def _compact_text(text: str, *, max_lines: int = 40, max_chars: int = 12000) -> dict[str, Any]:
    value = str(text or "")
    lines = value.splitlines()
    compacted = len(lines) > max_lines or len(value) > max_chars
    if compacted:
        tail_lines = lines[-max_lines:] if max_lines > 0 else []
        tail = "\n".join(tail_lines)
        if len(tail) > max_chars:
            tail = tail[-max_chars:]
        return {
            "text": tail,
            "line_count": len(lines),
            "char_count": len(value),
            "compacted": True,
        }
    return {
        "text": value,
        "line_count": len(lines),
        "char_count": len(value),
        "compacted": False,
    }


def _parse_scheduler_next_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).replace(microsecond=0)
    except ValueError:
        return None


def _scheduler_wait_plan(
    *,
    task_id: str = "xianfu-visit-partner",
    extra_seconds: float = 600.0,
    now: datetime | None = None,
    tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    task_list = tasks if tasks is not None else read_scheduler_tasks()
    task = next((item for item in task_list if str(item.get("id") or "") == task_id), None)
    next_text = str(task.get("next_time") or "") if isinstance(task, dict) else ""
    target = _parse_scheduler_next_time(next_text)
    current = now or datetime.now()
    seconds_until = max(0.0, (target - current).total_seconds()) if target is not None else None
    timeout_seconds = None if seconds_until is None else max(1.0, seconds_until + max(0.0, float(extra_seconds or 0.0)))
    return {
        "task_id": task_id,
        "next_time": next_text,
        "seconds_until": seconds_until,
        "extra_seconds": max(0.0, float(extra_seconds or 0.0)),
        "timeout_seconds": timeout_seconds,
        "found": task is not None,
    }


def _preflight_report(
    *,
    asset_tree: Path,
    screenshot_dir: Path,
    wait_extra_seconds: float = 600.0,
) -> dict[str, Any]:
    audit = audit_xianfu_assets(
        asset_tree_path=asset_tree,
        screenshot_dir=screenshot_dir,
        audit_ocr=False,
    )
    optional_175 = next((row for row in audit.get("rows") or [] if row.get("number") == 175), {})
    return {
        "ok": bool(audit.get("ok")),
        "asset_audit_ok": bool(audit.get("ok")),
        "image_175_present": bool(optional_175.get("present")),
        "wait_plan": _scheduler_wait_plan(extra_seconds=wait_extra_seconds),
        "asset_audit_output": audit.get("output_json"),
    }


def _current_xianfu_status(runner: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    frame = runner._screencap(ctx)
    scene_id, score = runner._identify_scene_number(ctx, frame, [175, 174, 173, 172, 171, 34])
    image174 = ctx["images"].get(174)
    status_text = ""
    cd_seconds: int | None = None
    if isinstance(image174, dict) and scene_id == 174:
        lines = runner._ocr_fragments_in_shapes(frame, image174, ("状态", "免费提示"), padding=16)
        status_text = runner._ocr_text(lines)
        cd_seconds = _parse_xianfu_visit_cd_seconds(status_text)
    return {
        "frame": frame,
        "scene_id": scene_id,
        "score": score,
        "status_text": status_text,
        "cd_seconds": cd_seconds,
    }


def _public_status(status: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in status.items() if key != "frame"}


def _wait_for_free_status(
    status_provider: Any,
    *,
    wait_until_free: bool,
    wait_timeout_seconds: float,
    poll_seconds: float,
    recover_not_on_174: Any | None = None,
    max_recover_count: int = 0,
    max_unreadable_count: int = 3,
    sleep: Any = time.sleep,
    monotonic: Any = time.monotonic,
    emit: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, int | None]:
    wait_started_at = monotonic()
    recover_count = 0
    unreadable_count = 0
    while True:
        status = status_provider()
        if status["scene_id"] != 174:
            can_recover = (
                wait_until_free
                and recover_not_on_174 is not None
                and (int(max_recover_count or 0) <= 0 or recover_count < int(max_recover_count))
            )
            if can_recover:
                elapsed = monotonic() - wait_started_at
                payload = {
                    "ok": True,
                    "ready": False,
                    "reason": "reprepare_not_on_174",
                    "elapsed_seconds": round(elapsed, 1),
                    "recover_count": recover_count + 1,
                    **_public_status(status),
                }
                if emit is not None:
                    emit(payload)
                recover_result = recover_not_on_174(status)
                recover_count += 1
                if not bool(recover_result.get("ok")):
                    return status, {
                        "ok": False,
                        "reason": "reprepare_failed",
                        "recover_count": recover_count,
                        "recover_runtime": recover_result,
                        **_public_status(status),
                    }, 1
                sleep(1.0)
                continue
            return status, {"ok": False, "reason": "not_on_174", **_public_status(status)}, 2
        if status["cd_seconds"] is None:
            can_retry = wait_until_free and unreadable_count < max(0, int(max_unreadable_count or 0))
            if can_retry:
                unreadable_count += 1
                elapsed = monotonic() - wait_started_at
                payload = {
                    "ok": True,
                    "ready": False,
                    "reason": "retry_cd_unreadable",
                    "elapsed_seconds": round(elapsed, 1),
                    "unreadable_count": unreadable_count,
                    **_public_status(status),
                }
                if emit is not None:
                    emit(payload)
                if recover_not_on_174 is not None:
                    recover_result = recover_not_on_174(status)
                    recover_count += 1
                    if not bool(recover_result.get("ok")):
                        return status, {
                            "ok": False,
                            "reason": "reprepare_failed",
                            "recover_count": recover_count,
                            "recover_runtime": recover_result,
                            **_public_status(status),
                        }, 1
                sleep(min(5.0, max(1.0, float(poll_seconds or 1.0))))
                continue
            return status, {"ok": False, "reason": "cd_unreadable", **_public_status(status)}, 2
        if status["cd_seconds"] <= 0:
            return status, None, None
        if not wait_until_free:
            return status, {"ok": True, "ready": False, "reason": "not_free", **_public_status(status)}, 0
        wait_timeout = float(wait_timeout_seconds or 0.0)
        elapsed = monotonic() - wait_started_at
        if wait_timeout > 0 and elapsed >= wait_timeout:
            return status, {
                "ok": True,
                "ready": False,
                "reason": "free_wait_timeout",
                "elapsed_seconds": round(elapsed, 1),
                **_public_status(status),
            }, 0
        sleep_seconds = max(1.0, float(poll_seconds or 60.0))
        if wait_timeout > 0:
            sleep_seconds = min(sleep_seconds, max(1.0, wait_timeout - elapsed))
        if status["cd_seconds"] and status["cd_seconds"] > 0:
            sleep_seconds = min(sleep_seconds, max(1.0, float(status["cd_seconds"])))
        payload = {
            "ok": True,
            "ready": False,
            "reason": "waiting_free",
            "elapsed_seconds": round(elapsed, 1),
            "next_poll_seconds": round(sleep_seconds, 1),
            **_public_status(status),
        }
        if emit is not None:
            emit(payload)
        sleep(sleep_seconds)


def _run_xianfu_runtime_task(
    *,
    entry_id: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    timeout = float(timeout_seconds or 180.0)
    command = [
        sys.executable,
        str(ROOT / "scripts" / "fanxiu_bt.py"),
        "--entry-id",
        str(entry_id),
        "--timeout-seconds",
        str(timeout),
        "--wait-timeout-seconds",
        str(timeout),
    ]
    command.extend([
        "task",
        "xianfu_visit_partner",
    ])
    process = subprocess.run(
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(30.0, timeout + 30.0),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return {
        "command": command,
        "returncode": process.returncode,
        "stdout": _compact_text(process.stdout),
        "stderr": _compact_text(process.stderr, max_lines=20, max_chars=6000),
        "ok": process.returncode == 0,
    }


def _run_runtime_after_install(
    *,
    entry_id: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    return _run_xianfu_runtime_task(
        entry_id=entry_id,
        timeout_seconds=timeout_seconds,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="免费窗口捕获仙府_寻访仙侣 #175 继续寻访弹窗。")
    parser.add_argument("--entry-id", default=DEFAULT_FANXIU_ENTRY_ID)
    parser.add_argument("--preflight", action="store_true", help="只做捕获前预检，不点击、不等待、不安装")
    parser.add_argument("--confirm-free-click", action="store_true", help="确认当前免费时允许点击 #174「寻访」")
    parser.add_argument("--install", action="store_true", help="OCR 全部复核后安装 #175 到资产树")
    parser.add_argument("--run-runtime-after-install", action="store_true", help="安装 #175 后立即运行 xianfu_visit_partner 完成弹窗处理和 next_time 写入")
    parser.add_argument("--prepare-via-runtime", action="store_true", help="等待前先通过公开 Runtime 跑一次 xianfu_visit_partner，把画面准备到 #174")
    parser.add_argument("--runtime-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--max-reprepare-count", type=int, default=20, help="等待免费期间不在 #174 时，最多重新通过 Runtime 准备的次数；0 表示不限")
    parser.add_argument("--max-unreadable-count", type=int, default=3, help="等待免费期间 #174 倒计时 OCR 不可读时的重试次数")
    parser.add_argument("--wait-until-free", action="store_true", help="未免费时持续轮询，直到可免费寻访或超时")
    parser.add_argument("--free-wait-from-scheduler", action="store_true", help="按 Scheduler 中 xianfu-visit-partner.next_time 自动设置等待上限")
    parser.add_argument("--free-wait-extra-seconds", type=float, default=600.0, help="按 Scheduler 等待时额外保留的秒数")
    parser.add_argument("--free-wait-timeout-seconds", type=float, default=0.0, help="等待免费窗口的最长秒数；0 表示不限制")
    parser.add_argument("--free-poll-seconds", type=float, default=60.0, help="等待免费窗口时的轮询间隔")
    parser.add_argument("--wait-timeout-seconds", type=float, default=18.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--asset-tree", type=Path, default=None)
    parser.add_argument("--screenshot-dir", type=Path, default=DEFAULT_SCREENSHOT_DIR)
    args = parser.parse_args()

    asset_tree = args.asset_tree or data_annotation_asset_tree_path(args.entry_id)
    output_dir = codeyun_temp_root("fanxiu_xianfu_continue_capture")
    if args.preflight:
        _print_json(_preflight_report(
            asset_tree=asset_tree,
            screenshot_dir=args.screenshot_dir,
            wait_extra_seconds=float(args.free_wait_extra_seconds or 0.0),
        ))
        return 0

    wait_plan: dict[str, Any] | None = None
    free_wait_timeout_seconds = float(args.free_wait_timeout_seconds or 0.0)
    if args.free_wait_from_scheduler:
        wait_plan = _scheduler_wait_plan(extra_seconds=float(args.free_wait_extra_seconds or 0.0))
        if free_wait_timeout_seconds <= 0 and wait_plan.get("timeout_seconds") is not None:
            free_wait_timeout_seconds = float(wait_plan["timeout_seconds"])
    prepare_result = None
    prepare_results: list[dict[str, Any]] = []

    def run_prepare() -> dict[str, Any]:
        result = _run_xianfu_runtime_task(
            entry_id=str(args.entry_id),
            timeout_seconds=float(args.runtime_timeout_seconds or 180.0),
        )
        prepare_results.append(result)
        return result

    if args.prepare_via_runtime:
        prepare_result = run_prepare()
        if not bool(prepare_result.get("ok")):
            _print_json({"ok": False, "reason": "prepare_runtime_failed", "prepare_runtime": prepare_result, "prepare_results": prepare_results})
            return 1

    runner = create_fanxiu_runtime_runner()
    tree = runner._load_asset_tree(asset_tree)
    ctx = {
        "entry": _LocalEntry(),
        "asset_tree": tree,
        "asset_tree_path": asset_tree,
        "images": runner._index_images(tree),
    }
    status, exit_payload, exit_code = _wait_for_free_status(
        lambda: _current_xianfu_status(runner, ctx),
        wait_until_free=bool(args.wait_until_free),
        wait_timeout_seconds=free_wait_timeout_seconds,
        poll_seconds=float(args.free_poll_seconds or 60.0),
        recover_not_on_174=(lambda _status: run_prepare()) if bool(args.prepare_via_runtime) else None,
        max_recover_count=int(args.max_reprepare_count or 0),
        max_unreadable_count=int(args.max_unreadable_count or 0),
        emit=_print_json,
    )
    if exit_code is not None:
        if isinstance(exit_payload, dict):
            exit_payload = {**exit_payload, "wait_plan": wait_plan, "prepare_runtime": prepare_result, "prepare_results": prepare_results}
        _print_json(exit_payload)
        return int(exit_code)
    if not args.confirm_free_click:
        _print_json({"ok": True, "ready": True, "clicked": False, "reason": "confirm_free_click_required", "wait_plan": wait_plan, "prepare_runtime": prepare_result, "prepare_results": prepare_results, **_public_status(status)})
        return 0

    image174 = ctx["images"].get(174)
    visit_shape = runner._find_shape(image174, "寻访") if isinstance(image174, dict) else None
    if not isinstance(image174, dict) or visit_shape is None:
        raise RuntimeError("缺少 #174「寻访」标注，无法捕获 #175")
    runner._click_shape(ctx, image174, visit_shape, status["frame"])

    deadline = time.monotonic() + max(1.0, float(args.wait_timeout_seconds))
    last_result: dict[str, Any] | None = None
    while True:
        time.sleep(max(0.2, float(args.poll_seconds)))
        frame = runner._screencap(ctx)
        frame_path = output_dir / f"xianfu_continue_{time.strftime('%Y%m%d_%H%M%S')}.png"
        _write_frame_data_url(frame, frame_path)
        result = build_candidates(
            page="继续寻访",
            old_root=DEFAULT_OLD_XIANFU_ROOT,
            frame_path=frame_path,
            old_crop=(0.0, 42.0, 476.0, 1037.0),
            output_dir=output_dir,
        )
        last_result = result
        if bool(result.get("ocr_verified")):
            installed = None
            if args.install:
                installed = install_continue_visit_image(
                    result=result,
                    asset_tree_path=asset_tree,
                    screenshot_dir=args.screenshot_dir,
                    target_number=175,
                )
            runtime_result = None
            if args.run_runtime_after_install:
                if installed is None:
                    raise RuntimeError("--run-runtime-after-install 需要同时启用 --install")
                runtime_result = _run_runtime_after_install(
                    entry_id=str(args.entry_id),
                    timeout_seconds=float(args.runtime_timeout_seconds or 180.0),
                )
            _print_json({
                "ok": True,
                "clicked": True,
                "ocr_verified": True,
                "wait_plan": wait_plan,
                "prepare_runtime": prepare_result,
                "prepare_results": prepare_results,
                "frame_path": str(frame_path),
                "annotated_path": result.get("annotated_path"),
                "installed": installed,
                "runtime_after_install": runtime_result,
            })
            if runtime_result is not None and not bool(runtime_result.get("ok")):
                return 1
            return 0
        if time.monotonic() >= deadline:
            _print_json({
                "ok": False,
                "clicked": True,
                "reason": "continue_popup_not_verified",
                "last_frame_path": str(frame_path),
                "annotated_path": last_result.get("annotated_path") if last_result else "",
                "unverified_labels": last_result.get("unverified_labels") if last_result else [],
            })
            return 1


if __name__ == "__main__":
    raise SystemExit(main())

