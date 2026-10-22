"""Pointer target fences and window geometry/fit (deterministic native seams; no desktop).

The seams model only Win32 facts: display rects, where SetCursorPos really lands (clamped to the
displays like Windows), which root window a point hits, and SetWindowPos. The tests check
coordinate math, fence ordering and that a refused target never produces a button or wheel event.
"""

from types import SimpleNamespace

import pytest
import win32con

from backend.core.devices import window_projection as projection

HWND, WID = 7, "7:70:1.5"
SINGLE = [((0, 0, 1920, 1200), (0, 0, 1920, 1152))]
# Left display has negative coordinates; a 100 px gap separates the right display.
MULTI = [((-1280, -200, 0, 824), (-1280, -200, 0, 784)), ((0, 0, 1920, 1080), (0, 0, 1920, 1040)),
         ((2020, 0, 3940, 1080), (2020, 0, 3940, 1040))]


class Desk:
    def __init__(self, monitors=SINGLE, client=(805, 162, 2018, 1026), outer=None):
        self.monitors, self.client = monitors, client
        self.outer = outer or (client[0] - 8, client[1] - 31, client[2] + 8, client[3] + 8)
        self.cursor, self.log, self.roots = (0, 0), [], {}
        self.foreground, self.visible, self.minimized, self.owner = HWND, True, False, 0
        self.identities, self.focus_moves = None, None

    def set_cursor(self, point):  # Windows clamps to the union of displays
        x, y = point
        self.cursor = min(((min(max(x, l), r - 1), min(max(y, t), b - 1)) for (l, t, r, b), _ in self.monitors),
                          key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)
        self.log.append(("cursor", self.cursor))

    def move_by(self, hwnd, dx, dy):
        self.log.append(("move", dx, dy))
        c, o = self.client, self.outer
        self.client = (c[0] + dx, c[1] + dy, c[2] + dx, c[3] + dy)
        self.outer = (o[0] + dx, o[1] + dy, o[2] + dx, o[3] + dy)

    def focus(self, hwnd):
        self.log.append(("focus",))
        if self.focus_moves:
            self.move_by(hwnd, *self.focus_moves)

    @property
    def events(self):
        return [entry for entry in self.log if entry[0] in {"mouse", "focus", "move"}]


@pytest.fixture()
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(projection, "selected_window", lambda wid: SimpleNamespace(hwnd=HWND))
    monkeypatch.setattr(projection, "_window_identity",
                        lambda hwnd: d.identities.pop(0) if d.identities else (WID if hwnd == HWND else f"{hwnd}:1:1"))
    monkeypatch.setattr(projection, "get_capture_rect", lambda hwnd, area: d.client)
    monkeypatch.setattr(projection, "_client_rect", lambda hwnd: d.client)
    monkeypatch.setattr(projection, "_outer_rect", lambda hwnd: d.outer)
    monkeypatch.setattr(projection, "_monitors", lambda: d.monitors)
    monkeypatch.setattr(projection, "_foreground", lambda: d.foreground)
    monkeypatch.setattr(projection, "_set_cursor", d.set_cursor)
    monkeypatch.setattr(projection, "_cursor_pos", lambda: d.cursor)
    monkeypatch.setattr(projection, "_root_window_at", lambda point: d.roots.get(point, HWND))
    monkeypatch.setattr(projection, "_mouse_event", lambda flag, data=0: d.log.append(("mouse", flag, data)))
    monkeypatch.setattr(projection, "_window_flags", lambda hwnd: (d.visible, d.minimized))
    monkeypatch.setattr(projection, "_window_owner", lambda hwnd: d.owner)
    monkeypatch.setattr(projection, "_move_window_by", d.move_by)
    monkeypatch.setattr(projection, "focus_projection_window", d.focus)
    monkeypatch.setattr(projection.time, "sleep", lambda s: None)
    return d


