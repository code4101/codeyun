"""projection_window_controls: bounded read-only UIA control values (static property/bbox seams; no desktop).

The seams only feed UIA properties and screen rectangles; the tests check selection and
screen->client projection math, caps, fences and the guarded forwarding, not any UI flow.
"""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from backend.core.baidu_netdisk.input_guard import GuardedDriver
from backend.core.devices import ui_automation
from backend.core.devices import window_projection as projection

WID = "7:70:1.5"
CLIENT = (100, 50, 500, 350)  # screen pixels: a 400x300 client area


def node(kind="EditControl", rect=(0, 0, 10, 10), name="", value="", help_text="", readonly=True,
         password=False, error=None):
    return SimpleNamespace(kind=kind, rect=rect, name=name, value=value, help_text=help_text,
                           readonly=readonly, password=password, error=error, read=False)


def _props(c):
    c.read = True
    if c.error:
        raise c.error
    return dict(name=c.name, help_text=c.help_text, value=c.value, is_readonly=None if c.value is None else c.readonly)


@pytest.fixture()
def uia(monkeypatch):
    state = {"walk": [], "identities": None, "rects": None}
    monkeypatch.setattr(projection, "selected_window", lambda wid: SimpleNamespace(hwnd=7))
    monkeypatch.setattr(projection, "_window_identity",
                        lambda hwnd: state["identities"].pop(0) if state["identities"] else WID)
    monkeypatch.setattr(projection, "_client_rect", lambda hwnd: state["rects"].pop(0) if state["rects"] else CLIENT)
    monkeypatch.setattr(projection, "_uia_walk", lambda hwnd: iter([(c, 1) for c in state["walk"]]))
    monkeypatch.setattr(projection, "_uia_control_type", lambda c: c.kind)
    monkeypatch.setattr(projection, "_uia_rect", lambda c: c.rect)
    monkeypatch.setattr(projection, "_uia_is_password", lambda c: c.password)
    monkeypatch.setattr(projection, "_uia_props", _props)
    monkeypatch.setattr(projection, "_window_application", lambda hwnd: "BaiduNetdisk.exe")
    monkeypatch.setattr(ui_automation, "ensure_ui_automation_thread_context", nullcontext)
    for name in ("projection_input", "pointer_gesture", "focus_projection_window", "capture_projection_window",
                 "restore_window", "activate_window", "write_clipboard_image", "read_projection_clipboard"):
        monkeypatch.setattr(projection, name, lambda *a, **k: pytest.fail("a control read sends no input or capture"))
    return state


def test_controls_inside_bounds_are_projected_to_client_pixels(uia):
    path = node(rect=(160, 90, 360, 110), value="D:\\很长的\\只读\\路径", help_text="tip")  # client (60,40)-(260,60)
    label = node("TextControl", (140, 92, 185, 108), name="下载路径", value=None)  # centre (62.5, 50)
    uia["walk"] = [node("PaneControl", (100, 50, 500, 350)),  # container whose centre is inside: type excluded
                   label, path,
                   node(rect=(160, 300, 360, 320), value="outside"),  # centre y 260: outside the bounds
                   node(rect=(160, 90, 360, 110), password=True)]  # in bounds, omitted, never read
    read = projection.projection_window_controls(WID, bounds_xywh=(50, 30, 300, 40))
    assert read["window_id"] == WID and read["application"] == "BaiduNetdisk.exe"
    assert (read["width"], read["height"], read["coordinate_space"], read["source"]) == (
        400, 300, "window_client_pixels", "uia")
    assert read["bounds_xywh"] == [50, 30, 300, 40] and read["truncated"] is False
    assert isinstance(read["read_at"], float)
    assert read["controls"] == [
        dict(type="TextControl", name="下载路径", help_text="", value=None, is_readonly=None, bbox=[40, 42, 45, 16]),
        dict(type="EditControl", name="", help_text="tip", value="D:\\很长的\\只读\\路径", is_readonly=True,
             bbox=[60, 40, 200, 20])]
    assert not uia["walk"][-1].read


def test_requested_types_only(uia):
    uia["walk"] = [node("TextControl", (160, 90, 360, 110), name="a"), node(rect=(160, 90, 360, 110), value="b")]
    read = projection.projection_window_controls(WID, bounds_xywh=(50, 30, 300, 40), control_types=["TextControl"])
    assert [c["type"] for c in read["controls"]] == ["TextControl"]


@pytest.mark.parametrize("bounds", [(0, 0, 0, 10), (0, 0, 10, -1), (-1, 0, 10, 10), (0, 0, 401, 10),
                                    (390, 0, 20, 10), (0, 295, 10, 10), (float("nan"), 0, 1, 1),
                                    (0, 0, float("inf"), 1), (0, 0, 1), ("a", 0, 1, 1), None])
def test_bounds_must_be_nonempty_finite_and_inside_the_client_area(uia, bounds):
    uia["walk"] = [node()]
    with pytest.raises(ValueError, match="bounds_xywh"):
        projection.projection_window_controls(WID, bounds_xywh=bounds)
    assert not uia["walk"][0].read


