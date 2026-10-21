"""projection_window_documents: read-only UIA Document read of one window (UIA walk faked; no desktop)."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from backend.core.devices import ui_automation
from backend.core.devices import window_projection as projection

WID = "7:70:1.5"


def node(name="", value="", doc=False):
    return SimpleNamespace(Name=name, value=value, doc=doc)


@pytest.fixture()
def uia(monkeypatch):
    state = {"walk": [], "identities": None}
    monkeypatch.setattr(projection, "selected_window", lambda wid: SimpleNamespace(hwnd=7))
    monkeypatch.setattr(projection, "_window_identity",
                        lambda hwnd: state["identities"].pop(0) if state["identities"] else WID)
    monkeypatch.setattr(projection, "_uia_walk", lambda hwnd: iter(state["walk"]))
    monkeypatch.setattr(projection, "_uia_document", lambda c: c.doc)
    monkeypatch.setattr(projection, "_uia_value", lambda c: c.value)
    monkeypatch.setattr(projection, "_window_application", lambda hwnd: "BaiduNetdiskUnite.exe")
    monkeypatch.setattr(ui_automation, "ensure_ui_automation_thread_context", nullcontext)
    for name in ("projection_input", "pointer_gesture", "focus_projection_window", "capture_projection_window",
                 "restore_window", "activate_window"):
        monkeypatch.setattr(projection, name, lambda *a, **k: pytest.fail("a document read sends no input or capture"))
    return state


def test_documents_report_value_and_iframe_nesting(uia):
    uia["walk"] = [(node(), 0), (node("百度网盘", "file:///x#/?path=%2F", doc=True), 1), (node(), 2),
                   (node("", "https://a/b", doc=True), 3), (node(), 1), (node("", "", doc=True), 1)]
    read = projection.projection_window_documents(WID)
    assert read["window_id"] == WID and read["application"] == "BaiduNetdiskUnite.exe"
    assert read["truncated"] is False and isinstance(read["read_at"], float)
    assert [(d["name"], d["value"], d["nested"]) for d in read["documents"]] == [
        ("百度网盘", "file:///x#/?path=%2F", False), ("", "https://a/b", True), ("", "", False)]


@pytest.mark.parametrize("walk", [
    [(node(), 0)] * (projection.UIA_NODE_LIMIT + 1),
    [(node(doc=True), 1)] * (projection.UIA_DOCUMENT_LIMIT + 1),
    [(node(value="v" * (projection.UIA_VALUE_LIMIT + 1), doc=True), 1)],
])
def test_caps_mark_the_read_truncated(uia, walk):
    uia["walk"] = walk
    read = projection.projection_window_documents(WID)
    assert read["truncated"] is True
    assert len(read["documents"]) <= projection.UIA_DOCUMENT_LIMIT
    assert all(len(d["value"]) <= projection.UIA_VALUE_LIMIT for d in read["documents"])


@pytest.mark.parametrize("identities", [["other"], [WID, "other"]])  # replaced before or during the walk
def test_replaced_window_raises_instead_of_answering(uia, identities):
    uia["identities"] = identities
    with pytest.raises(ValueError, match="窗口已切换"):
        projection.projection_window_documents(WID)


def test_emulator_display_has_no_native_documents(uia, monkeypatch):
    monkeypatch.setattr(projection, "selected_window", lambda *a: pytest.fail("must not resolve a desktop window"))
    with pytest.raises(ValueError):
        projection.projection_window_documents(projection.FANXIU_ADB_WINDOW_ID)
