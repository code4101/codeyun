#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pyxllib.cv.rgbfmt import encode_jpeg_bytes

if sys.platform == "win32":
    import cv2
    import mss
    import numpy as np
    import win32api
    import win32con
    import win32gui
    import win32ui


PW_CLIENTONLY = 0x00000001
PW_RENDERFULLCONTENT = 0x00000002
DWMWA_EXTENDED_FRAME_BOUNDS = 9


@dataclass(frozen=True)
class WindowCandidate:
    hwnd: int
    title: str
    class_name: str
    rect: tuple[int, int, int, int]

    @property
    def width(self) -> int:
        return max(0, self.rect[2] - self.rect[0])

    @property
    def height(self) -> int:
        return max(0, self.rect[3] - self.rect[1])

    @property
    def area(self) -> int:
        return self.width * self.height


def ensure_windows_runtime() -> None:
    if sys.platform != "win32":
        raise RuntimeError("向日葵投屏旋转预览仅支持 Windows 桌面环境")


def set_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def get_extended_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = ctypes.wintypes.RECT()
    try:
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd,
            DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if hr == 0:
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return win32gui.GetWindowRect(hwnd)


def get_capture_rect(hwnd: int, area: str) -> tuple[int, int, int, int]:
    if area == "client":
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        x1, y1 = win32gui.ClientToScreen(hwnd, (left, top))
        x2, y2 = win32gui.ClientToScreen(hwnd, (right, bottom))
        return x1, y1, x2, y2
    return get_extended_window_rect(hwnd)