def send(d, action, **kw):
    w, h = d.client[2] - d.client[0], d.client[3] - d.client[1]
    return projection.projection_input(token=projection.input_token(WID, w, h), action=action, window_id=WID, **kw)


# -- pointer fences -------------------------------------------------------------------


def test_real_offscreen_asset_menu_is_refused_before_focus(desk):
    """Sanitised incident: client 1213x864 at (805,162); menu bbox centre (1180,65) -> screen (1985,227)."""
    with pytest.raises(ValueError, match=r"\(1985, 227\).*不在任何显示器内"):
        send(desk, "click", x=1180 / 1213, y=65 / 864)
    assert desk.log == []  # no focus (its fallback clicks the title bar), cursor or button


@pytest.mark.parametrize("action,kw", [
    ("click", {}), ("double_click", {}), ("scroll", {"delta": -120}), ("move", {}),
    ("drag", {"path": [{"x": .1, "y": .1}, {"x": 1180 / 1213, "y": 65 / 864}]}),
])
def test_every_pointer_action_refuses_offscreen_points(desk, action, kw):
    kw.setdefault("x", 1180 / 1213)
    kw.setdefault("y", 65 / 864)
    with pytest.raises(ValueError, match="不在任何显示器内"):
        send(desk, action, **kw)
    assert desk.log == []


def test_onscreen_click_places_then_presses(desk):
    send(desk, "click", x=.5, y=.5)
    assert desk.log == [("focus",), ("cursor", (805 + 606, 162 + 432)),
                        ("mouse", win32con.MOUSEEVENTF_LEFTDOWN, 0), ("mouse", win32con.MOUSEEVENTF_LEFTUP, 0)]


def test_cursor_clamp_mismatch_refuses_without_button(desk, monkeypatch):
    """Displays changed after the pre-focus check: Windows clamps, the readback refuses."""
    monkeypatch.setattr(projection, "_monitors", lambda: [((0, 0, 3000, 1200), (0, 0, 3000, 1152))])  # stale view
    with pytest.raises(RuntimeError, match=r"鼠标未到达目标点 \(1985, 227\)（实际 \(1919, 227\)）"):
        send(desk, "click", x=1180 / 1213, y=65 / 864)  # desk.monitors (the real single display) clamps
    assert desk.log == [("focus",), ("cursor", (1919, 227))]


@pytest.mark.parametrize("action", ["click", "double_click", "scroll", "drag"])
def test_cursor_mismatch_never_sends_down_or_wheel(desk, monkeypatch, action):
    monkeypatch.setattr(projection, "_set_cursor", lambda point: desk.log.append(("cursor", "lost")))
    with pytest.raises(RuntimeError, match="鼠标未到达"):
        send(desk, action, path=[{"x": .1, "y": .1}, {"x": .2, "y": .2}])
    assert not [e for e in desk.log if e[0] == "mouse"]


def test_child_hit_resolving_to_the_selected_root_is_accepted(desk):
    desk.roots[(1411, 594)] = HWND  # WindowFromPoint returned a child; its GA_ROOT is the window
    send(desk, "move", x=.5, y=.5)
    assert desk.cursor == (1411, 594) and not [e for e in desk.log if e[0] == "mouse"]


@pytest.mark.parametrize("action", ["click", "double_click", "scroll", "move", "drag"])
def test_different_popup_root_is_refused_before_down_or_wheel(desk, action):
    desk.roots[(1411, 594)] = 99  # a popup (even one owned by the app) is its own root
    with pytest.raises(RuntimeError, match="其他窗口"):
        send(desk, action, x=.5, y=.5, path=[{"x": .5, "y": .5}, {"x": .6, "y": .6}])
    assert not [e for e in desk.log if e[0] == "mouse"]


def test_double_click_rechecks_hit_before_second_down(desk, monkeypatch):
    hits = iter([HWND, 99])
    monkeypatch.setattr(projection, "_root_window_at", lambda point: next(hits))
    with pytest.raises(RuntimeError, match="其他窗口"):
        send(desk, "double_click", x=.5, y=.5)
    assert [e[1] for e in desk.log if e[0] == "mouse"] == [win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP]


