"""Arrange Codex, OpenCode, Tabby, account-identified Weixin windows and MuMu.

Run ``uv run python scripts/desktop_layout.py`` to inspect the plan, or add
``--apply`` to execute it. TIM stays at the top right behind the work windows;
browsers are maximized underneath it.
On a single-account device Weixin fills the whole right half; OpenCode and Tabby
share that exact rectangle and keep their pre-existing stacking order, so the
right-half work windows are never raised against each other. MuMu keeps its own
current size and overlaps the right half at the top right instead of sharing a
tiled column.
Only desktop geometry changes; no app content or Android settings are touched.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import subprocess
import time

DESKTOP_LAYOUT_TASK_KEY = 'desktop_layout_alignment'
DESKTOP_LAYOUT_JOB_TYPE = 'desktop.layout-alignment'
# 当前设备只保留 code4102；右半屏作为单一微信工作区。
ACCOUNT_IDS = {'code4102': 'wxid_gxgjjgft1oj722'}


def _load_native():
    global win32api, win32con, win32gui, WindowWalker, get_extended_frame_bounds, list_live_accounts
    import win32api
    import win32con
    import win32gui
    from pyxllib.autogui.window import WindowWalker, get_extended_frame_bounds
    from pyxllib.autogui.weixin4_instrumentation import list_live_accounts
    # Imports may establish process DPI policy. Establish and validate the
    # thread's physical coordinate context *after* all native dependencies load.
    user32 = ctypes.windll.user32
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    if user32.GetAwarenessFromDpiAwarenessContext(user32.GetThreadDpiAwarenessContext()) != 2:
        raise RuntimeError('Desktop layout requires physical per-monitor DPI coordinates')


def optional(items, label, skipped):
    if not items:
        skipped.append(label)
        return None
    return unique(items, label)


def unique(items, label):
    if len(items) != 1:
        raise RuntimeError(f'{label}: expected one window, found {len(items)}')
    return items[0]


def minimum_size(hwnd):
    """Ask the app for its native minimum tracking dimensions."""
    class MinMax(ctypes.Structure):
        _fields_ = [(name, wintypes.POINT) for name in
                    ('reserved', 'maxsize', 'maxpos', 'mintrack', 'maxtrack')]
    info = MinMax()
    win32gui.SendMessage(hwnd, win32con.WM_GETMINMAXINFO, 0, ctypes.addressof(info))
    return info.mintrack.x, info.mintrack.y


def is_maximized(hwnd):
    return win32gui.GetWindowPlacement(hwnd)[1] == win32con.SW_SHOWMAXIMIZED


def is_app_fullscreen(hwnd):
    """Application fullscreen covers the monitor, rather than its work area.

    Electron can report a normal show state while enforcing fullscreen bounds;
    ordinary SetWindowPos calls then succeed but are immediately undone.
    """
    if is_maximized(hwnd) or win32gui.IsIconic(hwnd):
        return False
    monitor = win32api.GetMonitorInfo(win32api.MonitorFromWindow(hwnd, 1))['Monitor']
    actual = get_extended_frame_bounds(hwnd)
    return max(abs(a - b) for a, b in zip(actual, monitor)) <= 1


def exit_codex_fullscreen(hwnd, gate):
    """Send Codex's F11 once to its own window; never change desktop focus.

    Verify the application actually leaves fullscreen before changing geometry.
    This adapter is specific to Codex; do not guess other apps' shortcuts.
    """
    if not is_app_fullscreen(hwnd):
        return True
    gate()
    win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_F11, 0x00570001)
    gate()
    win32gui.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_F11, 0xC0570001)
    for _ in range(10):
        time.sleep(.2)
        if not is_app_fullscreen(hwnd):
            return True
    return False


def calculate_work_rects(work_area):
    """Split the current physical work area; absolute resolution is not a preset."""
    left, top, right, bottom = work_area
    split = left + (right - left) // 2
    middle = top + (bottom - top) // 2
    return {'Codex': (left, top, split, bottom),
            'code4101': (split, top, right, middle),
            'code4102': (split, middle, right, bottom)}


def right_half(work_area):
    """The full right half shared by the single Weixin work area and OpenCode."""
    left, top, right, bottom = work_area
    return (left + (right - left) // 2, top, right, bottom)


def align_top_right(work_area, visible_size):
    """Keep the app's width/height and anchor to its monitor's work-area edges."""
    _, top, right, _ = work_area
    width, height = visible_size
    return (right - width, top, right, top + height)


def calculate_wechat_rects(work_area, minimums):
    """Prefer equal rows, adjusting their boundary to native minimum sizes.

    If both rows cannot fit, preserve minimum sizes and anchor them to the
    top/bottom with overlap. A smaller desktop must not abort other apps' layout.
    Minimums describe visible dimensions, excluding invisible DWM borders.
    """
    left, top, right, bottom = work_area
    width, height = right - left, bottom - top
    upper = minimums.get('code4101', (0, 0))
    lower = minimums.get('code4102', (0, 0))
    column_width = max(width - width // 2, upper[0], lower[0])
    if upper[1] + lower[1] <= height:
        boundary = top + max(upper[1], min(height // 2, height - lower[1]))
        upper_bottom, lower_top = boundary, boundary
    else:
        upper_bottom = top + upper[1]
        lower_top = bottom - lower[1]
    return {'code4101': (right - column_width, top, right, upper_bottom),
            'code4102': (right - column_width, lower_top, right, bottom)}


def plan_layout():
    all_windows = list(WindowWalker(select=True).iter())
    # Minimized/hidden windows are intentional user state, not layout drift.
    windows = [w for w in all_windows if w.is_visible and w.title
               and not win32gui.IsIconic(w.hwnd)]
    skipped = []
    codex = optional([w for w in windows if w.process_name.lower() in
                    ('chatgpt.exe', 'codex.exe')], 'Codex', skipped)
    opencode = optional([w for w in windows if w.process_name.lower() == 'opencode.exe'],
                        'OpenCode', skipped)
    tabby = optional([w for w in windows if w.process_name.lower() == 'tabby.exe'],
                     'Tabby', skipped)
    emulator = optional([w for w in windows if w.process_name.lower() == 'mumunxdevice.exe'
                       and w.class_name.endswith('WindowIcon')], 'MuMu', skipped)
    # 微信账户识别依赖进程句柄枚举；Windows 句柄表瞬时异常时，仍应
    # 继续整理其余可安全识别的窗口，而不是让整次桌面排版失败。
    try:
        accounts = list_live_accounts()
    except (OSError, RuntimeError):
        accounts = []
        skipped.append('微信账户识别失败')
    wechat = {}
    for label, account_id in ACCOUNT_IDS.items():
        account = optional([a for a in accounts if a['account_id'] == account_id], label, skipped)
        if account:
            window = optional([w for w in windows if w.pid == account['pid']
                               and w.class_name.endswith('WindowIcon')], label, skipped)
            if window:
                wechat[label] = window
    monitor = win32api.GetMonitorInfo(win32api.MonitorFromWindow(
        codex.hwnd if codex else win32gui.GetForegroundWindow(), 1))
    left, top, right, bottom = monitor['Work']
    width, height = right - left, bottom - top
    work_rects = calculate_work_rects(monitor['Work'])
    minimums = {}
    for label, w in wechat.items():
        min_width, min_height = minimum_size(w.hwnd)
        outer = win32gui.GetWindowRect(w.hwnd)
        visible = get_extended_frame_bounds(w.hwnd)
        minimums[label] = (max(0, min_width - ((outer[2]-outer[0])-(visible[2]-visible[0]))),
                           max(0, min_height - ((outer[3]-outer[1])-(visible[3]-visible[1]))))
    work_rects.update(calculate_wechat_rects(monitor['Work'], minimums))
    split = left + (right - left) // 2
    if set(wechat) == {'code4102'}:
        # 单账号设备：整个右半屏归 code4102，不再保留空的上半区。
        work_rects['code4102'] = (split, top, right, bottom)
    items = []
    if codex:
        items.append(('Codex', codex, work_rects['Codex']))
    for label in ['code4102']:
        if label in wechat:
            items.append((label, wechat[label], work_rects[label]))
    if opencode:
        # OpenCode shares Weixin's right half; the two keep their existing
        # stacking because only item/background and item/browser pairs are
        # checked, never item-to-item order.
        items.append(('OpenCode', opencode, right_half(monitor['Work'])))
    if tabby:
        # Tabby joins the same right-half work stack as OpenCode: it overlaps
        # Weixin and MuMu freely and keeps whatever stacking the user set.
        items.append(('Tabby', tabby, right_half(monitor['Work'])))
    if emulator:
        current = get_extended_frame_bounds(emulator.hwnd)
        rect = align_top_right(monitor['Work'], (current[2] - current[0], current[3] - current[1]))
        items.append(('MuMu', emulator, rect))
    browsers = [w for w in windows if w.process_name.lower() in
                ('chrome.exe', 'msedge.exe', 'firefox.exe', 'brave.exe', 'vivaldi.exe', 'opera.exe')
                and win32gui.GetWindow(w.hwnd, win32con.GW_OWNER) == 0]
    background = []
    tim_candidates = [w for w in windows if w.process_name.lower() == 'tim.exe'
                      and w.title == 'TIM'
                      and win32gui.GetWindow(w.hwnd, win32con.GW_OWNER) == 0]
    if tim_candidates:
        tim = unique(tim_candidates, 'TIM')
        rect = (win32gui.GetWindowPlacement(tim.hwnd)[4]
                if win32gui.IsIconic(tim.hwnd) or is_maximized(tim.hwnd)
                else get_extended_frame_bounds(tim.hwnd))
        background.append(('TIM', tim, align_top_right(monitor['Work'],
                           (rect[2] - rect[0], rect[3] - rect[1]))))
    return monitor, items, browsers, background, skipped


def lease_cli(action, token=None):
    from backend.core.settings import ROOT_DIR
    from backend.core.services.launcher import run_quiet
    command = ['uv', 'run', 'python', 'scripts/resource_lease.py', action]
    command += ['--token', token] if token else ['--owner', 'desktop-layout-python']
    completed = run_quiet(command, cwd=ROOT_DIR,
                          capture_output=True, text=True, check=True, timeout=15)
    return json.loads(completed.stdout)


def set_visible_rect(hwnd, target, token, gate=None):
    """Align visible DWM edges, compensating for Windows' invisible borders."""
    if win32gui.IsIconic(hwnd) or not win32gui.IsWindowVisible(hwnd):
        return False  # The user can minimize a window between planning and mutation.
    changed = False
    if is_maximized(hwnd):
        gate() if gate else lease_cli('check', token)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
        changed = True
        time.sleep(.15)
    for _ in range(3):
        outer = win32gui.GetWindowRect(hwnd)
        visible = get_extended_frame_bounds(hwnd)
        if max(abs(a - b) for a, b in zip(visible, target)) <= 1:
            break
        x = target[0] - (visible[0] - outer[0])
        y = target[1] - (visible[1] - outer[1])
        width = target[2] - target[0] + (outer[2] - outer[0]) - (visible[2] - visible[0])
        height = target[3] - target[1] + (outer[3] - outer[1]) - (visible[3] - visible[1])
        gate() if gate else lease_cli('check', token)
        win32gui.SetWindowPos(hwnd, 0, x, y, width, height,
                              win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
        changed = True
        time.sleep(.2)
    return changed


def inspect_layout(items, browsers, background):
    """Read geometry, window state and relative layers without activating apps."""
    rows = []
    for label, w, rect in items + background:
        actual = get_extended_frame_bounds(w.hwnd)
        rows.append({'label': label, 'hwnd': w.hwnd, 'target': rect, 'actual': actual,
                     'fullscreen': is_app_fullscreen(w.hwnd),
                     'verified': (not win32gui.IsIconic(w.hwnd)
                                  and win32gui.IsWindowVisible(w.hwnd)
                                  and max(abs(a-b) for a,b in zip(actual, rect)) <= 1)})
    z_order = [w.hwnd for w in WindowWalker(select=True).iter()]
    pairs = [(front.hwnd, w.hwnd) for _, front, _ in items for _, w, _ in background]
    pairs += [(front.hwnd, w.hwnd) for _, front, _ in items + background for w in browsers]
    # Overlapping work windows may be raised by the user; only enforce the
    # background boundary, so periodic checks do not fight the active app.
    layers_ok = all(z_order.index(front) < z_order.index(back) for front, back in pairs)
    browser_rows = [{'hwnd': w.hwnd, 'maximized': bool(is_maximized(w.hwnd))} for w in browsers]
    maximized = all(w['maximized'] for w in browser_rows)
    return {'windows': rows, 'layers_verified': layers_ok,
            'browsers': browser_rows,
            'layer_violations': [{'front': front, 'back': back} for front, back in pairs
                                 if z_order.index(front) > z_order.index(back)],
            'work_window_order': [h for h in z_order if h in {w.hwnd for _, w, _ in items}],
            'browsers_maximized': maximized,
            'aligned': all(row['verified'] for row in rows) and layers_ok and maximized}


def layout_changes(observation):
    """Translate observed drift into minimal independent native operations.

    Layer drift only lowers background windows. Work windows are never raised;
    their mutual order belongs to the user. Aligned geometry is never rewritten.
    """
    return {
        'move': [w['hwnd'] for w in observation['windows'] if not w['verified']],
        'maximize': [w['hwnd'] for w in observation['browsers'] if not w['maximized']],
        'lower': list(dict.fromkeys(p['back'] for p in observation['layer_violations'])),
    }


def display_snapshot():
    """Physical monitor topology used as a fence against connection/DPI changes."""
    return [win32api.GetMonitorInfo(m[0]) for m in win32api.EnumDisplayMonitors()]


def desktop_available():
    """A locked or secure desktop must not receive automatic window changes."""
    user32 = ctypes.windll.user32
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    handle = user32.OpenInputDesktop(0, False, 0x100)
    if not handle:
        return False
    user32.CloseDesktop(handle)
    return True


def run_desktop_layout(*, apply=False, token=None, cancel_check=None):
    """Inspect the established layout and correct drift under a desktop lease.

    Missing apps are skipped, never launched. An aligned desktop is a no-op.
    Busy/locked desktops return a skipped result. Each mutation checks the
    same CLI lease gate as interactive work; results are suitable for LocalJobRun.
    """
    if os.name != 'nt':
        return {'status': 'skipped', 'reason': 'windows_only', 'applied': False}
    user32 = ctypes.windll.user32
    user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    previous = user32.GetThreadDpiAwarenessContext()
    try:
        return _run_desktop_layout(apply=apply, token=token, cancel_check=cancel_check)
    finally:
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext(previous)


def _run_desktop_layout(*, apply=False, token=None, cancel_check=None):
    _load_native()
    if not desktop_available():
        return {'status': 'skipped', 'reason': 'desktop_locked', 'applied': False}
    if cancel_check:
        cancel_check()
    topology = display_snapshot()
    monitor, items, browsers, background, skipped = plan_layout()
    result = {'work_area': monitor['Work'], 'display': topology,
              'missing_apps': skipped, 'applied': False,
              **inspect_layout(items, browsers, background)}
    result['status'] = 'aligned' if result['aligned'] else 'drifted'
    result['changes'] = layout_changes(result)
    result['before'] = {key: result[key] for key in
                        ('windows', 'browsers', 'layer_violations', 'work_window_order')}
    result['foreground_before'] = win32gui.GetForegroundWindow()
    if not apply or result['aligned']:
        return result
    own_token = not token
    if own_token:
        try:
            token = lease_cli('acquire')['token']
        except subprocess.CalledProcessError as exc:
            if 'resource_busy' in (exc.stdout or ''):
                return {**result, 'status': 'skipped', 'reason': 'desktop_busy'}
            raise
    performed = []
    try:
        if cancel_check:
            cancel_check()
        # Do not align to a transient desktop during RDP/monitor reconfiguration.
        time.sleep(.5)
        if display_snapshot() != topology:
            return {**result, 'status': 'skipped', 'reason': 'display_changed'}
        monitor, items, browsers, background, skipped = plan_layout()
        before = inspect_layout(items, browsers, background)
        changes = layout_changes(before)
        result.update(work_area=monitor['Work'], missing_apps=skipped,
                      before=before, changes=changes)
        if before['aligned']:
            return {**result, **before, 'status': 'aligned'}

        def gate():
            if cancel_check:
                cancel_check()
            if not desktop_available() or display_snapshot() != topology:
                raise InterruptedError('Desktop display changed during layout alignment')
            lease_cli('check', token)

        for label, w, rect in items + background:
            if w.hwnd not in changes['move']:
                continue
            gate()
            # A minimized app must stay minimized even if it changed after inspection.
            if win32gui.IsIconic(w.hwnd) or not win32gui.IsWindowVisible(w.hwnd):
                continue
            if label == 'Codex' and is_app_fullscreen(w.hwnd):
                exited = exit_codex_fullscreen(w.hwnd, gate)
                performed.append({'type': 'exit_fullscreen', 'label': label,
                                  'hwnd': w.hwnd, 'verified': exited})
                if not exited:
                    return {**result, 'status': 'skipped', 'reason': 'app_fullscreen',
                            'applied': True, 'performed': performed}
            if set_visible_rect(w.hwnd, rect, token, gate=gate):
                performed.append({'type': 'move', 'label': label, 'hwnd': w.hwnd, 'target': rect})
        for hwnd in changes['maximize']:
            gate()
            if win32gui.IsIconic(hwnd) or not win32gui.IsWindowVisible(hwnd):
                continue
            # SetWindowPlacement changes show state without a keyboard/focus action.
            flags, _, minpos, maxpos, normal = win32gui.GetWindowPlacement(hwnd)
            win32gui.SetWindowPlacement(hwnd, (flags, win32con.SW_SHOWMAXIMIZED, minpos, maxpos, normal))
            performed.append({'type': 'maximize', 'hwnd': hwnd})
        # Reobserve layer drift: geometry/maximization can alter native layering.
        # Lowering TIM can put it below an already-correct browser. Reobserve
        # once more to keep browsers behind TIM as well as the work windows.
        for _ in range(2):
            lower = layout_changes(inspect_layout(items, browsers, background))['lower']
            for hwnd in lower:
                gate()
                win32gui.SetWindowPos(hwnd, win32con.HWND_BOTTOM, 0, 0, 0, 0,
                                      win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
                performed.append({'type': 'lower', 'hwnd': hwnd})
        time.sleep(.5)
        if display_snapshot() != topology:
            raise InterruptedError('Desktop display changed after layout alignment')
        result.update(inspect_layout(items, browsers, background), applied=bool(performed),
                      performed=performed, foreground_after=win32gui.GetForegroundWindow())
        result['status'] = 'aligned' if result['aligned'] else 'failed'
        if not result['aligned']:
            raise RuntimeError('Desktop layout verification failed: '+json.dumps(result))
        return result
    except InterruptedError:
        return {**result, 'status': 'skipped', 'reason': 'display_changed',
                'applied': bool(performed), 'performed': performed}
    finally:
        if own_token:
            lease_cli('release', token)
