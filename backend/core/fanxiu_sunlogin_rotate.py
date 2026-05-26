from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
from pyxllib.cv.rgbfmt import (
    compare_bgr_pixel_tolerance,
    normalize_for_saved_jpeg_match,
    to_bgr_frame,
)

from backend.core.settings import ROOT_DIR, get_settings
from backend.core.sunlogin_rotate_preview import (
    WindowCapture,
    click_window_raw_point,
    drag_window_raw_points,
    encode_jpeg,
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
SCREENSHOT_FRAME_DIRNAME = "截图"
MATCH_FRAME_DIRNAME = "匹配"
_SCREENSHOT_FRAME_LOCK = threading.Lock()
_MATCH_FRAME_LOCK = threading.Lock()
_SCREENSHOT_FRAME_NAME_PATTERN = re.compile(r"^(\d+)\.jpe?g$", re.IGNORECASE)
_SCREENSHOT_IMAGE_SUFFIXES = {".jpg", ".jpeg"}


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


def _home_root() -> Path:
    return ROOT_DIR.parent.parent if ROOT_DIR.parent.name.lower() == "slns" else ROOT_DIR.parent


def get_fanxiu_mainwin_root() -> Path:
    configured = os.getenv("FX_MAINWIN_ROOT")
    if configured and configured.strip():
        return Path(configured.strip()).expanduser().resolve(strict=False)
    return (_home_root() / "data" / "m2508凡修" / "mainwin").resolve(strict=False)


def get_fanxiu_screenshot_frame_dir() -> Path:
    configured = os.getenv("FX_SCREENSHOT_FRAME_DIR")
    if configured and configured.strip():
        return Path(configured.strip()).expanduser().resolve(strict=False)
    return (get_fanxiu_mainwin_root() / SCREENSHOT_FRAME_DIRNAME).resolve(strict=False)


def get_fanxiu_match_frame_dir() -> Path:
    configured = os.getenv("FX_MATCH_FRAME_DIR")
    if configured and configured.strip():
        return Path(configured.strip()).expanduser().resolve(strict=False)
    return (get_fanxiu_mainwin_root() / MATCH_FRAME_DIRNAME).resolve(strict=False)


def _next_numbered_frame_path(output_dir: Path) -> tuple[int, Path]:
    max_index = 0
    if output_dir.exists():
        for path in output_dir.iterdir():
            if not path.is_file():
                continue
            match = _SCREENSHOT_FRAME_NAME_PATTERN.match(path.name)
            if match:
                max_index = max(max_index, int(match.group(1)))
    index = max_index + 1
    return index, output_dir / f"{index:04d}.jpg"


def _next_screenshot_frame_path(output_dir: Path) -> tuple[int, Path]:
    return _next_numbered_frame_path(output_dir)


def _normalize_screenshot_filename(filename: str) -> str:
    name = Path(str(filename or "")).name
    if not name or name != str(filename) or "\x00" in name:
        raise ValueError("截图文件名不合法")
    if Path(name).suffix.lower() not in _SCREENSHOT_IMAGE_SUFFIXES:
        raise ValueError("截图只支持 jpg/jpeg")
    return name


def _screenshot_sort_key(path: Path) -> tuple[int, int, str]:
    match = _SCREENSHOT_FRAME_NAME_PATTERN.match(path.name)
    if match:
        return (0, int(match.group(1)), path.name.lower())
    return (1, 0, path.name.lower())


def _read_image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return 0, 0


def _read_image_bgr(path: Path):
    import cv2
    import numpy as np

    data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _screenshot_path(filename: str) -> Path:
    output_dir = get_fanxiu_screenshot_frame_dir()
    image_path = (output_dir / _normalize_screenshot_filename(filename)).resolve(strict=False)
    if image_path.parent != output_dir.resolve(strict=False):
        raise ValueError("截图路径越界")
    return image_path


def _match_frame_path(filename: str) -> Path:
    output_dir = get_fanxiu_match_frame_dir()
    image_path = (output_dir / _normalize_screenshot_filename(filename)).resolve(strict=False)
    if image_path.parent != output_dir.resolve(strict=False):
        raise ValueError("匹配帧路径越界")
    return image_path


def _screenshot_pre_label_path(image_path: Path) -> Path:
    return image_path.with_name(f"{image_path.stem}_pre.json")


def _screenshot_final_label_path(image_path: Path) -> Path:
    return image_path.with_suffix(".json")


def list_fanxiu_screenshots() -> dict[str, Any]:
    output_dir = get_fanxiu_screenshot_frame_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for path in sorted(
        (item for item in output_dir.iterdir() if item.is_file() and item.suffix.lower() in _SCREENSHOT_IMAGE_SUFFIXES),
        key=_screenshot_sort_key,
    ):
        stat = path.stat()
        width, height = _read_image_size(path)
        pre_label_path = _screenshot_pre_label_path(path)
        label_path = _screenshot_final_label_path(path)
        items.append(
            {
                "filename": path.name,
                "stem": path.stem,
                "pre_label_filename": pre_label_path.name,
                "pre_label_exists": pre_label_path.is_file(),
                "label_filename": label_path.name,
                "label_exists": label_path.is_file(),
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "width": width,
                "height": height,
            }
        )
    return {
        "directory": os.fspath(output_dir),
        "items": items,
    }


def get_fanxiu_screenshot_path(filename: str) -> Path:
    image_path = _screenshot_path(filename)
    if not image_path.is_file():
        raise FileNotFoundError(f"截图不存在：{image_path.name}")
    return image_path


def get_fanxiu_match_frame_path(filename: str) -> Path:
    image_path = _match_frame_path(filename)
    if not image_path.is_file():
        raise FileNotFoundError(f"匹配帧不存在：{image_path.name}")
    return image_path


def delete_fanxiu_screenshot(filename: str) -> dict[str, Any]:
    image_path = get_fanxiu_screenshot_path(filename)
    deleted: list[str] = []
    for path in (image_path, _screenshot_pre_label_path(image_path), _screenshot_final_label_path(image_path)):
        if path.is_file():
            path.unlink()
            deleted.append(path.name)
    return {
        "filename": image_path.name,
        "deleted": deleted,
    }


def _default_screenshot_pre_label_payload(image_path: Path) -> dict[str, Any]:
    width, height = _read_image_size(image_path)
    return {
        "version": 1,
        "image": image_path.name,
        "size": {
            "width": width,
            "height": height,
        },
        "boxes": [],
    }


def _normalize_screenshot_pre_label_payload(image_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    width, height = _read_image_size(image_path)
    normalized_boxes: list[dict[str, Any]] = []
    raw_boxes = payload.get("boxes") if isinstance(payload, dict) else None
    if isinstance(raw_boxes, list):
        for index, raw_box in enumerate(raw_boxes, start=1):
            if not isinstance(raw_box, dict):
                continue
            try:
                x = round(float(raw_box.get("x", 0)))
                y = round(float(raw_box.get("y", 0)))
                w = round(float(raw_box.get("w", 0)))
                h = round(float(raw_box.get("h", 0)))
            except (TypeError, ValueError):
                continue
            if w <= 0 or h <= 0:
                continue
            name = str(raw_box.get("name") or "").strip()[:100]
            max_x = width if width > 0 else max(1, x + w)
            max_y = height if height > 0 else max(1, y + h)
            x = min(max(0, x), max(0, max_x - 1))
            y = min(max(0, y), max(0, max_y - 1))
            w = min(max(1, w), max(1, max_x - x))
            h = min(max(1, h), max(1, max_y - y))
            normalized_boxes.append(
                {
                    "name": name,
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                }
            )

    return {
        "version": 1,
        "image": image_path.name,
        "size": {
            "width": width,
            "height": height,
        },
        "boxes": normalized_boxes,
    }


def read_fanxiu_screenshot_pre_label(filename: str) -> dict[str, Any]:
    image_path = get_fanxiu_screenshot_path(filename)
    pre_label_path = _screenshot_pre_label_path(image_path)
    if not pre_label_path.is_file():
        return {
            "exists": False,
            "filename": pre_label_path.name,
            "payload": _default_screenshot_pre_label_payload(image_path),
        }
    try:
        payload = json.loads(pre_label_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "exists": True,
        "filename": pre_label_path.name,
        "payload": _normalize_screenshot_pre_label_payload(image_path, payload),
    }


def write_fanxiu_screenshot_pre_label(filename: str, payload: dict[str, Any]) -> dict[str, Any]:
    image_path = get_fanxiu_screenshot_path(filename)
    pre_label_path = _screenshot_pre_label_path(image_path)
    normalized = _normalize_screenshot_pre_label_payload(image_path, payload)
    pre_label_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "exists": True,
        "filename": pre_label_path.name,
        "payload": normalized,
    }


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
    title_match: str = "contains",
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
        title_match=title_match,
        auto_dismiss_popup=auto_dismiss_popup,
        popup_check_interval=popup_check_interval,
    )


