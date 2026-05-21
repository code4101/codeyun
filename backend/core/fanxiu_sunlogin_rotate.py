from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from backend.core.settings import ROOT_DIR, get_settings
from backend.core.sunlogin_rotate_preview import (
    WindowCapture,
    click_window_raw_point,
    ensure_windows_runtime,
    find_window,
    iter_mjpeg_frames,
    map_processed_point_to_raw_point,
    normalize_rotate,
    parse_crop,
    process_frame,
    set_dpi_awareness,
)


PROCESS_ENV_MARKER = "CODEYUN_FANXIU_SUNLOGIN_ROTATE"
PROCESS_ENV_VALUE = "1"
PREVIEW_MODULE = "backend.core.sunlogin_rotate_preview"
DEFAULT_TARGET_TITLE = "1249152866"
DEFAULT_PREVIEW_TITLE = "codeyun-sunlogin-rotate"
DEFAULT_FPS = "15"
DEFAULT_CROP = "0,49,4,4"
DEFAULT_TRIM_BORDER = "0,0,0,0"
DEFAULT_ROTATE = "90"
DEFAULT_FIXED_WIDTH = "0"
DEFAULT_FIXED_HEIGHT = "0"


@dataclass(frozen=True)
class SunloginRotateProcessInfo:
    pid: int
    parent_pid: int | None
    name: str
    command_line: str
    started_at: str | None
    runtime_seconds: int | None


@dataclass(frozen=True)
class SunloginRotateStatus:
    running: bool
    pids: list[int]
    primary_pid: int | None
    started_at: str | None
    runtime_seconds: int | None
    command_line: str
    target_title: str
    preview_title: str
    stdout_log: str
    stderr_log: str
    last_error: str


def get_target_title() -> str:
    return (os.getenv("CODEYUN_FANXIU_SUNLOGIN_TITLE") or DEFAULT_TARGET_TITLE).strip() or DEFAULT_TARGET_TITLE


def get_preview_title() -> str:
    return (os.getenv("CODEYUN_FANXIU_SUNLOGIN_PREVIEW_TITLE") or DEFAULT_PREVIEW_TITLE).strip() or DEFAULT_PREVIEW_TITLE


def _runtime_dir() -> Path:
    path = get_settings().data_dir / "fanxiu" / "sunlogin-rotate"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stdout_log_path() -> Path:
    return _runtime_dir() / "preview.stdout.log"


def _stderr_log_path() -> Path:
    return _runtime_dir() / "preview.stderr.log"


def _normalize_command_line(cmdline: Any) -> str:
    if isinstance(cmdline, (list, tuple)):
        return " ".join(str(part) for part in cmdline if part is not None)
    return str(cmdline or "")


