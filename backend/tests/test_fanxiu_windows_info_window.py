from __future__ import annotations

import json

import pytest

from backend.core.fanxiu.info_window import FanxiuWindowsInfoWindowClient
from backend.core.fanxiu.windows_info_window import (
    INFO_WINDOW_POLL_MILLISECONDS,
    ScreenRect,
    calculate_render_rect,
    select_overlay_boxes,
)


@pytest.mark.parametrize("enabled, auto, committed_at, now, expected", [
    (True, True, 100, 109.99, False),
    (True, True, 100, 110, True),
    (True, True, 110, 110, False),
    (False, True, 100, 120, False),
    (True, False, 100, 120, False),
    (True, True, 0, 120, True),
])
def test_info_window_refresh_deadline(enabled, auto, committed_at, now, expected):
    from backend.core.fanxiu.info_window_refresh import info_window_refresh_due

    assert info_window_refresh_due(
        {"enabled": enabled, "auto_refresh": auto},
        {"committed_at": committed_at}, now=now,
    ) is expected


@pytest.mark.parametrize("enabled, committed_at, requested_at, reason", [
    (True, 80, 89, "expired"),
    (True, 99, 100, "disabled_or_fresh"),
    (False, 80, 100, "disabled_or_fresh"),
])
def test_queued_info_window_refresh_rechecks_admission(monkeypatch, enabled, committed_at, requested_at, reason):
    from backend.core.fanxiu import info_window_refresh as refresh

    monkeypatch.setattr(refresh.time, "time", lambda: 100)
    monkeypatch.setattr(refresh, "fanxiu_info_window_settings_path", lambda: None)
    monkeypatch.setattr(refresh, "read_json_state_dict", lambda _: {"enabled": enabled, "auto_refresh": True})
    monkeypatch.setattr(refresh.fanxiu_info_window_state, "read", lambda: {"committed_at": committed_at})
    # No game double: all denied requests must return before touching a binding.
    assert refresh.refresh_info_window_in_kernel(None, requested_at=requested_at)["reason"] == reason


def test_info_window_uses_low_frequency_snapshot_polling() -> None:
    assert INFO_WINDOW_POLL_MILLISECONDS == 1000


def test_calculate_render_rect_excludes_mumu_custom_titlebar() -> None:
    assert calculate_render_rect(ScreenRect(2933, 0, 3833, 1661)) == ScreenRect(
        2933,
        61,
        3833,
        1661,
    )


def test_calculate_render_rect_centers_when_client_is_too_short() -> None:
    assert calculate_render_rect(ScreenRect(100, 200, 1000, 1700)) == ScreenRect(
        128,
        200,
        972,
        1700,
    )


def test_windows_renderer_heartbeat_requires_visible_recent_window(tmp_path) -> None:
    path = tmp_path / "windows_info_window.json"
    client = FanxiuWindowsInfoWindowClient(heartbeat_path=path)
    path.write_text(json.dumps({"running": True, "visible": True, "updated_at": 100.0}), encoding="utf-8")

    assert client.available(now=102.0) is True
    assert client.running(now=102.0) is True
    assert client.available(now=104.0) is False
    assert client.running(now=104.0) is False

    path.write_text(json.dumps({"running": True, "visible": False, "updated_at": 104.0}), encoding="utf-8")
    assert client.available(now=104.0) is False
    assert client.running(now=104.0) is True


def test_all_shapes_scope_includes_identity_without_drawing_it_twice() -> None:
    identity = {"x": 1, "y": 2, "w": 3, "h": 4}
    other = {"x": 5, "y": 6, "w": 7, "h": 8}
    payload = {"boxes": [identity], "all_shape_boxes": [identity, other]}

    assert select_overlay_boxes(payload, {
        "show_scene_identity_shapes": True,
        "show_all_shapes": False,
    }) == [identity]
    assert select_overlay_boxes(payload, {
        "show_scene_identity_shapes": True,
        "show_all_shapes": True,
    }) == [identity, other]