def capture_sunlogin_rotate_frame(
    *,
    title: str | None = None,
    title_match: str = "contains",
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
    target = find_window(normalized_title, title_match)
    capturer = WindowCapture(
        target.hwnd,
        area or os.getenv("CODEYUN_FANXIU_SUNLOGIN_AREA", "outer"),
        mode or os.getenv("CODEYUN_FANXIU_SUNLOGIN_MODE", "screen"),
        normalized_title,
        title_match,
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


def save_fanxiu_screenshot_frame(
    *,
    title: str | None = None,
    title_match: str = "contains",
    mode: str | None = None,
    area: str | None = None,
    crop: str | None = None,
    trim_border: str | None = None,
    rotate: str | None = None,
    fixed_width: int | None = None,
    fixed_height: int | None = None,
    quality: int = 82,
) -> dict[str, Any]:
    frame = capture_sunlogin_rotate_frame(
        title=title,
        title_match=title_match,
        mode=mode,
        area=area,
        crop=crop,
        trim_border=trim_border,
        rotate=rotate,
        fixed_width=fixed_width,
        fixed_height=fixed_height,
    )
    height, width = frame.shape[:2]
    data = encode_jpeg(frame, quality)

    output_dir = get_fanxiu_screenshot_frame_dir()
    with _SCREENSHOT_FRAME_LOCK:
        output_dir.mkdir(parents=True, exist_ok=True)
        index, output = _next_screenshot_frame_path(output_dir)
        while output.exists():
            index += 1
            output = output_dir / f"{index:04d}.jpg"
        output.write_bytes(data)

    return {
        "ok": True,
        "index": index,
        "filename": output.name,
        "path": os.fspath(output),
        "directory": os.fspath(output_dir),
        "width": width,
        "height": height,
    }


def _normalize_match_box(box: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    try:
        x = round(float(box.get("x", 0)))
        y = round(float(box.get("y", 0)))
        w = round(float(box.get("w", 0)))
        h = round(float(box.get("h", 0)))
    except (TypeError, ValueError) as exc:
        raise ValueError("匹配框坐标不合法") from exc
    if w <= 0 or h <= 0:
        raise ValueError("匹配框宽高必须大于 0")
    max_x = width if width > 0 else max(1, x + w)
    max_y = height if height > 0 else max(1, y + h)
    x = min(max(0, x), max(0, max_x - 1))
    y = min(max(0, y), max(0, max_y - 1))
    w = min(max(1, w), max(1, max_x - x))
    h = min(max(1, h), max(1, max_y - y))
    return {
        "name": str(box.get("name") or "").strip()[:100],
        "x": x,
        "y": y,
        "w": w,
        "h": h,
    }


def _ensure_bgr_frame(frame: Any):
    return to_bgr_frame(frame, source_format="auto")


def _crop_frame_box(frame: Any, box: dict[str, Any]):
    return frame[box["y"]:box["y"] + box["h"], box["x"]:box["x"] + box["w"]]


def _scale_box(box: dict[str, Any], source_width: int, source_height: int, target_width: int, target_height: int) -> dict[str, Any]:
    scale_x = target_width / source_width if source_width > 0 else 1.0
    scale_y = target_height / source_height if source_height > 0 else 1.0
    return _normalize_match_box(
        {
            "name": box.get("name", ""),
            "x": round(box["x"] * scale_x),
            "y": round(box["y"] * scale_y),
            "w": round(box["w"] * scale_x),
            "h": round(box["h"] * scale_y),
        },
        target_width,
        target_height,
    )


def _compare_frame_crops(reference_crop: Any, current_crop: Any, pixel_tolerance: int = 5) -> tuple[int, float]:
    return compare_bgr_pixel_tolerance(reference_crop, current_crop, pixel_tolerance)


def _correlate_frame_crops(reference_crop: Any, current_crop: Any) -> tuple[int, float]:
    import cv2

    template = _ensure_bgr_frame(reference_crop)
    crop = _ensure_bgr_frame(current_crop)
    if template.size == 0 or crop.size == 0:
        raise ValueError("匹配图片为空")
    if template.shape[:2] != crop.shape[:2]:
        crop = cv2.resize(crop, (template.shape[1], template.shape[0]), interpolation=cv2.INTER_AREA)

    template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if float(template_gray.std()) > 1e-6 and float(crop_gray.std()) > 1e-6:
        result = cv2.matchTemplate(crop_gray, template_gray, cv2.TM_CCOEFF_NORMED)
        _, max_value, _, _ = cv2.minMaxLoc(result)
        score = max(0.0, min(1.0, float(max_value)))
    else:
        result = cv2.matchTemplate(crop_gray, template_gray, cv2.TM_SQDIFF_NORMED)
        min_value, _, _, _ = cv2.minMaxLoc(result)
        score = max(0.0, min(1.0, 1.0 - float(min_value)))
    return int(round(score * 100)), score


def _jpeg_normalize_frame(frame: Any, quality: int) -> tuple[Any, bytes]:
    return normalize_for_saved_jpeg_match(frame, quality=quality, source_format="auto", return_bytes=True)


def _match_template_frame(
    reference_crop: Any,
    current_frame: Any,
    current_box: dict[str, Any],
    pixel_tolerance: int = 5,
) -> dict[str, Any]:
    import cv2
    import numpy as np

    template = _ensure_bgr_frame(reference_crop)
    frame = _ensure_bgr_frame(current_frame)
    if template.size == 0 or frame.size == 0:
        raise ValueError("模板匹配图片为空")

    frame_height, frame_width = frame.shape[:2]
    template_width = min(max(1, int(current_box["w"])), frame_width)
    template_height = min(max(1, int(current_box["h"])), frame_height)
    if template.shape[:2] != (template_height, template_width):
        template = cv2.resize(template, (template_width, template_height), interpolation=cv2.INTER_AREA)

    frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    if template_gray.shape[0] > frame_gray.shape[0] or template_gray.shape[1] > frame_gray.shape[1]:
        raise ValueError("模板尺寸大于当前画面")

    if float(template_gray.std()) > 1e-6 and float(frame_gray.std()) > 1e-6:
        result = cv2.matchTemplate(frame_gray, template_gray, cv2.TM_CCOEFF_NORMED)
        _, max_value, _, max_loc = cv2.minMaxLoc(result)
        score = max(0.0, min(1.0, float(max_value)))
        x, y = max_loc
    else:
        result = cv2.matchTemplate(frame_gray, template_gray, cv2.TM_SQDIFF_NORMED)
        min_value, _, min_loc, _ = cv2.minMaxLoc(result)
        score = max(0.0, min(1.0, 1.0 - float(min_value)))
        x, y = min_loc

    box = _normalize_match_box(
        {
            "name": current_box.get("name", ""),
            "x": x,
            "y": y,
            "w": template_width,
            "h": template_height,
        },
        frame_width,
        frame_height,
    )
    matched_crop = _crop_frame_box(frame, box)
    crop_similarity, crop_score = _compare_frame_crops(template, matched_crop, pixel_tolerance)
    return {
        "box": box,
        "similarity": int(round(score * 100)),
        "score": score,
        "crop_similarity": crop_similarity,
        "crop_score": crop_score,
    }


def match_fanxiu_screenshot_box_frame(
    *,
    filename: str,
    box: dict[str, Any],
    title: str | None = None,
    title_match: str = "contains",
    mode: str | None = None,
    area: str | None = None,
    crop: str | None = None,
    trim_border: str | None = None,
    rotate: str | None = None,
    fixed_width: int | None = None,
    fixed_height: int | None = None,
    quality: int = 82,
    pixel_tolerance: int = 5,
) -> dict[str, Any]:
    source_path = get_fanxiu_screenshot_path(filename)
    reference_frame = _read_image_bgr(source_path)
    if reference_frame is None:
        raise RuntimeError(f"读取截图失败：{source_path.name}")
    source_height, source_width = reference_frame.shape[:2]
    source_box = _normalize_match_box(box, source_width, source_height)
    reference_crop = _crop_frame_box(reference_frame, source_box)

    current_frame = capture_sunlogin_rotate_frame(
        title=title,
        title_match=title_match,
        mode=mode,
        area=area,
        crop=crop,
        trim_border=trim_border,
        rotate=rotate,
        fixed_width=fixed_width,
        fixed_height=fixed_height,
    )
    current_frame, data = _jpeg_normalize_frame(current_frame, quality)
    current_height, current_width = current_frame.shape[:2]
    current_box = _scale_box(source_box, source_width, source_height, current_width, current_height)
    current_crop = _crop_frame_box(current_frame, current_box)
    fixed_similarity, fixed_score = _correlate_frame_crops(reference_crop, current_crop)
    fixed_pixel_similarity, fixed_pixel_score = _compare_frame_crops(reference_crop, current_crop, pixel_tolerance)
    template_match = _match_template_frame(reference_crop, current_frame, current_box, pixel_tolerance)

    output_dir = get_fanxiu_match_frame_dir()
    with _MATCH_FRAME_LOCK:
        output_dir.mkdir(parents=True, exist_ok=True)
        index, output = _next_numbered_frame_path(output_dir)
        while output.exists():
            index += 1
            output = output_dir / f"{index:04d}.jpg"
        output.write_bytes(data)

    return {
        "ok": True,
        "index": index,
        "source_filename": source_path.name,
        "match_filename": output.name,
        "path": os.fspath(output),
        "directory": os.fspath(output_dir),
        "similarity": fixed_similarity,
        "score": fixed_score,
        "fixed_similarity": fixed_similarity,
        "fixed_score": fixed_score,
        "fixed_pixel_similarity": fixed_pixel_similarity,
        "fixed_pixel_score": fixed_pixel_score,
        "template_similarity": template_match["similarity"],
        "template_score": template_match["score"],
        "template_crop_similarity": template_match["crop_similarity"],
        "template_crop_score": template_match["crop_score"],
        "box": source_box,
        "current_box": current_box,
        "template_box": template_match["box"],
        "source_width": source_width,
        "source_height": source_height,
        "width": current_width,
        "height": current_height,
        "pixel_tolerance": max(0, min(255, int(pixel_tolerance if pixel_tolerance is not None else 5))),
    }


def click_sunlogin_rotate_processed_point(
    *,
    x: float,
    y: float,
    title: str | None = None,
    title_match: str = "contains",
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

    target = find_window(normalized_title, title_match)
    capturer = WindowCapture(target.hwnd, resolved_area, resolved_mode, normalized_title, title_match, refind_interval=1.0)
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


def drag_sunlogin_rotate_processed_points(
    *,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    duration_ms: int = 300,
    title: str | None = None,
    title_match: str = "contains",
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
        raise RuntimeError("固定画布模式暂不支持反向拖拽坐标映射")

    normalized_title = (title or get_target_title()).strip() or get_target_title()
    resolved_area = area or os.getenv("CODEYUN_FANXIU_SUNLOGIN_AREA", "outer")
    resolved_mode = mode or os.getenv("CODEYUN_FANXIU_SUNLOGIN_MODE", "screen")
    resolved_crop = parse_crop(crop or os.getenv("CODEYUN_FANXIU_SUNLOGIN_CROP", DEFAULT_CROP))
    resolved_trim_border = parse_crop(
        trim_border or os.getenv("CODEYUN_FANXIU_SUNLOGIN_TRIM_BORDER", DEFAULT_TRIM_BORDER)
    )
    resolved_rotate = normalize_rotate(rotate or os.getenv("CODEYUN_FANXIU_SUNLOGIN_ROTATE", DEFAULT_ROTATE))

    target = find_window(normalized_title, title_match)
    capturer = WindowCapture(target.hwnd, resolved_area, resolved_mode, normalized_title, title_match, refind_interval=1.0)
    raw_frame = capturer.capture()
    if raw_frame is None:
        raise RuntimeError("拖拽前截图失败，无法确认窗口坐标")

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
    frame_start_x = int(round(start_x))
    frame_start_y = int(round(start_y))
    frame_end_x = int(round(end_x))
    frame_end_y = int(round(end_y))
    for label, point_x, point_y in (
        ("起点", frame_start_x, frame_start_y),
        ("终点", frame_end_x, frame_end_y),
    ):
        if not (0 <= point_x < frame_width and 0 <= point_y < frame_height):
            raise RuntimeError(f"拖拽{label}坐标超出画面范围：({point_x}, {point_y}) / {frame_width}x{frame_height}")

    start_raw_point = map_processed_point_to_raw_point(
        (frame_start_x, frame_start_y),
        raw_shape=raw_frame.shape,
        crop=resolved_crop,
        trim_border=resolved_trim_border,
        rotate=resolved_rotate,
    )
    end_raw_point = map_processed_point_to_raw_point(
        (frame_end_x, frame_end_y),
        raw_shape=raw_frame.shape,
        crop=resolved_crop,
        trim_border=resolved_trim_border,
        rotate=resolved_rotate,
    )
    if start_raw_point is None or end_raw_point is None:
        raise RuntimeError("拖拽坐标无法映射到原始窗口坐标")

    drag_window_raw_points(capturer.hwnd, resolved_area, start_raw_point, end_raw_point, duration_ms=duration_ms)
    return {
        "ok": True,
        "title": normalized_title,
        "hwnd": capturer.hwnd,
        "frame_start_x": frame_start_x,
        "frame_start_y": frame_start_y,
        "frame_end_x": frame_end_x,
        "frame_end_y": frame_end_y,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "raw_start_x": start_raw_point[0],
        "raw_start_y": start_raw_point[1],
        "raw_end_x": end_raw_point[0],
        "raw_end_y": end_raw_point[1],
        "duration_ms": duration_ms,
        "area": resolved_area,
        "mode": resolved_mode,
        "rotate": resolved_rotate,
    }
