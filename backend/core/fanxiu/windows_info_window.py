from __future__ import annotations

"""Windows renderer for the Fanxiu information window.

The renderer consumes the persisted scene snapshot and draws a click-through
vector layer over MuMu's Android render viewport.  It never captures the game,
performs recognition, or sends input.
"""

import argparse
import ctypes
import os
import time
import tkinter as tk
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyxllib.prog import read_json_state_dict, write_json_state

from backend.core.fanxiu.info_window import (
    fanxiu_info_window_state_path,
    fanxiu_windows_info_window_client,
    fanxiu_windows_info_window_heartbeat_path,
    format_fanxiu_observation_age,
    format_fanxiu_scene_text,
    read_fanxiu_info_window_settings,
    read_fanxiu_info_window_user_settings,
    write_fanxiu_info_window_settings,
    write_fanxiu_info_window_user_settings,
)
from backend.core.services.launcher import popen_python_module_service
from backend.core.temp_paths import codeyun_temp_root


TRANSPARENT_COLOR = "#010203"
INFO_WINDOW_POLL_MILLISECONDS = 1000
MUMU_TITLE_MARKER = "凡人修仙传：人界篇-Powered by"
MUTEX_NAME = "Local\\CodeYun.FanxiuInfoWindow"
STOP_EVENT_NAME = "Local\\CodeYun.FanxiuInfoWindow.Stop"
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
GWLP_HWNDPARENT = -8


def _kernel32() -> Any:
    """Return kernel32 with 64-bit-safe named-object function signatures."""

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.SetEvent.restype = wintypes.BOOL
    kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
    kernel32.ResetEvent.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


@dataclass(frozen=True)
class ScreenRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


def calculate_render_rect(
    client_rect: ScreenRect,
    *,
    frame_width: int = 900,
    frame_height: int = 1600,
) -> ScreenRect:
    """Fit the Android frame into MuMu's client area, centered and bottom-aligned.

    :param ScreenRect client_rect: MuMu client rectangle in physical screen pixels.
    :param int frame_width: Android frame width.
    :param int frame_height: Android frame height.
    :return ScreenRect: Physical screen rectangle occupied by the Android frame.
    """

    if client_rect.width <= 0 or client_rect.height <= 0 or frame_width <= 0 or frame_height <= 0:
        return ScreenRect(0, 0, 0, 0)
    scale = min(client_rect.width / frame_width, client_rect.height / frame_height)
    width = max(1, min(client_rect.width, round(frame_width * scale)))
    height = max(1, min(client_rect.height, round(frame_height * scale)))
    left = client_rect.left + (client_rect.width - width) // 2
    top = client_rect.bottom - height
    return ScreenRect(left, top, left + width, top + height)


