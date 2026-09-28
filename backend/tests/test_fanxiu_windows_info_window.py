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


def test_ai_control_denies_overlay_submission_and_queued_observation(monkeypatch):
    from types import SimpleNamespace
    from backend.core.fanxiu import info_window_refresh as refresh
    from backend.core.fanxiu.windows_info_window import FanxiuWindowsInfoWindow
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control
    from backend.core.fanxiu.behavior_tree import jupyter_kernel

    monkeypatch.setattr(control, "read_scheduler_settings", lambda: {"job_group_enabled": False})
    calls = []
    monkeypatch.setattr(jupyter_kernel, "execute_fanxiu_jupyter_cell", lambda *a, **kw: calls.append(kw))
    window = SimpleNamespace(refresh_error="")
    FanxiuWindowsInfoWindow._refresh_scene(window)
    assert calls == [] and window.refresh_error == ""

    monkeypatch.setattr(refresh.time, "time", lambda: 100)
    monkeypatch.setattr(refresh, "fanxiu_info_window_settings_path", lambda: None)
    monkeypatch.setattr(refresh, "read_json_state_dict", lambda _: {"enabled": True, "auto_refresh": True})
    monkeypatch.setattr(refresh.fanxiu_info_window_state, "read", lambda: {"committed_at": 80})
    assert refresh.refresh_info_window_in_kernel(None, requested_at=100)["reason"] == "ai_control"


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


def test_magic_crystal_replaces_the_scene_number_on_the_auto_running_page() -> None:
    from backend.core.fanxiu.info_window import format_fanxiu_scene_text

    # #699 shows only the live quantity: no directory, number or 置信度.
    assert format_fanxiu_scene_text(
        699,
        100.0,
        asset_directory="日程/玩法榜/魔道入侵",
        magic_crystal=37081,
    ) == "#699 魔晶 3.708万"
    # 场景编号 off: the reading is its own display element.
    assert format_fanxiu_scene_text(
        699,
        100.0,
        asset_directory="日程/玩法榜/魔道入侵",
        show_scene_id=False,
        show_scene_score=False,
        magic_crystal=37081,
    ) == "魔晶 3.708万"


def test_xutian_currency_only_replaces_the_835_auto_running_page() -> None:
    from backend.core.fanxiu.info_window import format_fanxiu_scene_text

    assert format_fanxiu_scene_text(
        835, 100, asset_directory="日程/玩法榜/虚天殿", xutian_currency=2086,
    ) == "#835 纳元晶 2086"
    assert format_fanxiu_scene_text(
        835, 100, asset_directory="日程/玩法榜/虚天殿", xutian_currency=None,
    ) == "日程/玩法榜/虚天殿 #835 100%"
    assert format_fanxiu_scene_text(
        615, 100, asset_directory="日程/玩法榜/虚天殿", xutian_currency=2086,
    ) == "日程/玩法榜/虚天殿 #615 100%"


@pytest.mark.parametrize("scene_id, enabled, ok, expected", [
    (835, True, True, 2086),
    (835, False, True, None),
    (835, True, False, None),
    (615, True, True, None),
])
def test_xutian_currency_requires_835_and_a_successful_enabled_read(
    scene_id, enabled, ok, expected,
) -> None:
    from backend.core.fanxiu.windows_info_window import FanxiuWindowsInfoWindow

    class Stub:
        settings = {"show_xutian_currency": enabled}
        xutian_currency = {"ok": ok, "current": 1138, "cumulative": 2086}

    assert FanxiuWindowsInfoWindow._xutian_currency_value(Stub(), scene_id) == expected


def test_xutian_reader_uses_fixed_wallet_currency_and_cumulative_history(monkeypatch) -> None:
    from backend.core.fanxiu.instrumentation import xutian_currency

    calls = []

    def read(currency_type, *, allow_discovery):
        calls.append((currency_type, allow_discovery))
        return {
            "exchange_currency": 1138,
            "cumulative_currency": 2086,
            "captured_at": "2026-09-24T12:00:00+08:00",
        }

    monkeypatch.setattr(xutian_currency, "read_wallet_currency_snapshot", read)
    reader = xutian_currency.XutianCurrencyReader()
    assert reader.read(now=100)["cumulative"] == 2086
    assert reader.read(now=101)["current"] == 1138
    assert calls == [(12, True), (12, False)]