def iter_windows(title_substring: str = "", title_match: str = "contains") -> list[WindowCandidate]:
    needle = title_substring.lower()
    match_mode = "exact" if title_match == "exact" else "contains"
    items: list[WindowCandidate] = []

    def callback(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return True

        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return True
        title_lower = title.lower()
        if needle:
            if match_mode == "exact" and title_lower != needle:
                return True
            if match_mode == "contains" and needle not in title_lower:
                return True

        rect = get_extended_window_rect(hwnd)
        width = rect[2] - rect[0]
        height = rect[3] - rect[1]
        if width < 50 or height < 50:
            return True

        class_name = win32gui.GetClassName(hwnd)
        if class_name == "Main HighGUI class":
            return True
        if class_name == "Qt5156QWindowToolSaveBits":
            return True

        items.append(WindowCandidate(hwnd, title, class_name, rect))
        return True

    win32gui.EnumWindows(callback, None)
    return items


def find_window(title_substring: str, title_match: str = "contains") -> WindowCandidate:
    candidates = iter_windows(title_substring, title_match=title_match)
    if not candidates:
        if title_match == "exact":
            raise RuntimeError(f"未找到标题等于 {title_substring!r} 的可见窗口")
        raise RuntimeError(f"未找到标题包含 {title_substring!r} 的可见窗口")
    return max(candidates, key=lambda item: item.area)


def is_window_available(hwnd: int) -> bool:
    try:
        if not win32gui.IsWindow(hwnd):
            return False
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return False
        rect = get_extended_window_rect(hwnd)
        return rect[2] - rect[0] >= 50 and rect[3] - rect[1] >= 50
    except Exception:
        return False


def activate_window(hwnd: int) -> None:
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass


def click_window_title_bar(hwnd: int) -> tuple[int, int]:
    activate_window(hwnd)
    rect = get_extended_window_rect(hwnd)
    left, top, right, bottom = rect
    width = max(1, right - left)
    height = max(1, bottom - top)
    try:
        client_left, client_top = win32gui.ClientToScreen(hwnd, (0, 0))
    except Exception:
        client_left, client_top = left, top

    title_height = max(0, min(height, int(client_top) - top))
    if title_height >= 10:
        y = top + max(4, title_height // 2)
    else:
        y = top + min(12, max(1, height // 4))
    x = left + min(max(80, width // 3), max(1, width - 80))

    win32api.SetCursorPos((int(x), int(y)))
    time.sleep(0.03)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.03)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    return int(x), int(y)


def capture_by_printwindow(hwnd: int, area: str) -> np.ndarray | None:
    rect = get_capture_rect(hwnd, area)
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    if width <= 0 or height <= 0:
        return None

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
    save_dc.SelectObject(bitmap)

    flags = PW_RENDERFULLCONTENT
    if area == "client":
        flags |= PW_CLIENTONLY

    try:
        ok = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), flags)
        if not ok:
            return None

        raw = bitmap.GetBitmapBits(True)
        frame = np.frombuffer(raw, dtype=np.uint8)
        frame = frame.reshape((height, width, 4))
        return frame.copy()
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)


def capture_by_screen(sct: mss.mss, hwnd: int, area: str) -> np.ndarray | None:
    rect = get_capture_rect(hwnd, area)
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    if width <= 0 or height <= 0:
        return None

    monitor = {"left": rect[0], "top": rect[1], "width": width, "height": height}
    shot = sct.grab(monitor)
    return np.asarray(shot).copy()


def looks_blank(frame: np.ndarray) -> bool:
    if frame.size == 0:
        return True
    rgb = frame[:, :, :3]
    luma = rgb.mean(axis=2)
    very_dark_ratio = float((luma < 12).mean())
    very_bright_ratio = float((luma > 245).mean())
    return very_dark_ratio > 0.95 or very_bright_ratio > 0.98 or float(rgb.std()) < 1.5


def crop_frame(frame: np.ndarray, crop: tuple[int, int, int, int]) -> np.ndarray:
    left, top, right, bottom = crop
    height, width = frame.shape[:2]
    x1 = max(0, min(width, left))
    y1 = max(0, min(height, top))
    x2 = max(x1 + 1, min(width, width - right))
    y2 = max(y1 + 1, min(height, height - bottom))
    return frame[y1:y2, x1:x2]


def normalize_rotate(rotate: str) -> str:
    value = str(rotate or "0").strip().lower()
    aliases = {
        "0": "0",
        "none": "0",
        "90": "90",
        "cw": "90",
        "180": "180",
        "270": "270",
        "ccw": "270",
    }
    if value not in aliases:
        raise argparse.ArgumentTypeError("rotate 必须是顺时针角度 0/90/180/270")
    return aliases[value]


def rotate_frame(frame: np.ndarray, rotate: str) -> np.ndarray:
    rotate = normalize_rotate(rotate)
    if rotate == "90":
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if rotate == "180":
        return cv2.rotate(frame, cv2.ROTATE_180)
    if rotate == "270":
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


def ascii_title(text: str, fallback: str = "codeyun-sunlogin-rotate") -> str:
    title = text.encode("ascii", errors="ignore").decode("ascii").strip()
    return title or fallback


def fit_frame(frame: np.ndarray, max_width: int, max_height: int, scale: float) -> np.ndarray:
    height, width = frame.shape[:2]
    ratio = scale
    if max_width > 0:
        ratio = min(ratio, max_width / width)
    if max_height > 0:
        ratio = min(ratio, max_height / height)

    if ratio <= 0:
        ratio = 1.0
    if abs(ratio - 1.0) < 0.001:
        return frame

    new_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
    return cv2.resize(frame, new_size, interpolation=cv2.INTER_AREA)


def fit_frame_to_canvas(frame: np.ndarray, canvas_width: int, canvas_height: int) -> np.ndarray:
    height, width = frame.shape[:2]
    ratio = min(canvas_width / width, canvas_height / height)
    new_width = max(1, int(width * ratio))
    new_height = max(1, int(height * ratio))
    resized = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_AREA)
    if resized.ndim == 3 and resized.shape[2] == 4:
        resized = resized.copy()
        resized[:, :, 3] = 255

    channels = 1 if resized.ndim == 2 else resized.shape[2]
    if channels == 1:
        canvas = np.zeros((canvas_height, canvas_width), dtype=resized.dtype)
    else:
        canvas = np.zeros((canvas_height, canvas_width, channels), dtype=resized.dtype)
        if channels == 4:
            canvas[:, :, 3] = 255

    x = (canvas_width - new_width) // 2
    y = (canvas_height - new_height) // 2
    canvas[y:y + new_height, x:x + new_width] = resized
    return canvas


def is_cv_window_open(title: str) -> bool:
    try:
        return cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) >= 1
    except cv2.error:
        return False


