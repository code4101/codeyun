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
    iter_windows, capture_by_printwindow, click_window_raw_point,
    activate_window, get_capture_rect, set_dpi_awareness, capture_by_screen, click_window_title_bar, restore_window,
)

_lock = threading.Lock()
_frames: OrderedDict[str, tuple[float, int, np.ndarray]] = OrderedDict()
_input_secret = secrets.token_bytes(32)


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

    WeChat must use CodeYun's archive API rather than GUI automation.
    Window identity includes process creation time, not just a recycled HWND.
    """
    import psutil
    import win32process
    set_dpi_awareness()
    rows = []
    for window in iter_windows(include_minimized=True):
        # Desktop/taskbar/input-service surfaces are not application windows.
        if window.class_name.lower() in {'progman', 'workerw', 'shell_traywnd', 'shell_secondarytraywnd'}:
            continue
        try:
            pid = win32process.GetWindowThreadProcessId(window.hwnd)[1]
            process = psutil.Process(pid).name()
            if process.lower() in {'wechat.exe', 'weixin.exe', 'textinputhost.exe', 'codex-computer-use.exe'}:
                continue
            rows.append(dict(id=_window_identity(window.hwnd), title=window.title,
                             application=process, width=window.width, height=window.height))
        except (psutil.Error, OSError):
            continue
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
    """Capture the selected window, restoring it first if minimized."""
    with _lock:
        window = selected_window(window_id) if window_id else _codex_window()
        # A selected application may be minimized again after its initial selection.
        # Restore it here as well, rather than asking the user to reselect the row.
        restore_window(window.hwnd)
        frame, capture_mode = capture_projection_window(window.hwnd)
        if frame is None or not frame.size:
            raise RuntimeError('应用窗口采集失败，请保持窗口打开；当前采集方式不支持该窗口状态')
        if int(frame[:,:,:3].max()) - int(frame[:,:,:3].min()) < 3:
            raise RuntimeError('应用返回空白画面，请恢复窗口并刷新')
        if frame.shape[0] * frame.shape[1] > 16_000_000:
            raise RuntimeError('窗口尺寸过大，请缩小 应用窗口后重试')
        prior = _frames.get(token or '')
        previous = prior[2] if prior and prior[1] == window.hwnd and time.monotonic() - prior[0] < 120 else None
        patches = changed_tiles(frame, previous)
        next_token = uuid.uuid4().hex
        _frames[next_token] = (time.monotonic(), window.hwnd, frame)
        while len(_frames) > 12 or (len(_frames) > 1 and sum(value[2].nbytes for value in _frames.values()) > 64 * 1024 * 1024):
            _frames.popitem(last=False)
        return dict(token=next_token, width=frame.shape[1], height=frame.shape[0],
                    input_token=input_token(_window_identity(window.hwnd),frame.shape[1],frame.shape[0]),
                    window_id=_window_identity(window.hwnd),
                    title=window.title, capture_mode=capture_mode, patches=patches, full=previous is None,
                    image_bytes=sum(len(p['image'].split(',', 1)[1]) * 3 // 4 for p in patches))


def projection_input(*, token: str, action: str, window_id: str | None = None, x: float = .5, y: float = .5,
                     delta: int = 0, text: str = '', send: bool = False, send_key: str = 'enter',
                     button: str = 'left', path: list[dict] | None = None,
                     duration_ms: int = 600, axis: str = 'vertical') -> dict:
    """Serialize GUI input; reject stale geometry and never retry sending text.

    Text is pasted once at a point explicitly selected by the user in the
    projected input box. No application-specific composer coordinates or private APIs.
    """
    import win32api
    import win32con
    import win32gui
    import win32clipboard
    if action == 'text' and not text:
        raise ValueError('文本不能为空')
    if send_key not in {'enter', 'ctrl_enter'}:
        raise ValueError('不支持的发送键')
    if action == 'drag' and (not path or len(path) < 2):
        raise ValueError('拖拽至少需要起点和终点')
    with _lock:
        window = selected_window(window_id) if window_id else _codex_window()
        rect = get_capture_rect(window.hwnd, 'client')
        width, height = rect[2]-rect[0], rect[3]-rect[1]
        validate_input_token(token,_window_identity(window.hwnd),width,height)
        point = (min(width - 1, int(x * width)), min(height - 1, int(y * height)))
        focus_projection_window(window.hwnd)
        if action == 'text':
            click_window_raw_point(window.hwnd, 'client', point)
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
        elif action == 'text':
            if win32gui.GetForegroundWindow() != window.hwnd:
                raise RuntimeError('输入框点击后焦点改变，未粘贴，请核对桌面')
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            finally:
                win32clipboard.CloseClipboard()
            win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
            try:
                win32api.keybd_event(ord('V'), 0, 0, 0)
                win32api.keybd_event(ord('V'), 0, win32con.KEYEVENTF_KEYUP, 0)
            finally:
                win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
            if send:
                time.sleep(.25)
                if win32gui.GetForegroundWindow() != window.hwnd:
                    raise RuntimeError('粘贴后焦点改变，未按发送键，请核对桌面')
                if send_key == 'ctrl_enter':
                    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
                try:
                    win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
                    win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)
                finally:
                    if send_key == 'ctrl_enter':
                        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
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
