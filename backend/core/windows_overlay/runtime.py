from __future__ import annotations

"""Windows click-through renderer for declarative overlay scene documents."""

import argparse
import ctypes
import json
import os
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from typing import Any

from backend.core.windows_overlay.protocol import (
    default_heartbeat_path,
    default_preferences_path,
    default_scene_path,
    read_scene_document,
    read_overlay_preferences,
    write_overlay_preferences,
)


TRANSPARENT_COLOR = "#010203"
MUTEX_NAME = "Local\\CodeYun.WindowsOverlayRuntime"
STOP_EVENT_NAME = "Local\\CodeYun.WindowsOverlayRuntime.Stop"
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
GWLP_HWNDPARENT = -8


class BooleanSwitch(tk.Canvas):
    """Small keyboard-accessible boolean switch for the controller window."""

    def __init__(self, master: tk.Misc, *, value: bool, command: Any) -> None:
        super().__init__(
            master,
            width=46,
            height=26,
            bg="#FFFFFF",
            highlightthickness=0,
            borderwidth=0,
            cursor="hand2",
            takefocus=True,
        )
        self.value = bool(value)
        self.command = command
        self.bind("<Button-1>", self._toggle)
        self.bind("<space>", self._toggle)
        self.bind("<Return>", self._toggle)
        self.bind("<FocusIn>", lambda _event: self._draw())
        self.bind("<FocusOut>", lambda _event: self._draw())
        self._draw()

    def _toggle(self, _event: tk.Event[Any] | None = None) -> str:
        self.command(not self.value)
        return "break"

    def set_value(self, value: bool) -> None:
        self.value = bool(value)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        track = "#1677FF" if self.value else "#B7C0CC"
        focus = "#91CAFF" if self.focus_get() == self else "#FFFFFF"
        self.create_oval(0, 0, 46, 26, fill=focus, outline=focus)
        self.create_oval(2, 2, 24, 24, fill=track, outline=track)
        self.create_oval(22, 2, 44, 24, fill=track, outline=track)
        self.create_rectangle(13, 2, 33, 24, fill=track, outline=track)
        knob_left = 23 if self.value else 3
        self.create_oval(
            knob_left,
            3,
            knob_left + 20,
            23,
            fill="#FFFFFF",
            outline="#FFFFFF",
        )


def _kernel32() -> Any:
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