def _safe_command_line(proc: psutil.Process) -> str:
    try:
        return _normalize_command_line(proc.cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return ""


def _safe_environ(proc: psutil.Process) -> dict[str, str] | None:
    try:
        return proc.environ()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def _safe_name(proc: psutil.Process) -> str:
    try:
        return proc.name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return ""


def _safe_ppid(proc: psutil.Process) -> int | None:
    try:
        return proc.ppid()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def _safe_create_time(proc: psutil.Process) -> float | None:
    try:
        return proc.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def _is_preview_process(proc: psutil.Process) -> bool:
    if proc.pid == os.getpid():
        return False

    environ = _safe_environ(proc)
    if environ and environ.get(PROCESS_ENV_MARKER) == PROCESS_ENV_VALUE:
        return True

    command_line = _safe_command_line(proc)
    normalized = command_line.replace("\\", "/")
    return PREVIEW_MODULE in normalized and get_preview_title() in command_line


def _process_info(proc: psutil.Process) -> SunloginRotateProcessInfo | None:
    try:
        created_ts = _safe_create_time(proc)
        return SunloginRotateProcessInfo(
            pid=proc.pid,
            parent_pid=_safe_ppid(proc),
            name=_safe_name(proc),
            command_line=_safe_command_line(proc),
            started_at=datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d %H:%M:%S") if created_ts else None,
            runtime_seconds=max(0, int(time.time() - created_ts)) if created_ts else None,
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def _list_preview_processes() -> list[SunloginRotateProcessInfo]:
    items: list[SunloginRotateProcessInfo] = []
    for proc in psutil.process_iter(["pid"]):
        try:
            if not _is_preview_process(proc):
                continue
            info = _process_info(proc)
            if info is not None:
                items.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    items.sort(key=lambda item: (item.started_at or "", item.pid))
    return items


def _tail_text(path: Path, max_chars: int = 2000) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if not data:
        return ""
    return data[-max_chars:].decode("utf-8", errors="replace").strip()


def get_sunlogin_rotate_status() -> dict[str, Any]:
    items = _list_preview_processes()
    primary = items[0] if items else None
    status = SunloginRotateStatus(
        running=bool(items),
        pids=[item.pid for item in items],
        primary_pid=primary.pid if primary else None,
        started_at=primary.started_at if primary else None,
        runtime_seconds=primary.runtime_seconds if primary else None,
        command_line=primary.command_line if primary else "",
        target_title=get_target_title(),
        preview_title=get_preview_title(),
        stdout_log=os.fspath(_stdout_log_path()),
        stderr_log=os.fspath(_stderr_log_path()),
        last_error=_tail_text(_stderr_log_path()),
    )
    return asdict(status)


def _build_preview_command() -> list[str]:
    return [
        sys.executable,
        "-m",
        PREVIEW_MODULE,
        "--title",
        get_target_title(),
        "--fps",
        os.getenv("CODEYUN_FANXIU_SUNLOGIN_FPS", DEFAULT_FPS),
        "--mode",
        os.getenv("CODEYUN_FANXIU_SUNLOGIN_MODE", "screen"),
        "--crop",
        os.getenv("CODEYUN_FANXIU_SUNLOGIN_CROP", DEFAULT_CROP),
        "--trim-border",
        os.getenv("CODEYUN_FANXIU_SUNLOGIN_TRIM_BORDER", DEFAULT_TRIM_BORDER),
        "--rotate",
        os.getenv("CODEYUN_FANXIU_SUNLOGIN_ROTATE", DEFAULT_ROTATE),
        "--fixed-width",
        os.getenv("CODEYUN_FANXIU_SUNLOGIN_FIXED_WIDTH", DEFAULT_FIXED_WIDTH),
        "--fixed-height",
        os.getenv("CODEYUN_FANXIU_SUNLOGIN_FIXED_HEIGHT", DEFAULT_FIXED_HEIGHT),
        "--preview-title",
        get_preview_title(),
    ]


def _build_child_env() -> dict[str, str]:
    env = os.environ.copy()
    env[PROCESS_ENV_MARKER] = PROCESS_ENV_VALUE
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = os.fspath(ROOT_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def start_sunlogin_rotate_preview() -> dict[str, Any]:
    if sys.platform != "win32":
        raise RuntimeError("向日葵投屏旋转预览仅支持 Windows 桌面环境")

    current_status = get_sunlogin_rotate_status()
    if current_status["running"]:
        return current_status

    stdout_path = _stdout_log_path()
    stderr_path = _stderr_log_path()
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    command = _build_preview_command()
    with stdout_path.open("a", encoding="utf-8", errors="replace") as stdout_file, stderr_path.open(
        "a",
        encoding="utf-8",
        errors="replace",
    ) as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=os.fspath(ROOT_DIR),
            env=_build_child_env(),
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            text=True,
            creationflags=creationflags,
        )

    time.sleep(0.8)
    return_code = process.poll()
    if return_code is not None:
        detail = _tail_text(stderr_path) or _tail_text(stdout_path) or "无日志"
        raise RuntimeError(f"投屏旋转预览启动后退出（退出码 {return_code}）：{detail}")

    return get_sunlogin_rotate_status()


def stop_sunlogin_rotate_preview(timeout: float = 3.0) -> dict[str, Any]:
    targets: list[psutil.Process] = []
    for proc in psutil.process_iter(["pid"]):
        try:
            if _is_preview_process(proc):
                targets.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue

    errors: list[dict[str, Any]] = []
    for proc in targets:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError) as exc:
            errors.append({"pid": proc.pid, "error": str(exc)})

    _, alive = psutil.wait_procs(targets, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError) as exc:
            errors.append({"pid": proc.pid, "error": str(exc)})

    if alive:
        psutil.wait_procs(alive, timeout=timeout)

    status = get_sunlogin_rotate_status()
    if errors:
        status["errors"] = errors
    return status


def stream_sunlogin_rotate_mjpeg(
    *,
    title: str | None = None,
    fps: float | None = None,
    quality: int = 80,
    mode: str | None = None,
    area: str | None = None,
    crop: str | None = None,
    trim_border: str | None = None,
    rotate: str | None = None,
    fixed_width: int | None = None,
    fixed_height: int | None = None,
    auto_dismiss_popup: bool = False,
    popup_check_interval: float = 3.0,
):
    return iter_mjpeg_frames(
        title=(title or get_target_title()).strip() or get_target_title(),
        fps=float(fps or os.getenv("CODEYUN_FANXIU_SUNLOGIN_FPS", DEFAULT_FPS)),
        crop=parse_crop(crop or os.getenv("CODEYUN_FANXIU_SUNLOGIN_CROP", DEFAULT_CROP)),
        trim_border=parse_crop(trim_border or os.getenv("CODEYUN_FANXIU_SUNLOGIN_TRIM_BORDER", DEFAULT_TRIM_BORDER)),
        rotate=normalize_rotate(rotate or os.getenv("CODEYUN_FANXIU_SUNLOGIN_ROTATE", DEFAULT_ROTATE)),
        area=area or os.getenv("CODEYUN_FANXIU_SUNLOGIN_AREA", "outer"),
        mode=mode or os.getenv("CODEYUN_FANXIU_SUNLOGIN_MODE", "screen"),
        max_width=0,
        max_height=0,
        scale=1.0,
        fixed_width=int(fixed_width if fixed_width is not None else os.getenv("CODEYUN_FANXIU_SUNLOGIN_FIXED_WIDTH", DEFAULT_FIXED_WIDTH)),
        fixed_height=int(fixed_height if fixed_height is not None else os.getenv("CODEYUN_FANXIU_SUNLOGIN_FIXED_HEIGHT", DEFAULT_FIXED_HEIGHT)),
        refind_interval=1.0,
        quality=quality,
        auto_dismiss_popup=auto_dismiss_popup,
        popup_check_interval=popup_check_interval,
    )


def capture_sunlogin_rotate_frame(
    *,
    title: str | None = None,
    mode: str | None = None,
    area: str | None = None,
    crop: str | None = None,
    trim_border: str | None = None,
    rotate: str | None = None,
    fixed_width: int | None = None,
    fixed_height: int | None = None,
):
    ensure_windows_runtime()
    set_dpi_awareness()

    normalized_title = (title or get_target_title()).strip() or get_target_title()
    target = find_window(normalized_title)
    capturer = WindowCapture(
        target.hwnd,
        area or os.getenv("CODEYUN_FANXIU_SUNLOGIN_AREA", "outer"),
        mode or os.getenv("CODEYUN_FANXIU_SUNLOGIN_MODE", "screen"),
        normalized_title,
        refind_interval=1.0,
    )
    frame = capturer.capture()
    if frame is None:
        raise RuntimeError("截图失败")

    return process_frame(
        frame,
        parse_crop(crop or os.getenv("CODEYUN_FANXIU_SUNLOGIN_CROP", DEFAULT_CROP)),
        parse_crop(trim_border or os.getenv("CODEYUN_FANXIU_SUNLOGIN_TRIM_BORDER", DEFAULT_TRIM_BORDER)),
        normalize_rotate(rotate or os.getenv("CODEYUN_FANXIU_SUNLOGIN_ROTATE", DEFAULT_ROTATE)),
        max_width=0,
        max_height=0,
        scale=1.0,
        fixed_width=int(fixed_width if fixed_width is not None else os.getenv("CODEYUN_FANXIU_SUNLOGIN_FIXED_WIDTH", DEFAULT_FIXED_WIDTH)),
        fixed_height=int(fixed_height if fixed_height is not None else os.getenv("CODEYUN_FANXIU_SUNLOGIN_FIXED_HEIGHT", DEFAULT_FIXED_HEIGHT)),
    )


def click_sunlogin_rotate_processed_point(
    *,
    x: float,
    y: float,
    title: str | None = None,
    mode: str | None = None,
    area: str | None = None,
    crop: str | None = None,
    trim_border: str | None = None,
    rotate: str | None = None,
    fixed_width: int | None = None,
    fixed_height: int | None = None,
) -> dict[str, Any]:
    ensure_windows_runtime()
    set_dpi_awareness()

    resolved_fixed_width = int(
        fixed_width if fixed_width is not None else os.getenv("CODEYUN_FANXIU_SUNLOGIN_FIXED_WIDTH", DEFAULT_FIXED_WIDTH)
    )
    resolved_fixed_height = int(
        fixed_height if fixed_height is not None else os.getenv("CODEYUN_FANXIU_SUNLOGIN_FIXED_HEIGHT", DEFAULT_FIXED_HEIGHT)
    )
    if resolved_fixed_width > 0 or resolved_fixed_height > 0:
        raise RuntimeError("固定画布模式暂不支持反向点击坐标映射")

    normalized_title = (title or get_target_title()).strip() or get_target_title()
    resolved_area = area or os.getenv("CODEYUN_FANXIU_SUNLOGIN_AREA", "outer")
    resolved_mode = mode or os.getenv("CODEYUN_FANXIU_SUNLOGIN_MODE", "screen")
    resolved_crop = parse_crop(crop or os.getenv("CODEYUN_FANXIU_SUNLOGIN_CROP", DEFAULT_CROP))
    resolved_trim_border = parse_crop(
        trim_border or os.getenv("CODEYUN_FANXIU_SUNLOGIN_TRIM_BORDER", DEFAULT_TRIM_BORDER)
    )
    resolved_rotate = normalize_rotate(rotate or os.getenv("CODEYUN_FANXIU_SUNLOGIN_ROTATE", DEFAULT_ROTATE))

    target = find_window(normalized_title)
    capturer = WindowCapture(target.hwnd, resolved_area, resolved_mode, normalized_title, refind_interval=1.0)
    raw_frame = capturer.capture()
    if raw_frame is None:
        raise RuntimeError("点击前截图失败，无法确认窗口坐标")

    frame = process_frame(
        raw_frame,
        resolved_crop,
        resolved_trim_border,
        resolved_rotate,
        max_width=0,
        max_height=0,
        scale=1.0,
        fixed_width=0,
        fixed_height=0,
    )
    frame_height, frame_width = frame.shape[:2]
    frame_x = int(round(x))
    frame_y = int(round(y))
    if not (0 <= frame_x < frame_width and 0 <= frame_y < frame_height):
        raise RuntimeError(f"点击坐标超出画面范围：({frame_x}, {frame_y}) / {frame_width}x{frame_height}")

    raw_point = map_processed_point_to_raw_point(
        (frame_x, frame_y),
        raw_shape=raw_frame.shape,
        crop=resolved_crop,
        trim_border=resolved_trim_border,
        rotate=resolved_rotate,
    )
    if raw_point is None:
        raise RuntimeError("点击坐标无法映射到原始窗口坐标")

    click_window_raw_point(capturer.hwnd, resolved_area, raw_point)
    return {
        "ok": True,
        "title": normalized_title,
        "hwnd": capturer.hwnd,
        "frame_x": frame_x,
        "frame_y": frame_y,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "raw_x": raw_point[0],
        "raw_y": raw_point[1],
        "area": resolved_area,
        "mode": resolved_mode,
        "rotate": resolved_rotate,
    }
