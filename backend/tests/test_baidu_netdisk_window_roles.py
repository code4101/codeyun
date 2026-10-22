import pytest

from backend.core.baidu_netdisk.client import FILE_DIALOG_TITLE
from backend.core.baidu_netdisk.window_roles import (MAIN_TITLE, SETTINGS_TITLE, interfering_windows, role_rows,
                                                     scene_window, window_specs)

PROFILE = "pc-8.1.6"
BASE = {"profile": PROFILE, "client_version": "8.1.6.344", "dpi": 96}
MAIN = {**BASE, "scene_id": "home", "width": 1213, "height": 864}
AUX = {**BASE, "scene_id": "aux", "window_role": "transfer-panel", "window_title": MAIN_TITLE,
       "width": 1920, "height": 1152}
SETTINGS = {**BASE, "scene_id": "settings", "window_role": "settings", "window_title": SETTINGS_TITLE,
            "width": 723, "height": 450}

LIVE = {
    "m": {"client_version": "8.1.6.344", "width": 1213, "height": 864, "dpi": 96, "visible": True, "minimized": False},
    "a": {"client_version": "8.1.6.344", "width": 1920, "height": 1152, "dpi": 96, "visible": True, "minimized": False},
    "s": {"client_version": "8.1.6.344", "width": 723, "height": 450, "dpi": 96, "visible": True, "minimized": False},
}
# outer list-row sizes deliberately differ from the live client sizes
WINDOWS = [{"id": "m", "title": MAIN_TITLE, "width": 1229, "height": 903},
           {"id": "a", "title": MAIN_TITLE, "width": 1936, "height": 1191}]


def reader(table):
    def read(window_id):
        if window_id not in table:
            raise RuntimeError("window gone")
        return table[window_id]
    return read


def ids(rows):
    return [r["id"] for r in rows]


def test_same_title_windows_split_by_live_client_facts():
    specs = window_specs([MAIN, AUX], PROFILE)
    assert ids(role_rows(WINDOWS, specs, "main", reader(LIVE))) == ["m"]
    assert ids(role_rows(WINDOWS, specs, "transfer-panel", reader(LIVE))) == ["a"]


def test_duplicate_exact_profile_stays_ambiguous():
    specs = window_specs([MAIN, AUX], PROFILE)
    live = {**LIVE, "m2": LIVE["m"]}
    windows = [*WINDOWS, {"id": "m2", "title": MAIN_TITLE, "width": 1229, "height": 903}]
    assert ids(role_rows(windows, specs, "main", reader(live))) == ["m", "m2"]


def test_settings_matches_live_client_not_outer_row():
    specs = window_specs([MAIN, SETTINGS], PROFILE)
    windows = [{"id": "s", "title": SETTINGS_TITLE, "width": 725, "height": 477}]
    assert ids(role_rows(windows, specs, "settings", reader(LIVE))) == ["s"]


def test_fact_read_failure_never_matches():
    specs = window_specs([MAIN, AUX], PROFILE)
    assert role_rows(WINDOWS, specs, "transfer-panel", reader({"m": LIVE["m"]})) == []


def test_profile_filter_and_no_fabricated_main():
    assert window_specs([AUX], "other-profile") == []
    specs = window_specs([AUX], PROFILE)
    assert [s["role"] for s in specs] == ["transfer-panel"]
    with pytest.raises(ValueError):
        role_rows(WINDOWS, specs, "main", reader(LIVE))


def test_main_scene_without_facts_never_selects_or_declares():
    specs = window_specs([{"profile": PROFILE, "scene_id": "legacy"}, AUX], PROFILE)
    assert {"role": "main", "title": MAIN_TITLE, "facts": None} in specs
    assert role_rows(WINDOWS, specs, "main", reader(LIVE)) == []
    assert interfering_windows(WINDOWS, specs, "a", reader(LIVE)) == ["undeclared"]


def test_missing_live_facts_never_match():
    specs = window_specs([MAIN, AUX], PROFILE)
    partial = {"m": {k: v for k, v in LIVE["m"].items() if k != "dpi"}}
    assert role_rows(WINDOWS, specs, "main", reader(partial)) == []
    assert role_rows(WINDOWS, specs, "main", reader({})) == []


def test_visible_auxiliary_blocks_main_and_minimized_does_not():
    specs = window_specs([MAIN, AUX], PROFILE)
    unknown = {**LIVE, "a": {**LIVE["a"], "width": 800}}
    assert interfering_windows(WINDOWS, specs, "m", reader(LIVE)) == ["transfer-panel"]
    assert interfering_windows(WINDOWS, specs, "m", reader(unknown)) == ["undeclared"]
    minimized = {**LIVE, "a": {**LIVE["a"], "width": 800, "minimized": True}}
    assert interfering_windows(WINDOWS, specs, "m", reader(minimized)) == []
    hidden = {**LIVE, "a": {**LIVE["a"], "visible": False}}
    assert interfering_windows(WINDOWS, specs, "m", reader(hidden)) == []


def test_unreadable_or_unflagged_facts_are_unknown_interference():
    specs = window_specs([MAIN, AUX], PROFILE)
    assert interfering_windows(WINDOWS, specs, "m", reader({"m": LIVE["m"]})) == ["unknown"]
    no_flags = {**LIVE, "a": {k: v for k, v in LIVE["a"].items() if k != "visible"}}
    assert interfering_windows(WINDOWS, specs, "m", reader(no_flags)) == ["unknown"]


def test_builtin_titles_and_compatible_scene_window():
    assert scene_window({}) == ("main", MAIN_TITLE)
    assert scene_window(SETTINGS) == ("settings", SETTINGS_TITLE)
    assert scene_window({**BASE, "width": 1, "height": 1, "window_role": "file-dialog",
                         "window_title": FILE_DIALOG_TITLE}) == ("file-dialog", FILE_DIALOG_TITLE)
    assert scene_window(AUX) == ("transfer-panel", MAIN_TITLE)


@pytest.mark.parametrize("body", [
    {**AUX, "window_title": ""},
    {**AUX, "window_title": None},
    {**AUX, "window_title": f" {MAIN_TITLE}"},
    {**AUX, "dpi": None},
    {**AUX, "width": "1920"},
    {**AUX, "height": True},
    {**AUX, "client_version": ""},
    {**AUX, "window_role": "Bad Role"},
    {**AUX, "window_role": 3},
    {**SETTINGS, "window_title": "Settings"},
    {**MAIN, "dpi": None},
])
def test_malformed_scene_refuses(body):
    with pytest.raises(ValueError):
        scene_window(body)
    with pytest.raises(ValueError):
        window_specs([body], PROFILE)


def test_conflicting_titles_for_same_role_refuse():
    with pytest.raises(ValueError, match="conflicting"):
        window_specs([AUX, {**AUX, "window_title": "其他"}], PROFILE)
