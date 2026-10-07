"""Regression contracts: a small deviation must not reflow unrelated windows."""
import pytest
from types import SimpleNamespace
from backend.core.runtime import desktop_layout as layout
from backend.core.runtime.desktop_layout import layout_changes, calculate_work_rects, align_top_right


def test_aligned_layout_has_no_native_actions():
    assert layout_changes({'windows': [{'hwnd': 1, 'verified': True}],
                           'browsers': [{'hwnd': 2, 'maximized': True}],
                           'layer_violations': []}) == {'move': [], 'maximize': [], 'lower': []}


def test_one_displaced_window_does_not_touch_aligned_windows():
    changes = layout_changes({'windows': [{'hwnd': 1, 'verified': True},
                                          {'hwnd': 2, 'verified': False}],
                              'browsers': [], 'layer_violations': []})
    assert changes == {'move': [2], 'maximize': [], 'lower': []}


def test_layer_only_drift_does_not_move_or_raise_work_windows():
    changes = layout_changes({'windows': [{'hwnd': 1, 'verified': True},
                                          {'hwnd': 2, 'verified': True}],
                              'browsers': [{'hwnd': 3, 'maximized': True}],
                              'layer_violations': [{'front': 1, 'back': 3},
                                                   {'front': 2, 'back': 3}]})
    assert changes == {'move': [], 'maximize': [], 'lower': [3]}


def test_only_unmaximized_browser_is_maximized():
    changes = layout_changes({'windows': [],
                              'browsers': [{'hwnd': 1, 'maximized': True},
                                           {'hwnd': 2, 'maximized': False}],
                              'layer_violations': []})
    assert changes == {'move': [], 'maximize': [2], 'lower': []}


@pytest.mark.parametrize('work', [(0, 0, 2048, 1488), (0, 0, 3840, 2488),
                                 (-1920, 48, 0, 1080), (200, -1200, 2121, -121)])
def test_work_partition_tiles_arbitrary_resolution_and_monitor_origin(work):
    rects = calculate_work_rects(work)
    left, top, right, bottom = work
    codex, upper, lower = (rects[k] for k in ['Codex', 'code4101', 'code4102'])
    assert codex[0:2] == (left, top) and codex[3] == bottom
    assert abs((codex[2]-left)*2 - (right-left)) <= 1
    assert upper[0] == lower[0] == codex[2]
    assert upper[2] == lower[2] == right
    assert upper[1] == top and lower[3] == bottom
    assert upper[3] == lower[1]
    assert abs((upper[3]-top)*2 - (bottom-top)) <= 1


@pytest.mark.parametrize('work', [(0, 0, 2048, 1488), (0, 48, 3840, 2488),
                                 (-1920, 0, 0, 1080)])
def test_right_anchor_preserves_app_size_on_every_monitor(work):
    l, t, r, b = align_top_right(work, (736, 1339))
    assert (r-l, b-t) == (736, 1339)
    assert (t, r) == (work[1], work[2])


@pytest.mark.parametrize('maximized,actual,expected', [
    (False, (0, 0, 3840, 2560), True),
    (True, (0, 0, 3840, 2560), False),
    (False, (0, 0, 1920, 2488), False),
])
def test_fullscreen_is_distinct_from_maximized_and_half_screen(monkeypatch, maximized, actual, expected):
    monkeypatch.setattr(layout, 'is_maximized', lambda h: maximized)
    monkeypatch.setattr(layout, 'win32gui', SimpleNamespace(IsIconic=lambda h: False), raising=False)
    monkeypatch.setattr(layout, 'win32api', SimpleNamespace(
        MonitorFromWindow=lambda h, f: 1,
        GetMonitorInfo=lambda h: {'Monitor': (0, 0, 3840, 2560)}), raising=False)
    monkeypatch.setattr(layout, 'get_extended_frame_bounds', lambda h: actual, raising=False)
    assert layout.is_app_fullscreen(1) is expected


@pytest.mark.parametrize('states,expected', [([False], True), ([True, False], True), ([True] * 11, False)])
def test_fullscreen_exit_sends_one_shortcut_and_never_toggles_again(monkeypatch, states, expected):
    observations = iter(states)
    messages, gates = [], []
    monkeypatch.setattr(layout, 'is_app_fullscreen', lambda h: next(observations))
    monkeypatch.setattr(layout, 'win32gui', SimpleNamespace(PostMessage=lambda *args: messages.append(args)), raising=False)
    monkeypatch.setattr(layout, 'win32con', SimpleNamespace(WM_KEYDOWN=256, WM_KEYUP=257, VK_F11=122), raising=False)
    monkeypatch.setattr(layout.time, 'sleep', lambda seconds: None)
    assert layout.exit_codex_fullscreen(1, lambda: gates.append(True)) is expected
    assert len(messages) == (0 if states == [False] else 2)
    assert len(gates) == len(messages)