def parse_crop(text: str) -> tuple[int, int, int, int]:
    try:
        parts = [int(item.strip()) for item in text.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("crop 必须是 left,top,right,bottom 四个整数") from exc
    if len(parts) != 4 or any(item < 0 for item in parts):
        raise argparse.ArgumentTypeError("crop 必须是 left,top,right,bottom 四个非负整数")
    return tuple(parts)  # type: ignore[return-value]


class WindowCapture:
    def __init__(
        self,
        hwnd: int,
        area: str,
        mode: str,
        title_substring: str,
        title_match: str,
        refind_interval: float,
    ):
        self.hwnd = hwnd
        self.area = area
        self.mode = mode
        self.title_substring = title_substring
        self.title_match = title_match
        self.refind_interval = refind_interval
        self._force_screen = False
        self._last_refind_at = 0.0
        self._warned_screen_fallback = False
        self.sct = mss.mss()

    def _capture_current(self) -> np.ndarray | None:
        if self.mode == "printwindow":
            return capture_by_printwindow(self.hwnd, self.area)
        if self.mode == "screen":
            return capture_by_screen(self.sct, self.hwnd, self.area)
        if self._force_screen:
            return capture_by_screen(self.sct, self.hwnd, self.area)

        frame = capture_by_printwindow(self.hwnd, self.area)
        if frame is not None and not looks_blank(frame):
            return frame

        if not self._warned_screen_fallback:
            print("PrintWindow 捕捉失败或画面为空，已切换为屏幕区域捕捉。请不要遮挡目标窗口。", flush=True)
            self._warned_screen_fallback = True
        self._force_screen = True
        return capture_by_screen(self.sct, self.hwnd, self.area)

    def refresh_window(self, force: bool = False) -> bool:
        if not force and is_window_available(self.hwnd):
            return True
        if self.refind_interval <= 0:
            return False

        now = time.perf_counter()
        if not force and now - self._last_refind_at < self.refind_interval:
            return False
        self._last_refind_at = now

        try:
            target = find_window(self.title_substring, self.title_match)
        except RuntimeError:
            return False

        if target.hwnd != self.hwnd:
            print(f"已重新定位窗口: hwnd={target.hwnd} title={target.title!r} rect={target.rect}", flush=True)
            self.hwnd = target.hwnd
            self._force_screen = False
            self._warned_screen_fallback = False
        return True

    def capture(self) -> np.ndarray | None:
        if not self.refresh_window():
            return None
        try:
            return self._capture_current()
        except Exception:
            pass

        if not self.refresh_window(force=True):
            return None
        try:
            return self._capture_current()
        except Exception:
            return None


def process_frame(
    frame: np.ndarray,
    crop: tuple[int, int, int, int],
    trim_border: tuple[int, int, int, int],
    rotate: str,
    max_width: int,
    max_height: int,
    scale: float,
    fixed_width: int,
    fixed_height: int,
) -> np.ndarray:
    frame = crop_frame(frame, crop)
    frame = rotate_frame(frame, rotate)
    frame = crop_frame(frame, trim_border)
    if fixed_width > 0 and fixed_height > 0:
        return fit_frame_to_canvas(frame, fixed_width, fixed_height)
    return fit_frame(frame, max_width, max_height, scale)


def encode_jpeg(frame: np.ndarray, quality: int) -> bytes:
    return encode_jpeg_bytes(frame, quality=quality, source_format="auto")


def detect_sunlogin_promotion_cancel_point(frame: np.ndarray) -> tuple[int, int] | None:
    height, width = frame.shape[:2]
    if width <= 0 or height <= 0:
        return None

    if width <= height:
        popup_region = (0.08, 0.58, 0.33, 0.99)
        cancel_region = (0.13, 0.78, 0.19, 0.87)
        member_region = (0.13, 0.86, 0.19, 0.96)
    else:
        popup_region = (0.61, 0.52, 0.99, 0.80)
        cancel_region = (0.80, 0.64, 0.87, 0.72)
        member_region = (0.88, 0.64, 0.98, 0.72)

    def crop_relative(region: tuple[float, float, float, float]) -> np.ndarray:
        x1, y1, x2, y2 = region
        left = max(0, min(width, int(width * x1)))
        top = max(0, min(height, int(height * y1)))
        right = max(left + 1, min(width, int(width * x2)))
        bottom = max(top + 1, min(height, int(height * y2)))
        return frame[top:bottom, left:right, :3]

    popup_patch = crop_relative(popup_region)
    cancel_patch = crop_relative(cancel_region)
    member_patch = crop_relative(member_region)
    popup_luma = popup_patch.mean(axis=2)
    cancel_luma = cancel_patch.mean(axis=2)
    member_luma = member_patch.mean(axis=2)

    popup_dark_ratio = float((popup_luma < 55).mean())
    cancel_dark_ratio = float((cancel_luma < 65).mean())
    member_bright_ratio = float((member_luma > 150).mean())
    if popup_dark_ratio < 0.55 or cancel_dark_ratio < 0.55 or member_bright_ratio < 0.20:
        return None

    x1, y1, x2, y2 = cancel_region
    return int(width * (x1 + x2) / 2), int(height * (y1 + y2) / 2)


def map_processed_point_to_raw_point(
    point: tuple[int, int],
    *,
    raw_shape: tuple[int, ...],
    crop: tuple[int, int, int, int],
    trim_border: tuple[int, int, int, int],
    rotate: str,
    fixed_width: int = 0,
    fixed_height: int = 0,
) -> tuple[int, int] | None:
    raw_height, raw_width = raw_shape[:2]
    crop_left, crop_top, crop_right, crop_bottom = crop
    trim_left, trim_top, trim_right, trim_bottom = trim_border
    cropped_width = raw_width - crop_left - crop_right
    cropped_height = raw_height - crop_top - crop_bottom
    if cropped_width <= 0 or cropped_height <= 0:
        return None

    rotate = normalize_rotate(rotate)
    if rotate in {"90", "270"}:
        rotated_width, rotated_height = cropped_height, cropped_width
    else:
        rotated_width, rotated_height = cropped_width, cropped_height

    processed_width = rotated_width - trim_left - trim_right
    processed_height = rotated_height - trim_top - trim_bottom
    if processed_width <= 0 or processed_height <= 0:
        return None

    point_x = float(point[0])
    point_y = float(point[1])
    if fixed_width > 0 and fixed_height > 0:
        ratio = min(fixed_width / processed_width, fixed_height / processed_height)
        if ratio <= 0:
            return None
        fitted_width = max(1, int(processed_width * ratio))
        fitted_height = max(1, int(processed_height * ratio))
        offset_x = (fixed_width - fitted_width) // 2
        offset_y = (fixed_height - fitted_height) // 2
        if not (offset_x <= point_x < offset_x + fitted_width and offset_y <= point_y < offset_y + fitted_height):
            return None
        point_x = (point_x - offset_x) / ratio
        point_y = (point_y - offset_y) / ratio

    x_rotated = int(round(point_x + trim_left))
    y_rotated = int(round(point_y + trim_top))
    if rotate == "0":
        x_cropped, y_cropped = x_rotated, y_rotated
    elif rotate == "90":
        x_cropped = y_rotated
        y_cropped = cropped_height - 1 - x_rotated
    elif rotate == "180":
        x_cropped = cropped_width - 1 - x_rotated
        y_cropped = cropped_height - 1 - y_rotated
    else:
        x_cropped = cropped_width - 1 - y_rotated
        y_cropped = x_rotated

    raw_x = int(x_cropped + crop_left)
    raw_y = int(y_cropped + crop_top)
    if not (0 <= raw_x < raw_width and 0 <= raw_y < raw_height):
        return None
    return raw_x, raw_y


def click_window_raw_point(hwnd: int, area: str, raw_point: tuple[int, int]) -> None:
    rect = get_capture_rect(hwnd, area)
    screen_x = int(rect[0] + raw_point[0])
    screen_y = int(rect[1] + raw_point[1])
    activate_window(hwnd)
    time.sleep(0.03)
    win32api.SetCursorPos((screen_x, screen_y))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.03)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def drag_window_raw_points(
    hwnd: int,
    area: str,
    start_raw_point: tuple[int, int],
    end_raw_point: tuple[int, int],
    duration_ms: int = 300,
) -> None:
    rect = get_capture_rect(hwnd, area)
    start_x = int(rect[0] + start_raw_point[0])
    start_y = int(rect[1] + start_raw_point[1])
    end_x = int(rect[0] + end_raw_point[0])
    end_y = int(rect[1] + end_raw_point[1])
    duration = max(0.08, min(2.0, duration_ms / 1000))
    steps = max(4, min(60, int(duration * 60)))

    activate_window(hwnd)
    time.sleep(0.03)
    win32api.SetCursorPos((start_x, start_y))
    time.sleep(0.03)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    try:
        for step in range(1, steps + 1):
            t = step / steps
            x = int(round(start_x + (end_x - start_x) * t))
            y = int(round(start_y + (end_y - start_y) * t))
            win32api.SetCursorPos((x, y))
            time.sleep(duration / steps)
    finally:
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def iter_mjpeg_frames(
    title: str,
    fps: float,
    crop: tuple[int, int, int, int],
    trim_border: tuple[int, int, int, int],
    rotate: str,
    area: str,
    mode: str,
    max_width: int,
    max_height: int,
    scale: float,
    fixed_width: int,
    fixed_height: int,
    refind_interval: float,
    quality: int,
    title_match: str = "contains",
    auto_dismiss_popup: bool = False,
    popup_check_interval: float = 3.0,
    on_frame: Callable[[np.ndarray], None] | None = None,
):
    ensure_windows_runtime()
    set_dpi_awareness()
    if fps <= 0:
        raise ValueError("fps 必须大于 0")

    target = find_window(title, title_match)
    capturer = WindowCapture(target.hwnd, area, mode, title, title_match, refind_interval)
    interval = 1.0 / fps
    last_popup_check_at = 0.0
    last_popup_click_at = 0.0

    while True:
        started = time.perf_counter()
        raw_frame = capturer.capture()
        if raw_frame is None:
            time.sleep(min(0.2, interval))
            continue

        frame = process_frame(raw_frame, crop, trim_border, rotate, max_width, max_height, scale, fixed_width, fixed_height)
        if on_frame is not None:
            on_frame(frame)
        now = time.perf_counter()
        can_auto_click = (
            auto_dismiss_popup
            and fixed_width <= 0
            and fixed_height <= 0
            and max_width <= 0
            and max_height <= 0
            and now - last_popup_check_at >= max(1.0, popup_check_interval)
            and now - last_popup_click_at >= 5.0
        )
        if can_auto_click:
            last_popup_check_at = now
            cancel_point = detect_sunlogin_promotion_cancel_point(frame)
            if cancel_point is not None:
                raw_point = map_processed_point_to_raw_point(
                    cancel_point,
                    raw_shape=raw_frame.shape,
                    crop=crop,
                    trim_border=trim_border,
                    rotate=rotate,
                )
                if raw_point is not None:
                    try:
                        click_window_raw_point(capturer.hwnd, area, raw_point)
                        last_popup_click_at = now
                        print(f"已自动点击向日葵弹窗取消按钮: frame={cancel_point} raw={raw_point}", flush=True)
                    except Exception as exc:
                        print(f"自动关闭向日葵弹窗失败: {exc}", file=sys.stderr, flush=True)

        data = encode_jpeg(frame, quality)
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            + f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
            + data
            + b"\r\n"
        )

        elapsed = time.perf_counter() - started
        if elapsed < interval:
            time.sleep(interval - elapsed)


