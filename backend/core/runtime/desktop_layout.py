"""Arrange Codex, two account-identified Weixin windows and MuMu on Windows.

Run ``uv run python scripts/desktop_layout.py`` to inspect the plan, or add
``--apply`` to execute it. TIM stays at the top right behind the work windows;
browsers are maximized underneath it.
Weixin fills the right half in two rows. MuMu keeps its configured default size
and overlaps those rows at the top right instead of sharing a tiled column.
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
ACCOUNT_IDS = {'code4101': 'wxid_m1cd4f5aahut22', 'code4102': 'wxid_gxgjjgft1oj722'}


def _load_native():
    global win32api, win32con, win32gui, WindowWalker, get_extended_frame_bounds, list_live_accounts
    ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    import win32api
    import win32con
    import win32gui
    from pyxllib.autogui.window import WindowWalker, get_extended_frame_bounds
    from pyxllib.autogui.weixin4_instrumentation import list_live_accounts


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


def plan_layout():
    all_windows = list(WindowWalker(select=True).iter())
    windows = [w for w in all_windows if w.is_visible and w.title]
    skipped = []
    codex = optional([w for w in windows if w.process_name.lower() in
                    ('chatgpt.exe', 'codex.exe')], 'Codex', skipped)
    emulator = optional([w for w in windows if w.process_name.lower() == 'mumunxdevice.exe'
                       and w.class_name.endswith('WindowIcon')], 'MuMu', skipped)
    accounts = list_live_accounts()
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
    split = left + width // 2
    mid_y = top + height // 2
    for label, w in wechat.items():
        if minimum_size(w.hwnd)[0] > right - split:
            raise RuntimeError(f'{label} cannot fit horizontally in half the work area')
        if minimum_size(w.hwnd)[1] > height // 2:
            raise RuntimeError(f'{label} cannot fit vertically in half the work area')
    items = []
    if codex:
        items.append(('Codex', codex, (left, top, split, bottom)))
    for label, rect in [('code4101', (split, top, right, mid_y)),
                        ('code4102', (split, mid_y, right, bottom))]:
        if label in wechat:
            items.append((label, wechat[label], rect))
    if emulator:
        from backend.core.fanxiu.client.mumu_control import normalize_mumu_desktop_window_size
        default_width, default_height = normalize_mumu_desktop_window_size(apply=False)['target_main_size']
        current = get_extended_frame_bounds(emulator.hwnd)
        outer = win32gui.GetWindowRect(emulator.hwnd)
        # The Fanxiu API gives native outer size; align visible DWM edges.
        ew = default_width - ((outer[2] - outer[0]) - (current[2] - current[0]))
        eh = default_height - ((outer[3] - outer[1]) - (current[3] - current[1]))
        items.append(('MuMu', emulator, (right - ew, top, right, top + eh)))
    browsers = [w for w in windows if w.process_name.lower() in
                ('chrome.exe', 'msedge.exe', 'firefox.exe', 'brave.exe', 'vivaldi.exe', 'opera.exe')
                and win32gui.GetWindow(w.hwnd, win32con.GW_OWNER) == 0]
    background = []
    tim_candidates = [w for w in all_windows if w.process_name.lower() == 'tim.exe'
                      and w.title == 'TIM'
                      and win32gui.GetWindow(w.hwnd, win32con.GW_OWNER) == 0]
    if tim_candidates:
        tim = unique(tim_candidates, 'TIM')
        rect = (win32gui.GetWindowPlacement(tim.hwnd)[4]
                if win32gui.IsIconic(tim.hwnd) or is_maximized(tim.hwnd)
                else get_extended_frame_bounds(tim.hwnd))
        tw = min(right - split, rect[2] - rect[0])
        th = min(height, rect[3] - rect[1])
        background.append(('TIM', tim, (right - tw, top, right, top + th)))
    return monitor, items, browsers, background, skipped


def lease_cli(action, token=None):
    from backend.core.settings import ROOT_DIR
    from backend.core.services.launcher import run_quiet
    command = ['uv', 'run', 'python', 'scripts/resource_lease.py', action]
    command += ['--token', token] if token else ['--owner', 'desktop-layout-python']
    completed = run_quiet(command, cwd=ROOT_DIR,
                          capture_output=True, text=True, check=True, timeout=15)
    return json.loads(completed.stdout)


def set_visible_rect(hwnd, target, token):
    """Align visible DWM edges, compensating for Windows' invisible borders."""
    if win32gui.IsIconic(hwnd) or is_maximized(hwnd):
        lease_cli('check', token)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(.15)
    elif not win32gui.IsWindowVisible(hwnd):
        lease_cli('check', token)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
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
        lease_cli('check', token)
        win32gui.SetWindowPos(hwnd, 0, x, y, width, height,
                              win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
        time.sleep(.2)


def inspect_layout(items, browsers, background):
    """Read geometry, window state and relative layers without activating apps."""
    rows = []
    for label, w, rect in items + background:
        actual = get_extended_frame_bounds(w.hwnd)
        rows.append({'label': label, 'hwnd': w.hwnd, 'target': rect, 'actual': actual,
                     'verified': (not win32gui.IsIconic(w.hwnd)
                                  and win32gui.IsWindowVisible(w.hwnd)
                                  and max(abs(a-b) for a,b in zip(actual, rect)) <= 1)})
    z_order = [w.hwnd for w in WindowWalker(select=True).iter()]
    pairs = [(front.hwnd, w.hwnd) for _, front, _ in items for _, w, _ in background]
    pairs += [(front.hwnd, w.hwnd) for _, front, _ in items + background for w in browsers]
    # Overlapping work windows may be raised by the user; only enforce the
    # background boundary, so periodic checks do not fight the active app.
    layers_ok = all(z_order.index(front) < z_order.index(back) for front, back in pairs)
    maximized = all(is_maximized(w.hwnd) for w in browsers)
    return {'windows': rows, 'layers_verified': layers_ok,
            'browsers_maximized': maximized,
            'aligned': all(row['verified'] for row in rows) and layers_ok and maximized}


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
    _load_native()
    if not desktop_available():
        return {'status': 'skipped', 'reason': 'desktop_locked', 'applied': False}
    if cancel_check:
        cancel_check()
    monitor, items, browsers, background, skipped = plan_layout()
    result = {'work_area': monitor['Work'], 'missing_apps': skipped,
              'browser_windows': [w.hwnd for w in browsers], 'applied': False,
              **inspect_layout(items, browsers, background)}
    result['status'] = 'aligned' if result['aligned'] else 'drifted'
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
    try:
        if cancel_check:
            cancel_check()
        # Reobserve after obtaining ownership; stale handles/geometry are not inputs.
        monitor, items, browsers, background, skipped = plan_layout()
        result.update(work_area=monitor['Work'], missing_apps=skipped)
        for _, w, rect in background:
            set_visible_rect(w.hwnd, rect, token)
            lease_cli('check', token)
            win32gui.SetWindowPos(w.hwnd, win32con.HWND_BOTTOM, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
        for w in browsers:
            if not is_maximized(w.hwnd):
                lease_cli('check', token)
                win32gui.ShowWindow(w.hwnd, win32con.SW_MAXIMIZE)
            lease_cli('check', token)
            win32gui.SetWindowPos(w.hwnd, win32con.HWND_BOTTOM, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
        for _, w, rect in items:
            if cancel_check:
                cancel_check()
            set_visible_rect(w.hwnd, rect, token)
            lease_cli('check', token)
            win32gui.SetWindowPos(w.hwnd, win32con.HWND_TOP, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
        time.sleep(1)
        result.update(inspect_layout(items, browsers, background), applied=True)
        result['status'] = 'aligned' if result['aligned'] else 'failed'
        if not result['aligned']:
            raise RuntimeError('Desktop layout verification failed: '+json.dumps(result))
        return result
    finally:
        if own_token:
            lease_cli('release', token)
