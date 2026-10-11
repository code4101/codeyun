"""Application window projection: bounded snapshots, changed tiles and explicit input.

This view never creates another agent runtime. The selected native window owns
all operations. Capture restores minimized windows; input verifies identity and size.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections import OrderedDict
from io import BytesIO
import threading
import time
import uuid

import numpy as np
from PIL import Image

from backend.core.devices.window_capture_preview import (
    iter_windows, capture_by_printwindow,
    activate_window, get_capture_rect, set_dpi_awareness, capture_by_screen, click_window_title_bar, restore_window,
)

_lock = threading.Lock()
_frames: OrderedDict[str, tuple[float, int | str, np.ndarray]] = OrderedDict()
_input_secret = secrets.token_bytes(32)
FANXIU_ADB_WINDOW_ID = 'fanxiu:adb-display'


def _window_identity(hwnd: int) -> str:
    """Process creation time prevents a recycled HWND/PID accepting old input."""
    import psutil
    import win32process
    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
    return f'{hwnd}:{pid}:{psutil.Process(pid).create_time()}'


def input_token(identity: str, width: int, height: int) -> str:
    """Geometry authorization is independent of short-lived pixel caches."""
    data = base64.urlsafe_b64encode(json.dumps([identity,width,height], separators=(',',':')).encode()).decode().rstrip('=')
    signature = hmac.new(_input_secret,data.encode(),hashlib.sha256).hexdigest()
    return f'{data}.{signature}'


def validate_input_token(token: str, identity: str, width: int, height: int) -> None:
    try:
        data, signature = token.rsplit('.',1)
        expected = hmac.new(_input_secret,data.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):
            raise ValueError()
        saved_identity, saved_width, saved_height = json.loads(base64.urlsafe_b64decode(data + '=' * (-len(data)%4)))
    except (ValueError, TypeError, UnicodeError):
        raise ValueError('连接已更新，请刷新画面后操作') from None
    if saved_identity != identity:
        raise ValueError('桌面窗口已切换，请刷新后操作')
    if (saved_width,saved_height) != (width,height):
        raise ValueError('窗口尺寸已变化，请刷新后操作')


def focus_projection_window(hwnd: int) -> None:
    """Join the foreground input queue temporarily for service-driven UI input."""
    import win32api
    import win32gui
    import win32process
    import pywintypes
    activate_window(hwnd)
    if win32gui.GetForegroundWindow() == hwnd:
        return
    current = win32api.GetCurrentThreadId()
    foreground = win32gui.GetForegroundWindow()
    threads = {win32process.GetWindowThreadProcessId(handle)[0] for handle in (foreground,hwnd) if handle}
    joined = []
    try:
        for thread in threads - {current}:
            try:
                win32process.AttachThreadInput(current,thread,True)
                joined.append(thread)
            except (OSError, pywintypes.error):
                # Elevated apps may reject queue attachment. A normal title-bar
                # activation remains available without changing app contents.
                continue
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        except (OSError, pywintypes.error):
            pass
    finally:
        for thread in reversed(joined):
            win32process.AttachThreadInput(current,thread,False)
    if win32gui.GetForegroundWindow() != hwnd:
        click_window_title_bar(hwnd)
    if win32gui.GetForegroundWindow() != hwnd:
        if window_requires_elevation(hwnd):
            raise RuntimeError('应用以管理员权限运行，当前后端权限较低；需以管理员权限运行后端才能控制该窗口')
        raise RuntimeError('无法取得应用窗口焦点，操作已停止')


def window_requires_elevation(hwnd: int) -> bool:
    """Explain a real Windows integrity boundary rather than reporting capture failure."""
    import win32api
    import win32con
    import win32process
    import win32security
    import pywintypes
    try:
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        process = win32api.OpenProcess(0x1000, False, pid)
        try:
            target = win32security.OpenProcessToken(process, win32con.TOKEN_QUERY)
            current = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
            try:
                return bool(win32security.GetTokenInformation(target, win32security.TokenElevation)) and not bool(win32security.GetTokenInformation(current, win32security.TokenElevation))
            finally:
                target.Close(); current.Close()
        finally:
            process.Close()
    except pywintypes.error:
        return False


def _codex_window():
    import psutil
    import win32process
    set_dpi_awareness()
    windows = []
    for window in iter_windows():
        try:
            pid = win32process.GetWindowThreadProcessId(window.hwnd)[1]
            # Codex may be hosted by the unified ChatGPT desktop application.
            if psutil.Process(pid).name().lower() in {'codex.exe', 'chatgpt.exe'}:
                windows.append(window)
        except (psutil.Error, OSError):
            continue
    if not windows:
        raise RuntimeError('未找到可见的 Codex 桌面窗口，请打开窗口并取消最小化')
    return max(windows, key=lambda window: window.area)


def list_projection_windows() -> list[dict]:
    """Opened windows including minimized ones; enumeration never activates apps.

    This owner-facing view lists WeChat too. Agent-driven WeChat work still uses
    the archive API; that automation rule must not hide a human's open windows.
    Window identity includes process creation time, not just a recycled HWND.
    """
    import psutil
    import win32process
    from backend.core.devices.window_icons import window_icon
    set_dpi_awareness()
    rows = []
    for window in iter_windows(include_minimized=True):
        # Desktop/taskbar/input-service surfaces are not application windows.
        if window.class_name.lower() in {'progman', 'workerw', 'shell_traywnd', 'shell_secondarytraywnd'}:
            continue
        try:
            pid = win32process.GetWindowThreadProcessId(window.hwnd)[1]
            app = psutil.Process(pid)
            process = app.name()
            if process.lower() in {'textinputhost.exe', 'codex-computer-use.exe'}:
                continue
            try:
                executable = app.exe()
            except (psutil.Error, OSError):
                executable = ''
            rows.append(dict(id=_window_identity(window.hwnd), title=window.title,
                             application=process, width=window.width, height=window.height,
                             icon=window_icon(window.hwnd, executable)))
        except (psutil.Error, OSError):
            continue
    rows.append(dict(id=FANXIU_ADB_WINDOW_ID, title='凡修模拟器（独立画面）',
                     application='ADB · 只读查看', width=0, height=0, read_only=True,
                     icon=next((row['icon'] for row in rows
                                if row['application'].lower() == 'mumunxmain.exe' and row['icon']),
                               next((row['icon'] for row in rows
                                     if 'mumu' in row['application'].lower() and row['icon']), None))))
    return sorted(rows, key=lambda row: (row['application'].lower(), row['title']))


def selected_window(window_id: str):
    """Resolve a live, visible window and reject replaced or excluded applications."""
    allowed = {row['id'] for row in list_projection_windows()}
    if window_id not in allowed:
        raise ValueError('窗口已关闭或不支持操作，请刷新窗口清单')
    for window in iter_windows(include_minimized=True):
        if str(window.hwnd) == window_id.split(':', 1)[0]:
            return window
    raise ValueError('窗口已关闭，请刷新窗口清单')


def activate_projection_window(window_id: str) -> dict:
    """Explicit user selection restores and foregrounds the chosen application."""
    if window_id == FANXIU_ADB_WINDOW_ID:
        return {'activated': False, 'window_id': window_id}
    with _lock:
        window = selected_window(window_id)
        try:
            focus_projection_window(window.hwnd)
        except RuntimeError as exc:
            if not window_requires_elevation(window.hwnd):
                raise
            require_uncovered_window(window.hwnd)
            return {'activated': False, 'visible': True, 'window_id': _window_identity(window.hwnd), 'notice': str(exc)}
        # WebView/compositor painting follows foreground activation asynchronously.
        time.sleep(.3)
        return {'activated': True, 'window_id': _window_identity(window.hwnd)}


def require_uncovered_window(hwnd: int) -> None:
    """A screen fallback must not return pixels belonging to an overlapping app."""
    import win32gui
    import win32con
    if win32gui.IsIconic(hwnd):
        raise RuntimeError('窗口已最小化，请点击窗口清单恢复')
    left, top, right, bottom = get_capture_rect(hwnd, 'client')
    above = win32gui.GetWindow(hwnd, win32con.GW_HWNDPREV)
    while above:
        # Context menus and dialogs owned by this app are part of its interaction.
        owned = win32gui.GetAncestor(above, win32con.GA_ROOTOWNER) == win32gui.GetAncestor(hwnd, win32con.GA_ROOTOWNER)
        if not owned and win32gui.IsWindowVisible(above) and not win32gui.IsIconic(above):
            x1,y1,x2,y2 = win32gui.GetWindowRect(above)
            if max(left,x1) < min(right,x2) and max(top,y1) < min(bottom,y2):
                raise RuntimeError('此应用需要屏幕采集，但窗口被遮挡；请再次点击窗口清单激活')
        above = win32gui.GetWindow(above, win32con.GW_HWNDPREV)


def capture_projection_window(hwnd: int) -> tuple[np.ndarray, str]:
    """Prefer independent window capture; accelerated WebViews may need screen pixels."""
    import mss
    frame = capture_by_printwindow(hwnd, 'client')
    if frame is not None and frame.size and int(frame[:,:,:3].max()) - int(frame[:,:,:3].min()) >= 3:
        return frame, 'window'
    require_uncovered_window(hwnd)
    with mss.mss() as screen:
        frame = capture_by_screen(screen, hwnd, 'client')
    require_uncovered_window(hwnd)
    if frame is None or not frame.size:
        raise RuntimeError('窗口与屏幕采集均失败，请重新激活窗口')
    return frame, 'screen'


def changed_tiles(frame: np.ndarray, previous: np.ndarray | None) -> list[dict]:
    """Lossless WebP preserves small text; unchanged frames carry no image bytes."""
    height, width = frame.shape[:2]
    full = previous is None or previous.shape != frame.shape
    rectangles = [(0, 0, width, height)] if full else [
        (x, y, min(256, width - x), min(256, height - y))
        for y in range(0, height, 256) for x in range(0, width, 256)
        if not np.array_equal(frame[y:y+256, x:x+256], previous[y:y+256, x:x+256])
    ]
    if len(rectangles) > max(8, (width * height / (256 * 256)) * .6):
        rectangles = [(0, 0, width, height)]
    patches = []
    for x, y, w, h in rectangles:
        image = Image.fromarray(frame[y:y+h, x:x+w, :3][:, :, ::-1])
        out = BytesIO()
        image.save(out, format='WEBP', lossless=True, method=2)
        patches.append(dict(x=x, y=y, width=w, height=h,
                            image='data:image/webp;base64,' + base64.b64encode(out.getvalue()).decode()))
    return patches


def projection_frame(token: str | None = None, window_id: str | None = None) -> dict:
    """Capture a native window or the read-only emulator display via public ADB API.

    The emulator display does not activate Windows or submit game input. Its
    pixels use the same bounded tile cache as native windows.
    """
    with _lock:
        if window_id == FANXIU_ADB_WINDOW_ID:
            from backend.core.fanxiu.client.mumu_control import capture_fresh_mumu_adb_stream_frame
            data, _status = capture_fresh_mumu_adb_stream_frame()
            with Image.open(BytesIO(data)) as image:
                frame = np.asarray(image.convert('RGB'))[:, :, ::-1].copy()
            identity = cache_key = FANXIU_ADB_WINDOW_ID
            title, capture_mode = '凡修模拟器（独立画面）', 'adb'
        else:
            window = selected_window(window_id) if window_id else _codex_window()
            restore_window(window.hwnd)
            frame, capture_mode = capture_projection_window(window.hwnd)
            identity, cache_key, title = _window_identity(window.hwnd), window.hwnd, window.title
        if frame is None or not frame.size:
            raise RuntimeError('应用窗口采集失败，请保持窗口打开；当前采集方式不支持该窗口状态')
        if int(frame[:,:,:3].max()) - int(frame[:,:,:3].min()) < 3:
            raise RuntimeError('应用返回空白画面，请恢复窗口并刷新')
        if frame.shape[0] * frame.shape[1] > 16_000_000:
            raise RuntimeError('窗口尺寸过大，请缩小 应用窗口后重试')
        prior = _frames.get(token or '')
        previous = prior[2] if prior and prior[1] == cache_key and time.monotonic() - prior[0] < 120 else None
        patches = changed_tiles(frame, previous)
        next_token = uuid.uuid4().hex
        _frames[next_token] = (time.monotonic(), cache_key, frame)
        while len(_frames) > 12 or (len(_frames) > 1 and sum(value[2].nbytes for value in _frames.values()) > 64 * 1024 * 1024):
            _frames.popitem(last=False)
        return dict(token=next_token, width=frame.shape[1], height=frame.shape[0],
                    input_token='' if capture_mode == 'adb' else input_token(identity,frame.shape[1],frame.shape[0]),
                    window_id=identity,
                    title=title, capture_mode=capture_mode, patches=patches, full=previous is None,
                    image_bytes=sum(len(p['image'].split(',', 1)[1]) * 3 // 4 for p in patches))


MAX_CLIPBOARD_IMAGE_BYTES = 10 * 1024 * 1024
MAX_CLIPBOARD_IMAGE_PIXELS = 16_000_000


def clipboard_image_bytes(data_url: str) -> bytes:
    """Validate browser image input before touching focus or the native clipboard.

    Decode one raster frame and normalize to PNG. Both encoded size and pixel
    count are bounded; MIME labels alone are never trusted.
    """
    try:
        header, encoded = data_url.split(',', 1)
        if header not in {'data:image/png;base64', 'data:image/jpeg;base64', 'data:image/webp;base64', 'data:image/bmp;base64'}:
            raise ValueError()
        if len(encoded) > (MAX_CLIPBOARD_IMAGE_BYTES + 2) // 3 * 4:
            raise ValueError()
        raw = base64.b64decode(encoded, validate=True)
        return normalize_clipboard_image(raw)
    except (ValueError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('图片无效或过大：支持 PNG/JPEG/WebP/BMP，最多 10 MB、1600 万像素') from exc


def normalize_clipboard_image(raw: bytes, *, native: bool = False) -> bytes:
    """Canonical PNG transport for native PNG/DIB and browser image payloads."""
    # DIB stores uncompressed pixels: a normal 1440p screenshot can exceed
    # 10 MB while its transported PNG is small. Bound the raw native raster
    # independently, then apply the PNG transport limit below.
    input_limit = MAX_CLIPBOARD_IMAGE_PIXELS * 4 + 1024 if native else MAX_CLIPBOARD_IMAGE_BYTES
    if len(raw) > input_limit:
        raise ValueError('剪贴板图片超过 10 MB')
    with Image.open(BytesIO(raw)) as image:
        if image.width * image.height > MAX_CLIPBOARD_IMAGE_PIXELS:
            raise ValueError('剪贴板图片超过 1600 万像素')
        out = BytesIO()
        image.convert('RGBA').save(out, format='PNG')
    if out.tell() > MAX_CLIPBOARD_IMAGE_BYTES:
        raise ValueError('转换后的图片超过 10 MB')
    return out.getvalue()


def write_clipboard_image(png: bytes) -> None:
    """Publish PNG (with alpha) and CF_DIB for Windows paste compatibility."""
    import win32clipboard
    import win32con
    with Image.open(BytesIO(png)) as image:
        bitmap = BytesIO()
        image.convert('RGB').save(bitmap, format='BMP')
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.RegisterClipboardFormat('PNG'), png)
        # CF_DIB is a BMP without its 14-byte file header.
        win32clipboard.SetClipboardData(win32con.CF_DIB, bitmap.getvalue()[14:])
    finally:
        win32clipboard.CloseClipboard()


def projection_clipboard_image() -> dict:
    """Compatibility image-only read; normal remote copy uses the selection API."""
    with _lock:
        try:
            result = read_projection_clipboard()
        except ValueError as exc:
            raise ValueError(f'远端剪贴板中没有图片或图片无法读取：{exc}') from exc
        if 'image' not in result:
            raise ValueError('远端剪贴板中没有图片')
        return result


def read_projection_clipboard() -> dict:
    """Read one copied image or text while the caller holds the projection lock."""
    import win32clipboard
    import win32con
    win32clipboard.OpenClipboard()
    try:
        formats = [win32clipboard.RegisterClipboardFormat('PNG'), win32con.CF_DIBV5, win32con.CF_DIB]
        available = next((fmt for fmt in formats if win32clipboard.IsClipboardFormatAvailable(fmt)), None)
        if available is not None:
            raw = win32clipboard.GetClipboardData(available)
            text = None
        elif win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            raw = None
            text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            if len(text) > 100000:
                raise ValueError('复制的文本超过 10 万字')
        else:
            raise ValueError('应用没有复制可传递的文字或图片')
    finally:
        win32clipboard.CloseClipboard()
    if raw is not None:
        return {'image': 'data:image/png;base64,' + base64.b64encode(normalize_clipboard_image(raw, native=True)).decode()}
    return {'text': text}


def projection_shortcut(hwnd: int, key: int, ctrl: bool = False) -> None:
    """Press and release a complete shortcut, preserving the app's current caret."""
    import win32api
    import win32con
    import win32gui
    if win32gui.GetForegroundWindow() != hwnd:
        raise RuntimeError('窗口焦点改变，键盘操作已停止')
    if ctrl:
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    try:
        win32api.keybd_event(key, 0, 0, 0)
        win32api.keybd_event(key, 0, win32con.KEYEVENTF_KEYUP, 0)
    finally:
        if ctrl:
            win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)


