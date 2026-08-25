from __future__ import annotations

import json

from backend.core.fanxiu.info_window import FanxiuWindowsInfoWindowClient
from backend.core.fanxiu.windows_info_window import (
    INFO_WINDOW_POLL_MILLISECONDS,
    ScreenRect,
    calculate_render_rect,
    select_overlay_boxes,
)


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
