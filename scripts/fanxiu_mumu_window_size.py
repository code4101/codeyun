from __future__ import annotations

import argparse
import ctypes
import json
from ctypes import wintypes
from typing import Any

import win32con
import win32gui


DWMWA_EXTENDED_FRAME_BOUNDS = 9
TARGET_RENDER_PHYSICAL_WIDTH = 900
TARGET_RENDER_PHYSICAL_HEIGHT = 1600
TARGET_MAIN_LOGICAL_WIDTH = 607
TARGET_MAIN_LOGICAL_HEIGHT = 1111


def _extended_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
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


def _window_dpi(hwnd: int) -> int:
    try:
        return int(ctypes.windll.user32.GetDpiForWindow(hwnd))
    except Exception:
        return 96


def _window_info(hwnd: int) -> dict[str, Any]:
    window_rect = win32gui.GetWindowRect(hwnd)
    extended_rect = _extended_rect(hwnd)
    client_rect = win32gui.GetClientRect(hwnd)
    client_top_left = win32gui.ClientToScreen(hwnd, (client_rect[0], client_rect[1]))
    client_bottom_right = win32gui.ClientToScreen(hwnd, (client_rect[2], client_rect[3]))
    dpi = _window_dpi(hwnd)
    return {
        "hwnd": hwnd,
        "title": win32gui.GetWindowText(hwnd).strip(),
        "class": win32gui.GetClassName(hwnd),
        "window_rect": window_rect,
        "window_size_logical": [window_rect[2] - window_rect[0], window_rect[3] - window_rect[1]],
        "extended_rect_physical": extended_rect,
        "extended_size_physical": [extended_rect[2] - extended_rect[0], extended_rect[3] - extended_rect[1]],
        "client_screen_rect_logical": [
            client_top_left[0],
            client_top_left[1],
            client_bottom_right[0],
            client_bottom_right[1],
        ],
        "client_size_logical": [
            client_bottom_right[0] - client_top_left[0],
            client_bottom_right[1] - client_top_left[1],
        ],
        "dpi": dpi,
        "scale": round(dpi / 96, 4),
    }


def _iter_mumu_windows() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def callback(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd).strip()
        if "凡人修仙传" not in title and "MuMu" not in title:
            return True
        items.append(_window_info(hwnd))
        return True

    win32gui.EnumWindows(callback, None)
    items.sort(key=lambda item: (0 if "凡人修仙传" in item["title"] else 1, item["title"], item["class"]))
    return items


def _find_main_game_window() -> dict[str, Any]:
    for item in _iter_mumu_windows():
        if "凡人修仙传" in item["title"]:
            return item
    raise RuntimeError("未找到凡修 MuMu 主窗口")


def _target_main_size(hwnd: int) -> tuple[int, int]:
    dpi = _window_dpi(hwnd)
    scale = dpi / 96 if dpi > 0 else 1.0
    if abs(scale - 1.5) < 0.05:
        return TARGET_MAIN_LOGICAL_WIDTH, TARGET_MAIN_LOGICAL_HEIGHT
    render_width = int(round(TARGET_RENDER_PHYSICAL_WIDTH / scale))
    render_height = int(round(TARGET_RENDER_PHYSICAL_HEIGHT / scale))
    # Calibrated from MuMu default-size window: main window is render area plus chrome.
    return render_width + 7, render_height + 44


def normalize_mumu_window_size(*, apply: bool = False) -> dict[str, Any]:
    before = _find_main_game_window()
    hwnd = int(before["hwnd"])
    target_width, target_height = _target_main_size(hwnd)
    result: dict[str, Any] = {
        "target_main_size_logical": [target_width, target_height],
        "target_render_size_physical": [TARGET_RENDER_PHYSICAL_WIDTH, TARGET_RENDER_PHYSICAL_HEIGHT],
        "before": before,
        "applied": False,
    }
    if apply:
        left, top, _right, _bottom = before["window_rect"]
        win32gui.SetWindowPos(
            hwnd,
            None,
            int(left),
            int(top),
            int(target_width),
            int(target_height),
            win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
        )
        result["applied"] = True
        result["after"] = _find_main_game_window()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or restore Fanxiu MuMu desktop window size.")
    parser.add_argument("--apply", action="store_true", help="resize the main MuMu game window to calibrated default size")
    parser.add_argument("--list", action="store_true", help="list all visible MuMu related windows")
    args = parser.parse_args()
    if args.list:
        payload = {"windows": _iter_mumu_windows()}
    else:
        payload = normalize_mumu_window_size(apply=bool(args.apply))
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