def copy_projection_selection(hwnd: int) -> dict:
    """Copy the existing app selection once; never return the old clipboard.

    Clipboard rendering is asynchronous. Wait for a new sequence while checking
    focus, then read one bounded payload. Copy is never replayed on timeout.
    """
    import win32clipboard
    import win32gui
    import pywintypes
    sequence = win32clipboard.GetClipboardSequenceNumber()
    projection_shortcut(hwnd, ord('C'), ctrl=True)
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        if win32gui.GetForegroundWindow() != hwnd:
            raise RuntimeError('复制时窗口焦点改变，未取回剪贴板')
        if win32clipboard.GetClipboardSequenceNumber() != sequence:
            try:
                return read_projection_clipboard()
            except pywintypes.error:
                # The copying app may still own/render the clipboard.
                pass
        time.sleep(.02)
    raise RuntimeError('应用未更新剪贴板，请先选中要复制的内容')


def type_projection_text(hwnd: int, text: str) -> None:
    """Send committed Unicode at the current caret, preserving the clipboard.

    The local browser owns IME composition. UTF-16 surrogate pairs and key-up
    events are sent together; partial SendInput results are never retried.
    """
    import ctypes
    from ctypes import wintypes
    import win32gui
    class Keyboard(ctypes.Structure):
        _fields_ = [('vk', wintypes.WORD), ('scan', wintypes.WORD), ('flags', wintypes.DWORD),
                    ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
    class Mouse(ctypes.Structure):
        _fields_ = [('x', wintypes.LONG), ('y', wintypes.LONG), ('data', wintypes.DWORD),
                    ('flags', wintypes.DWORD), ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
    class Union(ctypes.Union):
        _fields_ = [('keyboard', Keyboard), ('mouse', Mouse)]
    class Input(ctypes.Structure):
        _fields_ = [('type', wintypes.DWORD), ('union', Union)]
    encoded = text.encode('utf-16-le')
    events = []
    for offset in range(0, len(encoded), 2):
        unit = int.from_bytes(encoded[offset:offset+2], 'little')
        for flags in (0x0004, 0x0004 | 0x0002):  # UNICODE, KEYUP
            events.append(Input(1, Union(keyboard=Keyboard(0, unit, flags, 0, 0))))
    if win32gui.GetForegroundWindow() != hwnd:
        raise RuntimeError('打字时窗口焦点改变，未输入')
    send = ctypes.WinDLL('user32', use_last_error=True).SendInput
    send.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    send.restype = wintypes.UINT
    sent = send(len(events), (Input * len(events))(*events), ctypes.sizeof(Input))
    if sent != len(events):
        raise RuntimeError('打字未完整确认，请核对画面，不要重复输入')


def projection_input(*, token: str, action: str, window_id: str | None = None, x: float = .5, y: float = .5,
                     delta: int = 0, text: str = '',
                     button: str = 'left', path: list[dict] | None = None,
                     duration_ms: int = 600, axis: str = 'vertical', image: str = '', key: str = 'enter') -> dict:
    """Serialize window input and reject stale identity/geometry.

    Browser paste preserves the current caret and never clicks a default point.
    Copy returns only a newly copied selection. Application buttons and sending
    are ordinary user gestures. No mutation is retried after ambiguity.
    """
    if window_id == FANXIU_ADB_WINDOW_ID:
        raise ValueError('凡修独立画面仅供查看，不接受桌面操作')
    if action not in {'click', 'double_click', 'drag', 'scroll', 'type', 'paste', 'copy', 'key', 'undo'}:
        raise ValueError('不支持的远控操作')
    import win32api
    import win32con
    import win32gui
    import win32clipboard
    if action == 'type' and (not text or len(text) > 4096):
        raise ValueError('打字内容必须为 1–4096 个字符')
    png = clipboard_image_bytes(image) if action == 'paste' and image else None
    if action == 'paste' and not (image or text):
        raise ValueError('剪贴板中没有可粘贴的文字或图片')
    keyboard = {'enter': (win32con.VK_RETURN, False), 'ctrl_enter': (win32con.VK_RETURN, True),
                'tab': (win32con.VK_TAB, False), 'escape': (win32con.VK_ESCAPE, False),
                'backspace': (win32con.VK_BACK, False), 'delete': (win32con.VK_DELETE, False),
                'select_all': (ord('A'), True), 'left': (win32con.VK_LEFT, False),
                'right': (win32con.VK_RIGHT, False), 'up': (win32con.VK_UP, False), 'down': (win32con.VK_DOWN, False)}
    if action == 'key' and key not in keyboard:
        raise ValueError('不支持的按键')
    if action == 'drag' and (not path or len(path) < 2):
        raise ValueError('拖拽至少需要起点和终点')
    with _lock:
        window = selected_window(window_id) if window_id else _codex_window()
        rect = get_capture_rect(window.hwnd, 'client')
        width, height = rect[2]-rect[0], rect[3]-rect[1]
        validate_input_token(token,_window_identity(window.hwnd),width,height)
        focus_projection_window(window.hwnd)
        # Foreground activation can restore/reposition the native window. Use
        # its current origin, and reject resizing before any content input.
        rect = get_capture_rect(window.hwnd, 'client')
        width, height = rect[2]-rect[0], rect[3]-rect[1]
        validate_input_token(token,_window_identity(window.hwnd),width,height)
        point = (min(width - 1, int(x * width)), min(height - 1, int(y * height)))
        if action in {'click', 'double_click', 'drag'}:
            pointer_gesture(window.hwnd, rect, action, button, x, y, path or [], duration_ms)
        elif action == 'scroll':
            win32api.SetCursorPos((rect[0] + point[0], rect[1] + point[1]))
            flag = win32con.MOUSEEVENTF_HWHEEL if axis == 'horizontal' else win32con.MOUSEEVENTF_WHEEL
            win32api.mouse_event(flag, 0, 0, delta, 0)
        elif action == 'undo':
            win32api.keybd_event(win32con.VK_CONTROL,0,0,0)
            try:
                win32api.keybd_event(ord('Z'),0,0,0)
                win32api.keybd_event(ord('Z'),0,win32con.KEYEVENTF_KEYUP,0)
            finally:
                win32api.keybd_event(win32con.VK_CONTROL,0,win32con.KEYEVENTF_KEYUP,0)
        elif action == 'copy':
            return {'accepted': True, **copy_projection_selection(window.hwnd)}
        elif action == 'key':
            projection_shortcut(window.hwnd, *keyboard[key])
        elif action == 'type':
            type_projection_text(window.hwnd, text)
        elif action == 'paste':
            if win32gui.GetForegroundWindow() != window.hwnd:
                raise RuntimeError('窗口焦点改变，未粘贴，请核对桌面')
            if png is not None:
                write_clipboard_image(png)
            else:
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                finally:
                    win32clipboard.CloseClipboard()
            projection_shortcut(window.hwnd, ord('V'), ctrl=True)
        return {'accepted': True}


def pointer_gesture(hwnd: int, rect: tuple[int,int,int,int], action: str, button: str,
                    x: float, y: float, path: list[dict], duration_ms: int) -> None:
    """Replay one complete gesture locally; network loss cannot leave a button held.

    Browser gestures are buffered until release. Double-click is two native clicks
    in one request; dragging preserves intermediate points without streaming video.
    """
    import win32api
    import win32con
    import win32gui
    down, up = {
        'left': (win32con.MOUSEEVENTF_LEFTDOWN,win32con.MOUSEEVENTF_LEFTUP),
        'right': (win32con.MOUSEEVENTF_RIGHTDOWN,win32con.MOUSEEVENTF_RIGHTUP),
        'middle': (win32con.MOUSEEVENTF_MIDDLEDOWN,win32con.MOUSEEVENTF_MIDDLEUP),
    }[button]
    left,top,right,bottom = rect
    def move(point):
        if win32gui.GetForegroundWindow() != hwnd:
            raise RuntimeError('手势执行时窗口焦点改变，已释放鼠标并停止')
        win32api.SetCursorPos((left+min(right-left-1,int(point['x']*(right-left))), top+min(bottom-top-1,int(point['y']*(bottom-top)))))
    points = path if action == 'drag' else [{'x':x,'y':y}]
    move(points[0])
    for repeat in range(2 if action == 'double_click' else 1):
        try:
            win32api.mouse_event(down,0,0,0,0)
            if action == 'drag':
                delay = min(2000,max(0,duration_ms)) / 1000 / (len(points)-1)
                for point in points[1:]:
                    time.sleep(delay)
                    move(point)
            else:
                time.sleep(.025)
        finally:
            win32api.mouse_event(up,0,0,0,0)
        if action == 'double_click' and repeat == 0:
            time.sleep(.06)
            move(points[0])