def select_overlay_boxes(payload: dict[str, Any], settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Select one box scope; all Shapes already includes scene identity Shapes."""
    if settings.get("show_all_shapes", False):
        boxes = payload.get("all_shape_boxes") or []
    elif settings.get("show_scene_identity_shapes", True):
        boxes = payload.get("boxes") or []
    else:
        boxes = []
    return [box for box in boxes if isinstance(box, dict)]


def _enable_per_monitor_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _find_mumu_window() -> int | None:
    import win32gui

    candidates: list[tuple[int, int]] = []

    def callback(hwnd: int, _: object) -> bool:
        title = win32gui.GetWindowText(hwnd).strip()
        if MUMU_TITLE_MARKER not in title:
            return True
        class_name = win32gui.GetClassName(hwnd)
        priority = 0 if class_name.endswith("WindowIcon") else 1
        candidates.append((priority, int(hwnd)))
        return True

    win32gui.EnumWindows(callback, None)
    candidates.sort()
    return candidates[0][1] if candidates else None


def _client_screen_rect(hwnd: int) -> ScreenRect:
    import win32gui

    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    top_left = win32gui.ClientToScreen(hwnd, (left, top))
    bottom_right = win32gui.ClientToScreen(hwnd, (right, bottom))
    return ScreenRect(int(top_left[0]), int(top_left[1]), int(bottom_right[0]), int(bottom_right[1]))


class FanxiuWindowsInfoWindow:
    """Transparent, input-pass-through renderer attached to the MuMu window."""

    def __init__(
        self,
        *,
        state_path: Path | None = None,
        heartbeat_path: Path | None = None,
        stop_event_handle: int | None = None,
    ) -> None:
        _enable_per_monitor_dpi_awareness()
        self.state_path = state_path or fanxiu_info_window_state_path()
        self.heartbeat_path = heartbeat_path or fanxiu_windows_info_window_heartbeat_path()
        self.stop_event_handle = stop_event_handle
        self.target_hwnd: int | None = None
        self.last_target_search_at = 0.0
        self.last_revision: int | None = None
        self.last_settings_signature: tuple[tuple[str, bool], ...] | None = None
        self.last_observation_age_text = ""
        self.last_geometry: ScreenRect | None = None
        self.last_heartbeat_at = 0.0
        self.visible = False
        self.payload: dict[str, Any] = {}
        self.settings = read_fanxiu_info_window_settings()

        self.root = tk.Tk(className="FanxiuInfoWindow")
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.configure(bg=TRANSPARENT_COLOR)
        self.root.wm_attributes("-transparentcolor", TRANSPARENT_COLOR)
        self.root.wm_attributes("-topmost", False)
        self.canvas = tk.Canvas(
            self.root,
            bg=TRANSPARENT_COLOR,
            highlightthickness=0,
            borderwidth=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.root.update_idletasks()
        self.overlay_hwnd = self._configure_native_window()

    def _configure_native_window(self) -> int:
        import win32con
        import win32gui

        child_hwnd = int(self.root.winfo_id())
        hwnd = int(win32gui.GetParent(child_hwnd) or child_hwnd)
        ex_style = int(win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE))
        ex_style |= (
            win32con.WS_EX_LAYERED
            | win32con.WS_EX_TRANSPARENT
            | win32con.WS_EX_TOOLWINDOW
            | win32con.WS_EX_NOACTIVATE
        )
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style)
        return hwnd

    def _attach_owner(self, hwnd: int) -> None:
        user32 = ctypes.windll.user32
        setter = user32.SetWindowLongPtrW
        setter.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        setter.restype = ctypes.c_void_p
        setter(ctypes.c_void_p(self.overlay_hwnd), GWLP_HWNDPARENT, ctypes.c_void_p(hwnd))

    def _target_ready(self, now: float) -> bool:
        import win32gui

        hwnd = self.target_hwnd
        if hwnd is None or not win32gui.IsWindow(hwnd):
            if now - self.last_target_search_at < 0.5:
                return False
            self.last_target_search_at = now
            hwnd = _find_mumu_window()
            self.target_hwnd = hwnd
            if hwnd is not None:
                self._attach_owner(hwnd)
        return bool(
            hwnd is not None
            and win32gui.IsWindow(hwnd)
            and win32gui.IsWindowVisible(hwnd)
            and not win32gui.IsIconic(hwnd)
        )

    def _read_payload(self) -> None:
        try:
            payload = dict(read_json_state_dict(self.state_path))
        except Exception:
            payload = {}
        revision = int(payload.get("revision") or 0)
        settings = read_fanxiu_info_window_settings()
        settings_signature = tuple(sorted(settings.items()))
        observation_age_text = format_fanxiu_observation_age(
            payload.get("observed_at"),
            now=time.time(),
        )
        if (
            revision != self.last_revision
            or settings_signature != self.last_settings_signature
            or observation_age_text != self.last_observation_age_text
        ):
            self.payload = payload
            self.settings = settings
            self.last_revision = revision
            self.last_settings_signature = settings_signature
            self.last_observation_age_text = observation_age_text
            self._draw()

    def _render_geometry(self) -> ScreenRect:
        frame_width = max(1, int(self.payload.get("frame_width") or 900))
        frame_height = max(1, int(self.payload.get("frame_height") or 1600))
        return calculate_render_rect(
            _client_screen_rect(int(self.target_hwnd or 0)),
            frame_width=frame_width,
            frame_height=frame_height,
        )

    def _move(self, rect: ScreenRect) -> None:
        import win32con
        import win32gui

        if rect == self.last_geometry:
            return
        self.last_geometry = rect
        self.root.geometry(f"{rect.width}x{rect.height}+{rect.left}+{rect.top}")
        self.root.update_idletasks()
        win32gui.SetWindowPos(
            self.overlay_hwnd,
            win32con.HWND_TOP,
            rect.left,
            rect.top,
            rect.width,
            rect.height,
            win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
        )
        self._draw()

    def _draw(self) -> None:
        if not hasattr(self, "canvas"):
            return
        width = max(1, int(self.canvas.winfo_width()))
        height = max(1, int(self.canvas.winfo_height()))
        self.canvas.delete("all")
        scene_id = self.payload.get("scene_id")
        score = max(0.0, float(self.payload.get("score") or 0.0))
        text = format_fanxiu_scene_text(
            int(scene_id) if scene_id is not None else None,
            score,
            asset_directory=str(self.payload.get("asset_directory") or ""),
            show_scene_id=bool(self.settings.get("show_scene_id", True)),
            show_scene_score=bool(self.settings.get("show_scene_score", True)),
        )
        observation_age_text = format_fanxiu_observation_age(self.payload.get("observed_at"))
        if observation_age_text:
            text = " ".join(part for part in (text, observation_age_text) if part)
        if text:
            self.canvas.create_text(
                12,
                12,
                anchor="nw",
                text=text,
                fill="white",
                font=("Microsoft YaHei UI", 15),
            )
        frame_width = max(1.0, float(self.payload.get("frame_width") or 900))
        frame_height = max(1.0, float(self.payload.get("frame_height") or 1600))
        scale_x = width / frame_width
        scale_y = height / frame_height
        boxes = select_overlay_boxes(self.payload, self.settings)
        for box in boxes:
            if not isinstance(box, dict):
                continue
            left = float(box.get("x") or 0.0) * scale_x
            top = float(box.get("y") or 0.0) * scale_y
            right = left + float(box.get("w") or 0.0) * scale_x
            bottom = top + float(box.get("h") or 0.0) * scale_y
            self.canvas.create_rectangle(left, top, right, bottom, outline="white", width=2)

    def _hide(self) -> None:
        if self.visible:
            self.root.withdraw()
            self.visible = False
        self.last_geometry = None

    def _show(self) -> None:
        if not self.visible:
            self.root.deiconify()
            self.visible = True

    def _stopping(self) -> bool:
        if not self.stop_event_handle:
            return False
        return _kernel32().WaitForSingleObject(self.stop_event_handle, 0) == WAIT_OBJECT_0

    def _heartbeat(self, now: float) -> None:
        if now - self.last_heartbeat_at < 0.5:
            return
        self.last_heartbeat_at = now
        try:
            write_json_state(self.heartbeat_path, {
                "ok": True,
                "running": True,
                "pid": os.getpid(),
                "visible": self.visible,
                "overlay_hwnd": self.overlay_hwnd,
                "target_hwnd": self.target_hwnd,
                "updated_at": time.time(),
            })
        except Exception:
            pass

    def poll(self) -> None:
        now = time.monotonic()
        if self._stopping():
            self.close()
            return
        try:
            self.settings = read_fanxiu_info_window_settings()
            if not self.settings.get("enabled", True) or not self._target_ready(now):
                self._hide()
            else:
                self._read_payload()
                rect = self._render_geometry()
                if rect.width <= 0 or rect.height <= 0:
                    self._hide()
                else:
                    self._move(rect)
                    self._show()
        except Exception:
            self.target_hwnd = None
            self._hide()
        self._heartbeat(now)
        self.root.after(INFO_WINDOW_POLL_MILLISECONDS, self.poll)

    def close(self) -> None:
        self._hide()
        try:
            write_json_state(self.heartbeat_path, {
                "ok": True,
                "running": False,
                "pid": os.getpid(),
                "visible": False,
                "overlay_hwnd": self.overlay_hwnd,
                "target_hwnd": self.target_hwnd,
                "updated_at": time.time(),
            })
        except Exception:
            pass
        self.root.destroy()

    def run(self) -> None:
        self.root.after(0, self.poll)
        self.root.mainloop()


def _create_single_instance_handles() -> tuple[int | None, int | None, bool]:
    if os.name != "nt":
        return None, None, False
    kernel32 = _kernel32()
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    already_running = kernel32.GetLastError() == ERROR_ALREADY_EXISTS
    stop_event = kernel32.CreateEventW(None, True, False, STOP_EVENT_NAME)
    if stop_event and not already_running:
        kernel32.ResetEvent(stop_event)
    return mutex, stop_event, already_running


def stop_running_renderer() -> bool:
    if os.name != "nt":
        return False
    kernel32 = _kernel32()
    handle = kernel32.OpenEventW(EVENT_MODIFY_STATE | SYNCHRONIZE, False, STOP_EVENT_NAME)
    if not handle:
        return False
    try:
        return bool(kernel32.SetEvent(handle))
    finally:
        kernel32.CloseHandle(handle)


def start_windows_renderer(*, wait_seconds: float = 2.0) -> dict[str, Any]:
    """Start the single renderer without opening a console window."""

    if os.name != "nt":
        raise RuntimeError("凡修信息窗 Windows renderer 仅支持 Windows")
    current = fanxiu_windows_info_window_client.status()
    if current.get("running"):
        return current
    repo_root = Path(__file__).resolve().parents[3]
    log_dir = codeyun_temp_root("fanxiu-windows-info-window")
    stdout_path = log_dir / "stdout.log"
    stderr_path = log_dir / "stderr.log"
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = popen_python_module_service(
            "backend.core.fanxiu.windows_info_window",
            preferred_root=repo_root,
            cwd=str(repo_root),
            stdout=stdout,
            stderr=stderr,
        )
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    while time.monotonic() < deadline:
        status = fanxiu_windows_info_window_client.status()
        if status.get("running"):
            return status
        if process.poll() is not None:
            break
        time.sleep(0.05)
    return {
        **fanxiu_windows_info_window_client.status(),
        "started_pid": process.pid,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }


def stop_windows_renderer(*, wait_seconds: float = 2.0) -> dict[str, Any]:
    """Stop the renderer without sending input to MuMu or the game."""

    stop_running_renderer()
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    while time.monotonic() < deadline:
        status = fanxiu_windows_info_window_client.status()
        if not status.get("running"):
            return status
        time.sleep(0.05)
    return fanxiu_windows_info_window_client.status()


def read_info_window_control_status(*, user_id: int | None = None, ensure_renderer: bool = False) -> dict[str, Any]:
    settings = (
        read_fanxiu_info_window_user_settings(user_id)
        if user_id is not None
        else read_fanxiu_info_window_settings()
    )
    if settings != read_fanxiu_info_window_settings():
        write_fanxiu_info_window_settings(settings)
    renderer = fanxiu_windows_info_window_client.status()
    if ensure_renderer and settings["enabled"] and not renderer.get("running"):
        renderer = start_windows_renderer()
    from backend.core.fanxiu.info_window import read_fanxiu_info_window_state

    return {
        "ok": True,
        "settings": settings,
        "renderer": renderer,
        "scene": read_fanxiu_info_window_state(),
    }


def update_info_window_control(settings: dict[str, Any], *, user_id: int | None = None) -> dict[str, Any]:
    saved = (
        write_fanxiu_info_window_user_settings(user_id, settings)
        if user_id is not None
        else write_fanxiu_info_window_settings(settings)
    )
    write_fanxiu_info_window_settings(saved)
    renderer = start_windows_renderer() if saved["enabled"] else stop_windows_renderer()
    return {
        "ok": True,
        "settings": saved,
        "renderer": renderer,
        "scene": {},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="凡修信息窗 Windows 透明悬浮层")
    parser.add_argument("--stop", action="store_true", help="停止正在运行的信息窗")
    args = parser.parse_args(argv)
    if args.stop:
        return 0 if stop_running_renderer() else 1
    if os.name != "nt":
        raise RuntimeError("凡修信息窗 Windows renderer 仅支持 Windows")
    mutex, stop_event, already_running = _create_single_instance_handles()
    if already_running:
        return 0
    try:
        FanxiuWindowsInfoWindow(stop_event_handle=stop_event).run()
    finally:
        kernel32 = _kernel32()
        if stop_event:
            kernel32.CloseHandle(stop_event)
        if mutex:
            kernel32.CloseHandle(mutex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