@pytest.mark.parametrize("value, expected", [
    (0, "0"),
    (876, "876"),
    (9999, "9999"),
    (10000, "1万"),
    (37081, "3.708万"),
    (158441, "15.84万"),
    (209821, "20.98万"),
    (12345678, "1235万"),
    (100000000, "1亿"),
    (150000000, "1.5亿"),
])
def test_game_quantities_use_万_亿_with_four_significant_digits(value, expected) -> None:
    from backend.core.fanxiu.info_window import format_fanxiu_quantity

    assert format_fanxiu_quantity(value) == expected


@pytest.mark.parametrize("scene_id, magic_crystal", [
    (698, None),   # 自动除魔配置页 keeps its scene number
    (699, None),    # no wallet observation yet: identity must not be lost
])
def test_scene_number_survives_without_a_magic_crystal_observation(scene_id, magic_crystal) -> None:
    from backend.core.fanxiu.info_window import format_fanxiu_scene_text

    assert format_fanxiu_scene_text(
        scene_id,
        100.0,
        asset_directory="日程/玩法榜/魔道入侵",
        magic_crystal=magic_crystal,
    ) == f"日程/玩法榜/魔道入侵 #{scene_id} 100%"


def test_magic_crystal_observation_is_a_display_setting_not_a_hidden_default() -> None:
    from backend.core.fanxiu.info_window import normalize_fanxiu_info_window_settings

    assert normalize_fanxiu_info_window_settings({})["show_magic_crystal"] is True
    assert normalize_fanxiu_info_window_settings({"show_magic_crystal": False})[
        "show_magic_crystal"
    ] is False


def test_active_magic_invasion_occurrence_prefers_the_running_cross_server_phase() -> None:
    from backend.core.fanxiu.instrumentation.magic_invasion_magic_crystal import (
        active_magic_invasion_occurrence,
    )

    # Runtime #66 rows carry millisecond epochs and ``serverCount``; the
    # discovery artifact stores the same facts as ISO text and ``cross_count``.
    def row(name, start_ms, end_ms, server_count, base_id, activity_id):
        return {
            "name": name,
            "startTime": start_ms,
            "endTime": end_ms,
            "serverCount": server_count,
            "base_id": base_id,
            "activityId": activity_id,
        }

    occurrences = [
        row("魔道入侵", 1789351200000, 1789394400000, None, 70000, 1070011400004),
        row("魔道入侵", 1789437600000, 1789480800000, 16, 70001, 16070001400004),
        row("论道", 1789437600000, 1789480800000, 16, 110000, 1116),
    ]
    running = active_magic_invasion_occurrence(
        occurrences, now=1789476000.0
    )
    assert running["base_id"] == "70001"
    assert running["cross_count"] == 16
    assert active_magic_invasion_occurrence(occurrences, now=1789351200.0)["base_id"] == "70000"
    # 09-14 09:00 (+08:00), before every listed 魔道入侵 window.
    assert active_magic_invasion_occurrence(occurrences, now=1789347600.0) is None


def test_occurrence_rows_accept_both_schedule_shapes() -> None:
    from backend.core.fanxiu.instrumentation.magic_invasion_magic_crystal import (
        magic_invasion_occurrence_rows,
    )

    assert magic_invasion_occurrence_rows({"items": [{"name": "魔道入侵"}]}) == [
        {"name": "魔道入侵"}
    ]
    assert magic_invasion_occurrence_rows({"occurrences": [{"name": "魔道入侵"}]}) == [
        {"name": "魔道入侵"}
    ]
    assert magic_invasion_occurrence_rows({}) == []


@pytest.mark.parametrize("scene_id, enabled, ok, expected", [
    (699, True, True, 37081),
    (699, False, True, None),   # 魔晶数量 switch off
    (699, True, False, None),   # no successful wallet observation yet
    (698, True, True, 37081),   # configuration keeps the live wallet visible
    (None, True, True, None),
])
def test_magic_crystal_is_only_drawn_for_a_read_auto_running_page(
    scene_id, enabled, ok, expected
) -> None:
    from backend.core.fanxiu.windows_info_window import FanxiuWindowsInfoWindow

    class Stub:
        settings = {"show_magic_crystal": enabled}
        # The title shows 活动期间累计魔晶, never the spendable balance.
        magic_crystal = (
            {"ok": ok, "current": 1234, "cumulative": 37081} if ok else {"ok": False}
        )

    assert FanxiuWindowsInfoWindow._magic_crystal_value(Stub(), scene_id) == expected
