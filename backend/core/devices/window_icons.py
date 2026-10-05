"""Read software-owned Windows icons without activating or capturing windows."""
from __future__ import annotations

import base64
from functools import lru_cache
from io import BytesIO

import numpy as np
from PIL import Image


def _icon_png(icon: int) -> str:
    """Render on black/white to retain transparency for alpha and mask icons."""
    import win32con
    import win32gui
    import win32ui

    size = 32
    screen_handle = win32gui.GetDC(0)
    screen = win32ui.CreateDCFromHandle(screen_handle)
    dc = screen.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    old = None
    try:
        bitmap.CreateCompatibleBitmap(screen, size, size)
        old = dc.SelectObject(bitmap)
        backgrounds = []
        for color in (0, 0xFFFFFF):
            dc.FillSolidRect((0, 0, size, size), color)
            win32gui.DrawIconEx(dc.GetSafeHdc(), 0, 0, icon, size, size, 0, None, win32con.DI_NORMAL)
            backgrounds.append(np.frombuffer(bitmap.GetBitmapBits(True), dtype=np.uint8)
                               .reshape(size, size, 4)[..., :3].astype(np.float32))
        black, white = backgrounds
        alpha = np.clip(255 - np.max(white - black, axis=2), 0, 255)
        rgb = np.clip(black[..., ::-1] * 255 / np.maximum(alpha[..., None], 1), 0, 255)
        pixels = np.dstack((np.rint(rgb), np.rint(alpha))).astype(np.uint8)
        output = BytesIO()
        Image.fromarray(pixels).save(output, format='PNG')
        return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii')
    finally:
        if old is not None:
            dc.SelectObject(old)
        win32gui.DeleteObject(bitmap.GetHandle())
        dc.DeleteDC()
        win32gui.ReleaseDC(0, screen_handle)


@lru_cache(maxsize=128)
def executable_icon(executable: str) -> str | None:
    """Return the original executable icon; only owned Shell handles are freed."""
    import win32gui
    import pywintypes
    from win32com.shell import shell, shellcon

    if not executable:
        return None
    icon = 0
    try:
        _, info = shell.SHGetFileInfo(executable, 0, shellcon.SHGFI_ICON | shellcon.SHGFI_LARGEICON)
        icon = info[0]
        return _icon_png(icon) if icon else None
    except (OSError, RuntimeError, pywintypes.error):
        return None
    finally:
        if icon:
            win32gui.DestroyIcon(icon)


def window_icon(hwnd: int, executable: str = '') -> str | None:
    """Prefer the window's icon, then its class, then the executable resource.

    WM_GETICON is bounded so an unresponsive app cannot stall the window list.
    Window/class handles are borrowed and must never be destroyed here.
    """
    import win32con
    import win32gui
    import pywintypes

    # ICON_SMALL2 (2) is absent from some pywin32 constant tables.
    for kind in (win32con.ICON_BIG, 2, win32con.ICON_SMALL):
        try:
            _, icon = win32gui.SendMessageTimeout(hwnd, win32con.WM_GETICON, kind, 0,
                                                win32con.SMTO_ABORTIFHUNG, 100)
            if icon:
                return _icon_png(icon)
        except (OSError, RuntimeError, pywintypes.error):
            break
    for index in (win32con.GCL_HICON, win32con.GCL_HICONSM):
        try:
            icon = win32gui.GetClassLong(hwnd, index)
            if icon:
                return _icon_png(icon)
        except (OSError, RuntimeError, pywintypes.error):
            pass
    return executable_icon(executable)