def test_drag_error_after_down_always_releases(desk, monkeypatch):
    positions = []
    def lands(point):
        positions.append(point)
        desk.cursor = point if len(positions) < 3 else (0, 0)
    desk.client = (100, 100, 500, 400)
    monkeypatch.setattr(projection, "_set_cursor", lands)
    with pytest.raises(RuntimeError, match="鼠标未到达"):
        send(desk, "drag", path=[{"x": 0, "y": 0}, {"x": .5, "y": .5}, {"x": 1, "y": 1}], duration_ms=0)
    assert positions == [(100, 100), (300, 250), (499, 399)]
    assert [e[1] for e in desk.log if e[0] == "mouse"] == [win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP]


def test_focus_loss_during_drag_releases(desk, monkeypatch):
    def leave(point):
        desk.cursor = point
        desk.foreground = 99
    desk.client = (100, 100, 500, 400)
    monkeypatch.setattr(projection, "_set_cursor", leave)
    with pytest.raises(RuntimeError, match="焦点改变"):
        send(desk, "drag", path=[{"x": 0, "y": 0}, {"x": 1, "y": 1}])
    assert [e[1] for e in desk.log if e[0] == "mouse"] == [win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP]


def test_scroll_wheel_follows_verified_placement(desk):
    desk.client = (100, 100, 500, 400)
    send(desk, "scroll", x=.25, y=.5, delta=-240, axis="horizontal")
    assert desk.log[-2:] == [("cursor", (200, 250)), ("mouse", win32con.MOUSEEVENTF_HWHEEL, -240)]


@pytest.mark.parametrize("kw", [dict(x=float("nan")), dict(x=float("inf")), dict(y=-0.01), dict(y=1.5),
                                dict(x="a"), dict(action="drag", path=[{"x": 0, "y": 0}, {"x": float("nan"), "y": 0}]),
                                dict(action="drag", path=[{"x": 0, "y": 0}, {"y": 0}]),
                                dict(action="drag", path=[{"x": 0, "y": 0}, 5])])
def test_non_finite_or_out_of_range_points_are_refused_before_focus(desk, kw):
    desk.client, kw = (100, 100, 500, 400), dict(kw)
    with pytest.raises(ValueError):
        send(desk, kw.pop("action", "click"), **kw)
    assert desk.log == []


def test_multi_monitor_negative_coordinates_accepted_and_gap_refused(desk):
    desk.monitors = MULTI
    desk.client = (-600, -100, 400, 500)  # spans the left (negative) and primary displays
    send(desk, "click", x=.1, y=.1)
    assert ("cursor", (-500, -40)) in desk.log
    desk.log.clear()
    desk.client = (1800, 100, 2200, 400)  # x 1920..2019 lies in the gap between displays
    with pytest.raises(ValueError, match=r"\(1960, 250\)"):
        send(desk, "click", x=.4, y=.5)
    assert desk.log == []
    send(desk, "click", x=.9, y=.5)  # (2160, 250) is on the right display
    assert ("cursor", (2160, 250)) in desk.log


def test_window_moved_offscreen_by_focus_is_refused_before_button(desk):
    desk.client = (100, 100, 500, 400)
    desk.focus_moves = (1700, 0)
    with pytest.raises(ValueError, match="不在任何显示器内"):
        send(desk, "click", x=.9, y=.5)
    assert desk.events == [("focus",), ("move", 1700, 0)]


# -- geometry ---------------------------------------------------------------------------


def test_geometry_reports_public_facts_read_only(desk):
    desk.owner = 42
    geo = projection.projection_window_geometry(WID)
    assert geo == dict(window_id=WID, client_screen_rect=[805, 162, 2018, 1026], width=1213, height=864,
                       outer_screen_rect=[797, 131, 2026, 1034], monitor_rect=[0, 0, 1920, 1200],
                       workarea_rect=[0, 0, 1920, 1152], virtual_desktop_rect=[0, 0, 1920, 1200],
                       visible=True, minimized=False, client_on_monitor=False, outer_in_workarea=False,
                       owner_window_id="42:1:1")
    assert desk.log == []