def test_minimized_window_has_no_client_area_to_read(uia):
    uia["rects"] = [(0, 0, 0, 0)]
    with pytest.raises(ValueError, match="bounds_xywh"):
        projection.projection_window_controls(WID, bounds_xywh=(0, 0, 1, 1))


@pytest.mark.parametrize("types", [(), ("PaneControl",), ("EditControl", "DocumentControl")])
def test_control_types_are_an_explicit_supported_subset(uia, types):
    with pytest.raises(ValueError, match="control_types"):
        projection.projection_window_controls(WID, bounds_xywh=(0, 0, 10, 10), control_types=types)


@pytest.mark.parametrize("walk", [
    [node(rect=(9000, 9000, 9001, 9001))] * (projection.UIA_NODE_LIMIT + 1),
    [node(rect=(160, 90, 360, 110))] * (projection.UIA_CONTROL_LIMIT + 1),
    [node(rect=(160, 90, 360, 110), value="v" * (projection.UIA_VALUE_LIMIT + 1))],
    [node(rect=(160, 90, 360, 110), name="n" * (projection.UIA_VALUE_LIMIT + 1))],
    [node(rect=(160, 90, 360, 110), help_text="h" * (projection.UIA_VALUE_LIMIT + 1))],
])
def test_caps_mark_the_read_truncated(uia, walk):
    uia["walk"] = walk
    read = projection.projection_window_controls(WID, bounds_xywh=(50, 30, 300, 40))
    assert read["truncated"] is True and len(read["controls"]) <= projection.UIA_CONTROL_LIMIT
    assert all(len(c[k] or "") <= projection.UIA_VALUE_LIMIT for c in read["controls"]
               for k in ("name", "value", "help_text"))


@pytest.mark.parametrize("identities", [["other"], [WID, "other"]])  # replaced before or during the walk
def test_replaced_window_raises_instead_of_answering(uia, identities):
    uia["identities"] = identities
    with pytest.raises(ValueError, match="窗口已切换"):
        projection.projection_window_controls(WID, bounds_xywh=(0, 0, 10, 10))


def test_moved_or_resized_client_area_during_the_walk_raises(uia):
    uia["rects"] = [CLIENT, (101, 50, 501, 350)]
    uia["walk"] = [node(rect=(160, 90, 360, 110), value="v")]
    with pytest.raises(ValueError, match="位置或尺寸已变化"):
        projection.projection_window_controls(WID, bounds_xywh=(50, 30, 300, 40))


def test_uia_failure_is_typed_and_never_an_empty_success(uia):
    class COMError(Exception):
        pass

    uia["walk"] = [node(rect=(160, 90, 360, 110), value="v"),
                   node(rect=(160, 90, 360, 110), error=COMError("D:\\private value"))]
    with pytest.raises(projection.UIAReadError, match="COMError") as info:
        projection.projection_window_controls(WID, bounds_xywh=(50, 30, 300, 40))
    assert "private" not in str(info.value) and info.value.__cause__ is None


def test_emulator_display_has_no_native_controls(uia, monkeypatch):
    monkeypatch.setattr(projection, "selected_window", lambda *a: pytest.fail("must not resolve a desktop window"))
    with pytest.raises(ValueError):
        projection.projection_window_controls(projection.FANXIU_ADB_WINDOW_ID, bounds_xywh=(0, 0, 1, 1))


# -- driver / guard forwarding ------------------------------------------------------


def test_driver_forwards_to_the_provider(monkeypatch):
    from backend.core.baidu_netdisk.asset_client import AssetProjectionDriver
    calls = []
    monkeypatch.setattr(projection, "projection_window_controls",
                        lambda wid, **kw: calls.append((wid, kw)) or {"controls": []})
    AssetProjectionDriver().native_controls(WID, bounds_xywh=[1, 2, 3, 4])
    assert calls == [(WID, {"bounds_xywh": (1, 2, 3, 4), "control_types": ("EditControl", "TextControl")})]


class Account:
    def __init__(self, allowed=True):
        self.allowed, self.log = allowed, []

    def check(self):
        self.log.append("check")
        if not self.allowed:
            raise RuntimeError("lease lost")

    def pace(self):
        self.log.append("pace")

    def record_activity(self):
        self.log.append("activity")


class Inner:
    def __init__(self):
        self.calls = []

    def native_controls(self, window_id, **bounded):
        self.calls.append((window_id, bounded))
        return {"controls": []}

    def input(self, frame, **action):
        pytest.fail("a control read is not an input")


def test_guard_checks_the_lease_and_never_paces_or_counts_an_input():
    inner, account = Inner(), Account()
    guard = GuardedDriver(inner, account=account, gate=lambda f, a: pytest.fail("a read passes no input gate"))
    assert guard.native_controls(WID, bounds_xywh=(0, 0, 5, 5)) == {"controls": []}
    assert account.log == ["check"] and guard.generation == 0
    assert inner.calls == [(WID, {"bounds_xywh": (0, 0, 5, 5)})]
    refused = GuardedDriver(inner, account=Account(allowed=False), gate=lambda f, a: None)
    with pytest.raises(RuntimeError, match="lease lost"):
        refused.native_controls(WID, bounds_xywh=(0, 0, 5, 5))
    assert len(inner.calls) == 1
