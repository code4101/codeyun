from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.world_map import (
    _ordered_target_point,
    ensure_world_realm,
    normalize_world_realm,
    read_world_realm,
)


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


class _FakeRuntime:
    def __init__(self, current: str, *, option_tokens=None):
        self.current = current
        self.menu_open = False
        self.option_tokens = list(option_tokens or [])
        self.clicks = []
        self._views = {
            425: SimpleNamespace(
                raw={"width": 900, "height": 1600}
            ),
            426: SimpleNamespace(
                raw={"width": 900, "height": 1600}
            ),
        }
        self._shapes = {
            (425, "界面"): SimpleNamespace(
                raw={"x": 0.833333, "y": 0.039583, "w": 0.12037, "h": 0.066667}
            ),
            (426, "选项"): SimpleNamespace(
                raw={"x": 0.640741, "y": 0.008333, "w": 0.359259, "h": 0.197917}
            ),
        }

    def view(self, view_id):
        return self._views[int(view_id)]

    def shape(self, view, title):
        view_id = next(
            key for key, value in self._views.items() if value is view
        )
        return self._shapes[(view_id, title)]

    def cur_frame(self, update=False):
        return "frame"

    def ocr_tokens_in_shapes(self, view, shapes, **_options):
        if int(view) == 425:
            if _options.get("crop"):
                return [
                    {
                        "text": "卜",
                        "x": 780,
                        "y": 60,
                        "w": 50,
                        "h": 70,
                    }
                ]
            return [
                {
                    "text": self.current,
                    "x": 780,
                    "y": 60,
                    "w": 50,
                    "h": 70,
                }
            ]
        return list(self.option_tokens)

    def click_shape_center(self, view, shape):
        assert (view, shape) == (425, "界面")
        self.menu_open = True
        self.clicks.append(("open", view, shape))

    def click_frame_point(self, view, x, y):
        assert int(view) == 426
        self.clicks.append(("select", x, y))
        if self.option_tokens:
            self.current = "灵"
        else:
            self.current = "魔"
        self.menu_open = False

    def wait_action_settle(self, _seconds):
        if False:
            yield None

    def clear_frame(self):
        return None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("人", "人"),
        ("人界", "人"),
        (" 灵界 ", "灵"),
        ("魔界", "魔"),
        ("仙", "仙"),
    ],
)
def test_normalize_world_realm(value, expected):
    assert normalize_world_realm(value) == expected


def test_read_world_realm_uses_only_first_character():
    runtime = _FakeRuntime("仙界")

    result = read_world_realm(runtime)

    assert result["ok"] is True
    assert result["realm"] == "仙"
    assert result["realm_name"] == "仙界"


def test_read_world_realm_prefers_valid_full_frame_over_bad_crop():
    runtime = _FakeRuntime("人")

    result = read_world_realm(runtime)

    assert result["realm"] == "人"
    assert result["cropped_tokens"][0]["text"] == "卜"


def test_ensure_world_realm_is_idempotent():
    runtime = _FakeRuntime("人")

    result = _drain(ensure_world_realm(runtime, "人界"))

    assert result["changed"] is False
    assert runtime.clicks == []


def test_ensure_world_realm_prefers_target_character_ocr():
    runtime = _FakeRuntime(
        "人",
        option_tokens=[
            {"text": "灵", "x": 610, "y": 80, "w": 40, "h": 60},
        ],
    )

    result = _drain(ensure_world_realm(runtime, "灵界"))

    assert result["changed"] is True
    assert result["attempts"][0]["action_source"] == "target_first_character_ocr"
    assert runtime.clicks[1] == ("select", 630.0, 110.0)


def test_ensure_world_realm_falls_back_to_cyclic_anchor():
    runtime = _FakeRuntime("人")

    result = _drain(ensure_world_realm(runtime, "魔界"))

    assert result["changed"] is True
    assert result["realm"] == "魔"
    assert result["attempts"][0]["action_source"] == "ordered_anchor_fallback"


@pytest.mark.parametrize(
    ("current", "target", "option_index"),
    [
        ("人", "灵", 0),
        ("人", "魔", 1),
        ("人", "仙", 2),
        ("灵", "人", 0),
        ("灵", "魔", 1),
        ("灵", "仙", 2),
        ("魔", "人", 0),
        ("魔", "灵", 1),
        ("魔", "仙", 2),
        ("仙", "人", 0),
        ("仙", "灵", 1),
        ("仙", "魔", 2),
    ],
)
def test_ordered_anchor_covers_every_realm_pair(
    current,
    target,
    option_index,
):
    runtime = _FakeRuntime("人")

    point = _ordered_target_point(
        runtime,
        current=current,
        target=target,
    )
    expected = (
        (641.33331, 111.49987),
        (689.833275, 206.50003),
        (806.233191, 276.16685),
    )[option_index]
    assert point == pytest.approx(expected, abs=0.001)
