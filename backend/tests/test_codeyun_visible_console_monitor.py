from scripts.codeyun_visible_console_monitor import _new_visible_window_events


def test_visible_window_reappearance_is_recorded_as_new_event() -> None:
    active_keys: set[tuple[int, int, str]] = set()
    window = {"hwnd": 100, "pid": 200, "title": "Terminal"}

    assert _new_visible_window_events([window], active_keys=active_keys) == [window]
    assert _new_visible_window_events([window], active_keys=active_keys) == []
    assert _new_visible_window_events([], active_keys=active_keys) == []
    assert _new_visible_window_events([window], active_keys=active_keys) == [window]