def test_geometry_nearest_display_and_virtual_desktop_with_negative_coordinates(desk):
    desk.monitors = MULTI
    desk.client, desk.outer = (-1100, 0, -500, 400), (-1108, -31, -492, 408)
    geo = projection.projection_window_geometry(WID)
    assert geo["monitor_rect"] == [-1280, -200, 0, 824] and geo["workarea_rect"] == [-1280, -200, 0, 784]
    assert geo["virtual_desktop_rect"] == [-1280, -200, 3940, 1080]
    assert geo["client_on_monitor"] and geo["outer_in_workarea"] and geo["owner_window_id"] is None


@pytest.mark.parametrize("identities", [["other"], [WID, "other"]])
def test_geometry_refuses_replaced_window(desk, identities):
    desk.identities = identities
    with pytest.raises(ValueError, match="窗口已切换"):
        projection.projection_window_geometry(WID)


# -- fit --------------------------------------------------------------------------------


def test_fit_moves_only_into_workarea_preserving_size_without_clicks(desk):
    result = projection.fit_projection_window(WID)
    # outer 1229 wide must end at workarea right 1920; top already inside.
    assert result == dict(window_id=WID, moved=True, old_rect=[797, 131, 2026, 1034], workarea_rect=[0, 0, 1920, 1152],
                          width=1213, height=864, new_rect=[691, 131, 1920, 1034], client_screen_rect=[699, 162, 1912, 1026])
    assert desk.events == [("move", -106, 0)]
    send(desk, "click", x=1180 / 1213, y=65 / 864)  # the incident target is now on screen
    assert ("cursor", (1879, 227)) in desk.log


def test_fit_negative_display_moves_toward_its_own_workarea(desk):
    desk.monitors = MULTI
    desk.client, desk.outer = (-1400, 0, -800, 400), (-1408, -31, -792, 408)
    result = projection.fit_projection_window(WID)
    assert result["moved"] and result["new_rect"] == [-1280, -31, -664, 408]
    assert desk.events == [("move", 128, 0)]


def test_fit_already_inside_does_not_move(desk):
    desk.client, desk.outer = (100, 100, 500, 400), (92, 69, 508, 408)
    result = projection.fit_projection_window(WID)
    assert result["moved"] is False and result["new_rect"] == result["old_rect"] and desk.log == []


def test_fit_refuses_window_larger_than_workarea(desk):
    desk.client, desk.outer = (0, 0, 1900, 1150), (-8, -31, 1908, 1158)  # 1189 tall > 1152
    with pytest.raises(ValueError, match="大于所在显示器工作区"):
        projection.fit_projection_window(WID)
    assert desk.log == []


def test_fit_refuses_minimized_window_without_restoring(desk):
    desk.minimized = True
    with pytest.raises(ValueError, match="最小化"):
        projection.fit_projection_window(WID)
    assert desk.log == []


def test_fit_refuses_replaced_window_after_move(desk):
    desk.identities = [WID, "other"]
    with pytest.raises(ValueError, match="窗口已切换"):
        projection.fit_projection_window(WID)


def test_fit_refuses_client_size_change(desk, monkeypatch):
    def resizes(hwnd, dx, dy):
        desk.client = (0, 0, 10, 10)
    monkeypatch.setattr(projection, "_move_window_by", resizes)
    with pytest.raises(RuntimeError, match="尺寸变化"):
        projection.fit_projection_window(WID)


def test_fit_refuses_unconfirmed_position(desk, monkeypatch):
    monkeypatch.setattr(projection, "_move_window_by", lambda hwnd, dx, dy: None)  # the app ignored the move
    with pytest.raises(RuntimeError, match="未移到目标位置"):
        projection.fit_projection_window(WID)
