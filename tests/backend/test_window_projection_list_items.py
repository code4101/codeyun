"""projection_window_controls: bounded read-only UIA control read of one window (UIA walk faked; no desktop)."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from backend.core.devices import ui_automation
from backend.core.devices import window_projection as projection

WID = "7:70:1.5"
CLIENT = (100, 200, 900, 800)  # screen pixels; client area is 800 x 600


def node(kind="EditControl", name="", value=None, rect=(110, 210, 210, 230), password=False, readonly=False):
    return SimpleNamespace(kind=kind, name=name, value=value, rect=rect, password=password, readonly=readonly)


@pytest.fixture()
def uia(monkeypatch):
    state = {"walk": [], "identities": None, "clients": None}
    monkeypatch.setattr(projection, "selected_window", lambda wid: SimpleNamespace(hwnd=7))
    monkeypatch.setattr(projection, "_window_identity",
                        lambda hwnd: state["identities"].pop(0) if state["identities"] else WID)
    monkeypatch.setattr(projection, "_client_rect",
                        lambda hwnd: state["clients"].pop(0) if state["clients"] else CLIENT)
    monkeypatch.setattr(projection, "_uia_walk", lambda hwnd: iter(state["walk"]))
    monkeypatch.setattr(projection, "_uia_control_type", lambda c: c.kind)
    monkeypatch.setattr(projection, "_uia_rect", lambda c: c.rect)
    monkeypatch.setattr(projection, "_uia_is_password", lambda c: c.password)
    monkeypatch.setattr(projection, "_uia_props", lambda c: dict(
        name=c.name, help_text="", value=c.value, is_readonly=None if c.value is None else c.readonly))
    monkeypatch.setattr(projection, "_window_application", lambda hwnd: "explorer.exe")
    monkeypatch.setattr(ui_automation, "ensure_ui_automation_thread_context", nullcontext)
    for name in ("projection_input", "pointer_gesture", "focus_projection_window", "capture_projection_window",
                 "restore_window", "activate_window", "click_window_title_bar", "type_projection_text",
                 "projection_shortcut", "write_clipboard_image", "place_pointer"):
        monkeypatch.setattr(projection, name, lambda *a, **k: pytest.fail("a control read sends no input or capture"))
    return state


FULL = (0, 0, 800, 600)
ROW = "一个非常长的文件名_会在原生文件选择器里被省略号截断_2026-10-22_最终版.zip"


def _walk():
    return [(node("PaneControl"), 0), (node("EditControl", "文件名:", "a.zip"), 1),
            (node("TextControl", "提示"), 1), (node("ListItemControl", ROW, rect=(120, 300, 520, 320)), 2)]


def test_default_types_unchanged_and_list_items_not_read(uia):
    uia["walk"] = _walk()
    assert projection.UIA_CONTROL_TYPES == ("EditControl", "TextControl")
    read = projection.projection_window_controls(WID, bounds_xywh=FULL)
    assert [c["type"] for c in read["controls"]] == ["EditControl", "TextControl"]
    assert read["controls"][0]["value"] == "a.zip" and read["controls"][0]["is_readonly"] is False


def test_list_item_opt_in_returns_full_name_and_client_bbox(uia):
    uia["walk"] = _walk()
    read = projection.projection_window_controls(WID, bounds_xywh=FULL, control_types=("ListItemControl",))
    assert read["window_id"] == WID and read["source"] == "uia"
    assert read["coordinate_space"] == "window_client_pixels" and read["truncated"] is False
    assert read["controls"] == [dict(type="ListItemControl", name=ROW, help_text="", value=None,
                                     is_readonly=None, bbox=[20, 100, 400, 20])]


def test_list_item_opt_in_alongside_defaults(uia):
    uia["walk"] = _walk()
    types = projection.UIA_CONTROL_TYPES + ("ListItemControl",)
    read = projection.projection_window_controls(WID, bounds_xywh=FULL, control_types=types)
    assert [c["type"] for c in read["controls"]] == ["EditControl", "TextControl", "ListItemControl"]


@pytest.mark.parametrize("types", [(), ("PaneControl",), ("ListItemControl", "ButtonControl"), ("ListControl",)])
def test_unsupported_types_rejected(uia, types):
    with pytest.raises(ValueError, match="control_types"):
        projection.projection_window_controls(WID, bounds_xywh=FULL, control_types=types)


def test_list_item_outside_bounds_and_password_excluded(uia):
    uia["walk"] = [(node("ListItemControl", "outside", rect=(120, 700, 520, 720)), 1),
                   (node("ListItemControl", "secret", password=True), 1),
                   (node("ListItemControl", "inside"), 1)]
    read = projection.projection_window_controls(WID, bounds_xywh=(0, 0, 400, 300),
                                                 control_types=("ListItemControl",))
    assert [c["name"] for c in read["controls"]] == ["inside"]


@pytest.mark.parametrize("walk", [
    [(node("PaneControl"), 0)] * (projection.UIA_NODE_LIMIT + 1),
    [(node("ListItemControl", "row"), 1)] * (projection.UIA_CONTROL_LIMIT + 1),
    [(node("ListItemControl", "n" * (projection.UIA_VALUE_LIMIT + 1)), 1)],
])
def test_list_item_caps_mark_the_read_truncated(uia, walk):
    uia["walk"] = walk
    read = projection.projection_window_controls(WID, bounds_xywh=FULL, control_types=("ListItemControl",))
    assert read["truncated"] is True
    assert len(read["controls"]) <= projection.UIA_CONTROL_LIMIT
    assert all(len(c["name"]) <= projection.UIA_VALUE_LIMIT for c in read["controls"])


@pytest.mark.parametrize("identities", [["other"], [WID, "other"]])  # replaced before or during the walk
def test_replaced_window_raises_instead_of_answering(uia, identities):
    uia["walk"] = _walk()
    uia["identities"] = identities
    with pytest.raises(ValueError, match="窗口已切换"):
        projection.projection_window_controls(WID, bounds_xywh=FULL, control_types=("ListItemControl",))


def test_moved_window_raises(uia):
    uia["walk"] = _walk()
    uia["clients"] = [CLIENT, (101, 200, 901, 800)]
    with pytest.raises(ValueError, match="窗口位置或尺寸已变化"):
        projection.projection_window_controls(WID, bounds_xywh=FULL, control_types=("ListItemControl",))


@pytest.mark.parametrize("bounds", [(0, 0, 801, 600), (-1, 0, 10, 10), (0, 0, 0, 10), ("x", 0, 1, 1)])
def test_bounds_must_lie_in_client_area(uia, bounds):
    with pytest.raises(ValueError, match="bounds_xywh"):
        projection.projection_window_controls(WID, bounds_xywh=bounds, control_types=("ListItemControl",))


def test_uia_failure_raises_class_only(uia, monkeypatch):
    uia["walk"] = _walk()

    def boom(c):
        raise OSError("secret detail")
    monkeypatch.setattr(projection, "_uia_props", boom)
    with pytest.raises(projection.UIAReadError, match="OSError") as exc:
        projection.projection_window_controls(WID, bounds_xywh=FULL, control_types=("ListItemControl",))
    assert "secret detail" not in str(exc.value)


def test_emulator_display_has_no_native_controls(uia, monkeypatch):
    monkeypatch.setattr(projection, "selected_window", lambda *a: pytest.fail("must not resolve a desktop window"))
    with pytest.raises(ValueError):
        projection.projection_window_controls(projection.FANXIU_ADB_WINDOW_ID, bounds_xywh=FULL,
                                              control_types=("ListItemControl",))