def enable_per_monitor_dpi_awareness() -> None:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def find_target_window(target: dict[str, Any]) -> int | None:
    import win32gui

    hwnd = int(target.get("hwnd") or 0)
    needle = str(target.get("title_contains") or "").lower()
    if (
        hwnd
        and win32gui.IsWindow(hwnd)
        and (not needle or needle in win32gui.GetWindowText(hwnd).lower())
    ):
        return hwnd
    if not needle:
        return None
    candidates: list[tuple[int, int]] = []

    def callback(candidate: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(candidate):
            return True
        title = win32gui.GetWindowText(candidate).strip()
        if needle not in title.lower():
            return True
        left, top, right, bottom = win32gui.GetWindowRect(candidate)
        candidates.append((max(0, right - left) * max(0, bottom - top), int(candidate)))
        return True

    win32gui.EnumWindows(callback, None)
    return max(candidates)[1] if candidates else None


def target_screen_rect(hwnd: int, area: str) -> tuple[int, int, int, int]:
    import win32gui

    if area == "window":
        return tuple(int(value) for value in win32gui.GetWindowRect(hwnd))
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    screen_left, screen_top = win32gui.ClientToScreen(hwnd, (left, top))
    screen_right, screen_bottom = win32gui.ClientToScreen(hwnd, (right, bottom))
    return int(screen_left), int(screen_top), int(screen_right), int(screen_bottom)


class WindowsOverlayRuntime:
    def __init__(
        self,
        *,
        scene_path: Path,
        heartbeat_path: Path,
        preferences_path: Path,
        stop_event_handle: int | None,
    ) -> None:
        enable_per_monitor_dpi_awareness()
        self.scene_path = scene_path
        self.heartbeat_path = heartbeat_path
        self.preferences_path = preferences_path
        self.stop_event_handle = stop_event_handle
        self.document: dict[str, Any] = {}
        self.target_hwnd: int | None = None
        self.last_revision: int | None = None
        self.last_scene_mtime_ns: int | None = None
        self.last_scene_received_at: float | None = None
        self.last_geometry: tuple[int, int, int, int] | None = None
        self.last_target_search_at = 0.0
        self.last_heartbeat_at = 0.0
        self.visible = False
        self.last_error: str | None = None
        preferences = read_overlay_preferences(preferences_path)
        self.enhancement_enabled = preferences["enhancement_enabled"]
        self.click_through_enabled = preferences["click_through_enabled"]
        self.selection_item: int | None = None
        self.selection_anchor = 0
        self.hovered_popover_id: str | None = None
        self.popover_hit_regions: dict[str, tuple[float, float, float, float]] = {}

        self.root = tk.Tk(className="CodeYunWindowsOverlayControl")
        self.control_window = self._create_control_window()
        control_child_hwnd = int(self.control_window.winfo_id())
        self.overlay_window = tk.Toplevel(self.root, class_="CodeYunWindowsOverlay")
        self.overlay_window.withdraw()
        self.overlay_window.overrideredirect(True)
        self.overlay_window.configure(bg=TRANSPARENT_COLOR)
        self.overlay_window.wm_attributes("-transparentcolor", TRANSPARENT_COLOR)
        self.canvas = tk.Canvas(
            self.overlay_window,
            bg=TRANSPARENT_COLOR,
            highlightthickness=0,
            borderwidth=0,
            selectbackground="#1677FF",
            selectforeground="#FFFFFF",
            selectborderwidth=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._begin_text_selection)
        self.canvas.bind("<B1-Motion>", self._extend_text_selection)
        self.canvas.bind("<Control-c>", self._copy_text_selection)
        self.root.update_idletasks()
        self.overlay_hwnd = self._configure_native_window()
        try:
            import win32gui

            self.control_hwnd = int(win32gui.GetParent(control_child_hwnd) or control_child_hwnd)
        except Exception:
            self.control_hwnd = control_child_hwnd
        self._refresh_control_window()

    def _create_control_window(self) -> tk.Tk:
        window = self.root
        window.title("界面增强")
        window.geometry("380x126+36+96")
        window.resizable(False, False)
        window.configure(bg="#FFFFFF")
        window.attributes("-topmost", True)
        window.protocol("WM_DELETE_WINDOW", self.close)

        body = tk.Frame(window, bg="#FFFFFF")
        body.pack(fill="both", expand=True, padx=18, pady=12)
        display_row = self._create_switch_row(body, "显示增强图层")
        self.enhancement_switch = BooleanSwitch(
            display_row,
            value=self.enhancement_enabled,
            command=self.set_enhancement_enabled,
        )
        self.enhancement_switch.pack(side="right")
        interaction_row = self._create_switch_row(body, "鼠标点击穿透")
        self.click_through_switch = BooleanSwitch(
            interaction_row,
            value=self.click_through_enabled,
            command=self.set_click_through_enabled,
        )
        self.click_through_switch.pack(side="right")
        window.bind("<Control-Shift-space>", lambda _event: self.toggle_enhancement())
        return window

    @staticmethod
    def _create_switch_row(master: tk.Misc, label_text: str) -> tk.Frame:
        row = tk.Frame(master, bg="#FFFFFF", height=42)
        row.pack(fill="x")
        row.pack_propagate(False)
        label = tk.Label(
            row,
            text=label_text,
            bg="#FFFFFF",
            fg="#1F2937",
            font=("Microsoft YaHei UI", 11),
        )
        label.pack(side="left")
        return row

    def _refresh_control_window(self) -> None:
        self.enhancement_switch.set_value(self.enhancement_enabled)
        self.click_through_switch.set_value(self.click_through_enabled)

    def _save_preferences(self) -> None:
        write_overlay_preferences(
            {
                "enhancement_enabled": self.enhancement_enabled,
                "click_through_enabled": self.click_through_enabled,
            },
            self.preferences_path,
        )

    def set_enhancement_enabled(self, enabled: bool) -> None:
        self.enhancement_enabled = bool(enabled)
        self._save_preferences()
        if not self.enhancement_enabled:
            self._hide()
        self._refresh_control_window()

    def toggle_enhancement(self) -> None:
        self.set_enhancement_enabled(not self.enhancement_enabled)

    def set_click_through_enabled(self, enabled: bool) -> None:
        self.click_through_enabled = bool(enabled)
        self._save_preferences()
        self._apply_interaction_style()
        if self.click_through_enabled:
            self.canvas.select_clear()
            self.selection_item = None
        self._refresh_control_window()

    def _configure_native_window(self) -> int:
        import win32con
        import win32gui

        child_hwnd = int(self.overlay_window.winfo_id())
        hwnd = int(win32gui.GetParent(child_hwnd) or child_hwnd)
        self.overlay_hwnd = hwnd
        self._apply_interaction_style()
        return hwnd

    def _apply_interaction_style(self) -> None:
        import win32con
        import win32gui

        hwnd = int(self.overlay_hwnd)
        ex_style = int(win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE))
        ex_style |= win32con.WS_EX_LAYERED | win32con.WS_EX_TOOLWINDOW
        if self.click_through_enabled:
            ex_style |= win32con.WS_EX_TRANSPARENT | win32con.WS_EX_NOACTIVATE
        else:
            ex_style &= ~(win32con.WS_EX_TRANSPARENT | win32con.WS_EX_NOACTIVATE)
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style)
        win32gui.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE
            | win32con.SWP_NOSIZE
            | win32con.SWP_NOZORDER
            | win32con.SWP_FRAMECHANGED
            | win32con.SWP_NOACTIVATE,
        )

    def _text_item_at(self, x: int, y: int) -> int | None:
        for item in reversed(self.canvas.find_overlapping(x, y, x, y)):
            if "selectable-text" in self.canvas.gettags(item):
                return int(item)
        return None

    def _begin_text_selection(self, event: tk.Event[Any]) -> str:
        item = self._text_item_at(event.x, event.y)
        self.canvas.select_clear()
        self.selection_item = item
        if item is not None:
            self.canvas.focus_set()
            self.canvas.focus(item)
            self.selection_anchor = int(self.canvas.index(item, f"@{event.x},{event.y}"))
            self.canvas.select_from(item, self.selection_anchor)
            self.canvas.select_to(item, self.selection_anchor)
        return "break"

    def _extend_text_selection(self, event: tk.Event[Any]) -> str:
        if self.selection_item is not None:
            index = int(self.canvas.index(self.selection_item, f"@{event.x},{event.y}"))
            self.canvas.select_from(self.selection_item, self.selection_anchor)
            self.canvas.select_to(self.selection_item, index)
        return "break"

    def _copy_text_selection(self, _event: tk.Event[Any]) -> str:
        item = self.selection_item
        if item is None:
            return "break"
        try:
            first = int(self.canvas.index(item, "sel.first"))
            last = int(self.canvas.index(item, "sel.last"))
        except tk.TclError:
            return "break"
        text = str(self.canvas.itemcget(item, "text"))[first:last + 1]
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        return "break"

    def _attach_owner(self, hwnd: int) -> None:
        setter = ctypes.windll.user32.SetWindowLongPtrW
        setter.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        setter.restype = ctypes.c_void_p
        setter(ctypes.c_void_p(self.overlay_hwnd), GWLP_HWNDPARENT, ctypes.c_void_p(hwnd))

    def _read_document(self) -> None:
        try:
            mtime_ns = self.scene_path.stat().st_mtime_ns
        except OSError:
            return
        if mtime_ns == self.last_scene_mtime_ns:
            return
        document = read_scene_document(self.scene_path)
        previous_target = self.document.get("target") if self.document else None
        self.last_scene_mtime_ns = mtime_ns
        self.last_scene_received_at = time.monotonic()
        self.document = document
        self.last_error = None
        revision = int(document["revision"])
        if revision != self.last_revision:
            self.last_revision = revision
            if document.get("target") != previous_target:
                self.target_hwnd = None
            self._draw()

    def _scene_fresh(self, now: float) -> bool:
        if self.last_scene_received_at is None:
            return False
        ttl_ms = max(100, int(self.document.get("ttl_ms") or 0))
        received_age_ms = (now - self.last_scene_received_at) * 1000
        published_at = float(self.document.get("published_at") or 0)
        published_age_ms = max(0.0, (time.time() - published_at) * 1000) if published_at else float("inf")
        return received_age_ms <= ttl_ms and published_age_ms <= ttl_ms

    def _target_ready(self, now: float) -> bool:
        import win32gui

        target = self.document.get("target") or {}
        hwnd = self.target_hwnd
        if hwnd is None or not win32gui.IsWindow(hwnd):
            if now - self.last_target_search_at < 0.25:
                return False
            self.last_target_search_at = now
            hwnd = find_target_window(target)
            self.target_hwnd = hwnd
            if hwnd:
                self._attach_owner(hwnd)
        if not hwnd or not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return False
        needle = str(target.get("title_contains") or "").lower()
        if needle and needle not in win32gui.GetWindowText(hwnd).lower():
            self.target_hwnd = None
            return False
        if target.get("only_when_foreground", True):
            foreground = int(win32gui.GetForegroundWindow() or 0)
            if foreground not in {hwnd, self.overlay_hwnd, self.control_hwnd}:
                return False
        return True

    def _move(self) -> None:
        import win32con
        import win32gui

        target = self.document.get("target") or {}
        rect = target_screen_rect(int(self.target_hwnd or 0), str(target.get("area") or "client"))
        if rect == self.last_geometry:
            return
        self.last_geometry = rect
        left, top, right, bottom = rect
        width = max(1, right - left)
        height = max(1, bottom - top)
        self.overlay_window.geometry(f"{width}x{height}+{left}+{top}")
        self.overlay_window.update_idletasks()
        win32gui.SetWindowPos(
            self.overlay_hwnd,
            win32con.HWND_TOP,
            left,
            top,
            width,
            height,
            win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
        )
        self._draw()

    def _geometry_matches_scene(self) -> bool:
        target = self.document.get("target") or {}
        viewport = self.document.get("viewport") or {}
        if viewport.get("coordinate_mode") == "scale":
            return True
        left, top, right, bottom = target_screen_rect(
            int(self.target_hwnd or 0),
            str(target.get("area") or "client"),
        )
        return (
            right - left == int(viewport.get("width") or 0)
            and bottom - top == int(viewport.get("height") or 0)
        )

    def _draw(self) -> None:
        self.canvas.delete("all")
        popover_ids = {
            str(element["id"])
            for element in self.document.get("elements") or []
            if element.get("type") == "popover"
        }
        if self.hovered_popover_id not in popover_ids:
            self.hovered_popover_id = None
        self.popover_hit_regions = {}
        viewport = self.document.get("viewport") or {}
        source_width = max(1.0, float(viewport.get("width") or 1))
        source_height = max(1.0, float(viewport.get("height") or 1))
        if self.last_geometry:
            left, top, right, bottom = self.last_geometry
            render_width = max(1, right - left)
            render_height = max(1, bottom - top)
        else:
            render_width = max(1, self.canvas.winfo_width())
            render_height = max(1, self.canvas.winfo_height())
        scale_x = render_width / source_width
        scale_y = render_height / source_height
        for element in self.document.get("elements") or []:
            x = float(element["x"]) * scale_x
            y = float(element["y"]) * scale_y
            width = float(element["width"]) * scale_x
            height = float(element["height"]) * scale_y
            style = element["style"]
            if element["type"] == "popover":
                self._draw_popover(
                    element,
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                    render_width=render_width,
                    render_height=render_height,
                    scale_x=scale_x,
                    scale_y=scale_y,
                )
                continue
            if element["type"] == "rect":
                self.canvas.create_rectangle(
                    x,
                    y,
                    x + width,
                    y + height,
                    outline=style["stroke"],
                    width=max(1, round(float(style["stroke_width"]) * min(scale_x, scale_y))),
                    fill=(style["background"] if style["background"] != TRANSPARENT_COLOR else ""),
                )
                continue
            padding = int(style["padding"])
            if style["background"] != TRANSPARENT_COLOR:
                self.canvas.create_rectangle(
                    x,
                    y,
                    x + width,
                    y + height,
                    outline=style["background"],
                    fill=style["background"],
                )
            self.canvas.create_text(
                x + padding,
                y + padding,
                anchor="nw",
                width=max(1, width - padding * 2),
                text=element.get("text") or "",
                fill=style["color"],
                font=(
                    "Microsoft YaHei UI",
                    max(8, round(int(style["font_size"]) * min(scale_x, scale_y))),
                    style["font_weight"],
                ),
                tags=("selectable-text", f"element:{element['id']}"),
            )

    def _draw_popover(
        self,
        element: dict[str, Any],
        *,
        x: float,
        y: float,
        width: float,
        height: float,
        render_width: int,
        render_height: int,
        scale_x: float,
        scale_y: float,
    ) -> None:
        element_id = str(element["id"])
        style = element["style"]
        marker_size = max(20.0, min(width, height))
        marker_right = x + marker_size
        marker_bottom = y + marker_size
        self.canvas.create_oval(
            x,
            y,
            marker_right,
            marker_bottom,
            fill=style["background"],
            outline=style["stroke"],
            width=max(1, round(float(style["stroke_width"]) * min(scale_x, scale_y))),
        )
        self.canvas.create_text(
            x + marker_size / 2,
            y + marker_size / 2,
            anchor="center",
            text=element.get("marker") or "?",
            fill=style["color"],
            font=("Microsoft YaHei UI", max(9, round(int(style["font_size"]) * min(scale_x, scale_y))), "bold"),
        )
        self.popover_hit_regions[element_id] = (x, y, marker_right, marker_bottom)
        if self.hovered_popover_id != element_id:
            return

        popup = element["popup"]
        popup_width = min(float(popup["width"]) * scale_x, max(180.0, render_width - 24.0))
        popup_x = marker_right + float(popup["offset_x"]) * scale_x
        if popup_x + popup_width > render_width - 12:
            popup_x = max(12.0, x - popup_width - float(popup["offset_x"]) * scale_x)
        popup_y = max(12.0, y + float(popup["offset_y"]) * scale_y)
        padding = max(6, round(int(popup["padding"]) * min(scale_x, scale_y)))
        content = str(element.get("text") or "")
        title = str(element.get("title") or "")
        display_text = f"{title}\n\n{content}" if title else content
        text_item = self.canvas.create_text(
            popup_x + padding,
            popup_y + padding,
            anchor="nw",
            width=max(1.0, popup_width - padding * 2),
            text=display_text,
            fill=popup["color"],
            font=(
                "Microsoft YaHei UI",
                max(9, round(int(popup["font_size"]) * min(scale_x, scale_y))),
                "normal",
            ),
            tags=("selectable-text", f"element:{element_id}"),
        )
        bbox = self.canvas.bbox(text_item) or (
            round(popup_x + padding),
            round(popup_y + padding),
            round(popup_x + popup_width - padding),
            round(popup_y + padding + 40),
        )
        popup_height = max(48.0, float(bbox[3] - bbox[1] + padding * 2))
        if popup_y + popup_height > render_height - 12:
            adjusted_y = max(12.0, render_height - 12.0 - popup_height)
            self.canvas.move(text_item, 0, adjusted_y - popup_y)
            popup_y = adjusted_y
        card = self.canvas.create_rectangle(
            popup_x,
            popup_y,
            popup_x + popup_width,
            popup_y + popup_height,
            fill=popup["background"],
            outline=popup["stroke"],
            width=1,
        )
        self.canvas.tag_lower(card, text_item)
        self.popover_hit_regions[element_id] = (
            min(x, popup_x),
            min(y, popup_y),
            max(marker_right, popup_x + popup_width),
            max(marker_bottom, popup_y + popup_height),
        )

    def _update_hover_state(self) -> None:
        if not self.visible or not self.last_geometry or not self.popover_hit_regions:
            new_hover = None
        else:
            import win32gui

            screen_x, screen_y = win32gui.GetCursorPos()
            left, top, _right, _bottom = self.last_geometry
            local_x = screen_x - left
            local_y = screen_y - top
            new_hover = next((
                element_id
                for element_id, (x1, y1, x2, y2) in reversed(tuple(self.popover_hit_regions.items()))
                if x1 <= local_x <= x2 and y1 <= local_y <= y2
            ), None)
        if new_hover != self.hovered_popover_id:
            self.hovered_popover_id = new_hover
            self._draw()

    def _hide(self) -> None:
        if self.visible:
            import win32con
            import win32gui

            win32gui.ShowWindow(self.overlay_hwnd, win32con.SW_HIDE)
            self.visible = False
        self.last_geometry = None

    def _show(self) -> None:
        if not self.visible:
            import win32con
            import win32gui

            win32gui.ShowWindow(self.overlay_hwnd, win32con.SW_SHOWNOACTIVATE)
            self.visible = True

    def _stopping(self) -> bool:
        return bool(
            self.stop_event_handle
            and _kernel32().WaitForSingleObject(self.stop_event_handle, 0) == WAIT_OBJECT_0
        )

    def _heartbeat(self, now: float) -> None:
        if now - self.last_heartbeat_at < 0.5:
            return
        self.last_heartbeat_at = now
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        scene_age_ms = (
            round(max(0.0, (time.time() - float(self.document.get("published_at") or 0)) * 1000), 1)
            if self.document.get("published_at")
            else None
        )
        payload = {
            "running": True,
            "visible": self.visible,
            "enhancement_enabled": self.enhancement_enabled,
            "click_through_enabled": self.click_through_enabled,
            "hovered_popover_id": self.hovered_popover_id,
            "pid": os.getpid(),
            "overlay_hwnd": self.overlay_hwnd,
            "target_hwnd": self.target_hwnd,
            "revision": self.last_revision,
            "producer_id": self.document.get("producer_id"),
            "scene_fresh": self._scene_fresh(now) if self.document else False,
            "scene_age_ms": scene_age_ms,
            "scene_ttl_ms": self.document.get("ttl_ms"),
            "last_error": self.last_error,
            "updated_at": time.time(),
        }
        try:
            self.heartbeat_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def poll(self) -> None:
        now = time.monotonic()
        if self._stopping():
            self.close()
            return
        try:
            self._read_document()
            if (
                not self.document
                or not self.enhancement_enabled
                or not self._scene_fresh(now)
                or not self._target_ready(now)
                or not self._geometry_matches_scene()
            ):
                self._hide()
            else:
                self._move()
                self._show()
                self._update_hover_state()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.target_hwnd = None
            self._hide()
        self._heartbeat(now)
        self.root.after(100, self.poll)

    def close(self) -> None:
        self._hide()
        self.root.destroy()

    def run(self) -> None:
        self.root.after(0, self.poll)
        self.root.mainloop()


