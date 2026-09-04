from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.tasks import xutian_native_auto as xutian
from backend.core.fanxiu.data_annotation.tasks import yunmeng_native_auto as yunmeng
from backend.core.fanxiu.instrumentation import tiandi_yiju as tiandi_runtime
from backend.core.fanxiu.instrumentation import ui_runtime_context
from backend.core.fanxiu.instrumentation import xutian_runtime
from backend.core.fanxiu.instrumentation import yunmeng_trial


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as stop:
        return stop.value


def _empty_generator(*_args, **_kwargs):
    if False:
        yield None
    return None


class _Context:
    def __init__(self):
        self.clicks = []

    def shape_box(self, scene_id, shape):
        widths = {
            (560, "挑战次数_减少"): 63.0,
            (560, "挑战次数_滑块"): 58.5,
            (615, "挑战次数_减少"): 49.5,
            (615, "挑战次数_滑块"): 40.5,
        }
        return {"w": widths[(scene_id, shape)], "h": 40.0}

    def click_shape_center(self, scene_id, shape):
        self.clicks.append((scene_id, shape))

    def wait_action_settle(self, _seconds):
        return _empty_generator()

    def current_scene(self, scene_ids, *, update):
        assert update is True
        scene = 562 if 562 in scene_ids else 615
        if False:
            yield None
        return scene, 100.0, "frame"

    def ocr_text(self, _frame):
        return "自动挑战已完成"

    def cur_frame(self, *, update):
        assert update is True
        return "frame"


def test_yunmeng_adapter_uses_live_panel_maximum_and_fixed_m10(monkeypatch) -> None:
    calls = []

    monkeypatch.setattr(yunmeng, "_observe", _empty_generator)
    monkeypatch.setattr(yunmeng, "_attempt_optional_boost", lambda *_a, **_k: _returning(True))
    monkeypatch.setattr(
        yunmeng,
        "_set_required_toggle",
        lambda _context, _assets, _asset, desired: _returning(desired),
    )

    def set_count(context, assets, desired, **options):
        calls.append((assets, desired, options))
        if False:
            yield None
        return {"before": 240, "after": desired, "maximum": options["maximum"]}

    monkeypatch.setattr(yunmeng, "_set_count", set_count)
    monkeypatch.setattr(yunmeng, "read_positive_integer_count", lambda *_a, **_k: 25)
    snapshots = iter((
        {"current": 240, "maximum": 240},
        {"current": 25, "maximum": 240},
    ))
    monkeypatch.setattr(
        yunmeng_trial, "read_yunmeng_auto_count_snapshot", lambda: next(snapshots)
    )

    result = _drain(
        yunmeng.run_yunmeng_native_auto(
            _Context(),
            yunmeng.YunmengNativeAutoAssets(558, 560, (562,), 561),
            yunmeng.YunmengNativeAutoRequest(
                requested_challenges=25, max_count_adjustments=200
            ),
            terminal_polls=1,
        )
    )

    assert result.settings.requested_challenges == 25
    assert calls[0][1] == 25
    assert calls[0][2]["maximum"] == 240
    assert calls[0][2]["max_adjustments"] == 10
    assert calls[0][0].count_slider_left_center_offset == 31.5
    assert calls[0][0].count_slider_right_center_offset == 29.25


def _returning(value):
    if False:
        yield None
    return value


def test_xutian_adapter_uses_live_panel_maximum_and_fixed_m10(monkeypatch) -> None:
    calls = []
    identity = {
        "source": "runtime_memory",
        "current_heaven": 1,
        "evidence": {"pid": 7, "process_start_ticks": 9, "auto_settings_raw": {}},
        "available_quality_keys": [],
        "special_options": {
            "find_demon_selected": False,
            "native_soul_lock_selected": False,
        },
        "auto_settings": {},
        "auto_progress": {"running": False, "completed_challenges": 0},
    }
    snapshots = iter((
        dict(identity),
        dict(identity),
        {**identity, "auto_progress": {"running": False, "completed_challenges": 25}},
    ))
    monkeypatch.setattr(xutian, "_read_auto_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(xutian, "_reconcile_lower_switch", _empty_generator)
    monkeypatch.setattr(xutian, "validate_xutian_auto_settings", lambda *_a, **_k: [])
    monkeypatch.setattr(xutian, "_wait_scene", lambda *_a, **_k: _returning((614, 100.0, "frame")))
    monkeypatch.setattr(xutian, "read_positive_integer_count", lambda *_a, **_k: 25)

    def set_count(context, assets, desired, **options):
        calls.append((assets, desired, options))
        if False:
            yield None
        return {"before": 320, "after": desired, "maximum": options["maximum"]}

    monkeypatch.setattr(xutian, "_set_count", set_count)
    count_snapshots = iter((
        {"current": 320, "maximum": 320},
        {"current": 25, "maximum": 320},
    ))
    monkeypatch.setattr(
        xutian_runtime, "read_xutian_auto_count_snapshot", lambda: next(count_snapshots)
    )

    result = _drain(
        xutian._configure_and_run_batch(
            _Context(),
            requested_challenges=25,
            stop_event=SimpleNamespace(is_set=lambda: False),
        )
    )

    assert result["auto_progress"]["completed_challenges"] == 25
    assert calls[0][2]["maximum"] == 320
    assert calls[0][2]["max_adjustments"] == 10
    assert calls[0][0].count_slider_left_center_offset == 24.75
    assert calls[0][0].count_slider_right_center_offset == 20.25


@pytest.mark.parametrize(
    ("reader", "fields", "current", "maximum", "source"),
    [
        (
            yunmeng_trial.read_yunmeng_auto_count_snapshot,
            {"_CurMaxFightCount": 21, "_MaxSliderValue": 237},
            21,
            237,
            "active_yunmeng_auto_settings_panel",
        ),
        (
            xutian_runtime.read_xutian_auto_count_snapshot,
            {"_CurMaxFightCount": 33, "_MaxSliderValue": 408},
            33,
            408,
            "active_xutian_auto_settings_panel",
        ),
        (
            tiandi_runtime.read_tiandi_yiju_auto_count_snapshot,
            {"useNum": 44, "useMax": 4325},
            44,
            4325,
            "active_tiandi_yiju_auto_panel",
        ),
    ],
)
def test_active_panel_runtime_count_readers(
    monkeypatch, reader, fields, current, maximum, source
) -> None:
    component = SimpleNamespace(address=0x1234)
    binding = SimpleNamespace(pid=71, process_start_ticks=73)
    monkeypatch.setattr(
        ui_runtime_context,
        "active_ui_component_objects",
        lambda _context: [component],
    )
    monkeypatch.setattr(
        ui_runtime_context,
        "read_ui_object_field",
        lambda _context, _address, name: fields.get(name),
    )
    monkeypatch.setattr(
        ui_runtime_context,
        "read_ui_runtime_snapshot",
        lambda required, callback, *, fast: callback(
            SimpleNamespace(
                reader=SimpleNamespace(fields=lambda _component: {key: True for key in required}),
                binding=binding,
            )
        ),
    )

    snapshot = reader()

    assert snapshot["current"] == current
    assert snapshot["maximum"] == maximum
    assert snapshot["minimum"] == 1
    assert snapshot["source"] == source
    assert snapshot["evidence"]["panel_address"] == "0x1234"