def save_snapshot(
    capturer: WindowCapture,
    output: Path,
    crop: tuple[int, int, int, int],
    trim_border: tuple[int, int, int, int],
    rotate: str,
    max_width: int,
    max_height: int,
    scale: float,
    fixed_width: int,
    fixed_height: int,
) -> None:
    frame = capturer.capture()
    if frame is None:
        raise RuntimeError("截图失败")
    frame = process_frame(frame, crop, trim_border, rotate, max_width, max_height, scale, fixed_width, fixed_height)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), frame):
        raise RuntimeError(f"保存截图失败: {output}")


def preview_loop(
    capturer: WindowCapture,
    fps: float,
    crop: tuple[int, int, int, int],
    trim_border: tuple[int, int, int, int],
    rotate: str,
    max_width: int,
    max_height: int,
    scale: float,
    fixed_width: int,
    fixed_height: int,
    window_x: int | None,
    window_y: int | None,
    preview_title: str,
    resizable: bool,
) -> None:
    if fps <= 0:
        raise ValueError("fps 必须大于 0")

    interval = 1.0 / fps
    preview_title = ascii_title(preview_title)
    window_mode = cv2.WINDOW_NORMAL if resizable else cv2.WINDOW_AUTOSIZE
    cv2.namedWindow(preview_title, window_mode)
    if resizable and fixed_width > 0 and fixed_height > 0:
        cv2.resizeWindow(preview_title, fixed_width, fixed_height)
    if window_x is not None and window_y is not None:
        cv2.moveWindow(preview_title, window_x, window_y)

    frame_count = 0
    stat_start = time.perf_counter()

    while True:
        started = time.perf_counter()
        frame = capturer.capture()
        if frame is None:
            time.sleep(min(0.2, interval))
            continue

        frame = process_frame(frame, crop, trim_border, rotate, max_width, max_height, scale, fixed_width, fixed_height)
        cv2.imshow(preview_title, frame)

        frame_count += 1
        now = time.perf_counter()
        if now - stat_start >= 1.0:
            actual_fps = frame_count / (now - stat_start)
            frame_count = 0
            stat_start = now
            cv2.setWindowTitle(preview_title, f"{preview_title} | target {fps:g} FPS | actual {actual_fps:.1f} FPS")

        elapsed = time.perf_counter() - started
        wait_ms = max(1, int((interval - elapsed) * 1000))
        key = cv2.waitKey(wait_ms) & 0xFF
        if key in (27, ord("q")) or not is_cv_window_open(preview_title):
            break

    try:
        cv2.destroyWindow(preview_title)
    except cv2.error:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="捕捉指定 Windows 窗口，按顺时针角度旋转后预览。")
    parser.add_argument("--title", default="1249152866", help="目标窗口标题包含的文字")
    parser.add_argument("--fps", type=float, default=15.0, help="目标刷新帧率")
    parser.add_argument("--rotate", choices=["0", "90", "180", "270", "ccw", "cw", "none"], default="90", help="顺时针旋转角度")
    parser.add_argument("--area", choices=["outer", "client"], default="outer", help="捕捉外框或客户区")
    parser.add_argument("--mode", choices=["auto", "printwindow", "screen"], default="screen", help="窗口捕捉模式")
    parser.add_argument("--crop", type=parse_crop, default=(0, 0, 0, 0), help="旋转前裁边：left,top,right,bottom")
    parser.add_argument("--trim-border", type=parse_crop, default=(0, 0, 0, 0), help="旋转后裁边：left,top,right,bottom")
    parser.add_argument("--scale", type=float, default=1.0, help="预览缩放比例")
    parser.add_argument("--max-width", type=int, default=0, help="预览最大宽度，0 表示不限制")
    parser.add_argument("--max-height", type=int, default=0, help="预览最大高度，0 表示不限制")
    parser.add_argument("--fixed-width", type=int, default=0, help="固定输出画布宽度，需和 --fixed-height 一起使用")
    parser.add_argument("--fixed-height", type=int, default=0, help="固定输出画布高度，需和 --fixed-width 一起使用")
    parser.add_argument("--window-x", type=int, default=None, help="预览窗口左上角 x 坐标")
    parser.add_argument("--window-y", type=int, default=None, help="预览窗口左上角 y 坐标")
    parser.add_argument("--preview-title", default="codeyun-sunlogin-rotate", help="OpenCV 预览窗口标题，建议只用英文/数字")
    parser.add_argument("--resizable", action="store_true", help="允许手动缩放 OpenCV 预览窗口")
    parser.add_argument("--refind-interval", type=float, default=1.0, help="目标窗口失效后重新查找的最小间隔秒数，0 表示禁用")
    parser.add_argument("--activate", action="store_true", help="启动时尝试激活目标窗口")
    parser.add_argument("--list", action="store_true", help="列出匹配窗口后退出")
    parser.add_argument("--snapshot", type=Path, help="保存一帧旋转后的截图并退出")
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_windows_runtime()
    set_dpi_awareness()
    args = build_parser().parse_args(argv)
    if (args.fixed_width > 0) != (args.fixed_height > 0):
        print("错误: --fixed-width 和 --fixed-height 必须同时设置", file=sys.stderr)
        return 1

    if args.list:
        candidates = iter_windows(args.title)
        if not candidates:
            print(f"未找到标题包含 {args.title!r} 的可见窗口")
            return 1
        for item in candidates:
            print(
                f"hwnd={item.hwnd} title={item.title!r} class={item.class_name!r} "
                f"rect={item.rect} size={item.width}x{item.height}"
            )
        return 0

    target = find_window(args.title)
    print(f"目标窗口: hwnd={target.hwnd} title={target.title!r} rect={target.rect} size={target.width}x{target.height}", flush=True)

    if args.activate:
        activate_window(target.hwnd)
        time.sleep(0.2)

        capturer = WindowCapture(target.hwnd, args.area, args.mode, args.title, "contains", args.refind_interval)
    if args.snapshot:
        save_snapshot(
            capturer,
            args.snapshot,
            args.crop,
            args.trim_border,
            args.rotate,
            args.max_width,
            args.max_height,
            args.scale,
            args.fixed_width,
            args.fixed_height,
        )
        print(f"已保存: {args.snapshot}", flush=True)
        return 0

    preview_loop(
        capturer,
        args.fps,
        args.crop,
        args.trim_border,
        args.rotate,
        args.max_width,
        args.max_height,
        args.scale,
        args.fixed_width,
        args.fixed_height,
        args.window_x,
        args.window_y,
        args.preview_title,
        args.resizable,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