def stop_running_runtime() -> bool:
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


def _create_single_instance_handles() -> tuple[int | None, int | None, bool]:
    kernel32 = _kernel32()
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    already_running = kernel32.GetLastError() == ERROR_ALREADY_EXISTS
    stop_event = kernel32.CreateEventW(None, True, False, STOP_EVENT_NAME)
    if stop_event and not already_running:
        kernel32.ResetEvent(stop_event)
    return mutex, stop_event, already_running


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CodeYun Windows 通用透明图层运行时")
    parser.add_argument("--scene", type=Path, default=default_scene_path())
    parser.add_argument("--heartbeat", type=Path, default=default_heartbeat_path())
    parser.add_argument("--preferences", type=Path, default=default_preferences_path())
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args(argv)
    if args.stop:
        return 0 if stop_running_runtime() else 1
    if os.name != "nt":
        raise RuntimeError("Windows Overlay Runtime 仅支持 Windows")
    mutex, stop_event, already_running = _create_single_instance_handles()
    if already_running:
        return 0
    try:
        WindowsOverlayRuntime(
            scene_path=args.scene,
            heartbeat_path=args.heartbeat,
            preferences_path=args.preferences,
            stop_event_handle=stop_event,
        ).run()
    finally:
        kernel32 = _kernel32()
        if stop_event:
            kernel32.CloseHandle(stop_event)
        if mutex:
            kernel32.CloseHandle(mutex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
