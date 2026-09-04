from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.tasks import magic_invasion
from backend.core.fanxiu.instrumentation import item_batch_use_dialog
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
)


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stopped:
            return stopped.value


class _Reader:
    def __init__(self, fields):
        self._fields = fields

    def fields(self, value):
        address = value.address if isinstance(value, LuaRef) else int(value)
        return self._fields.get(address, {})

    def long(self, value):
        return int(value)


def _runtime_context(*, current=7, use_max=120, owned=900, use_parameter_max=True):
    fields = {
        100: {
            **{name: object() for name in item_batch_use_dialog._ITEM_BATCH_USE_FIELDS},
            "useNum": current,
            "useMax": use_max,
            "itemlo": LuaRef("table", 200),
            "itemvo": LuaRef("table", 300),
            "V_UseParamMax": use_parameter_max,
        },
        200: {"id": magic_invasion.TIANYAN_ITEM_ID},
        300: {"baseId": magic_invasion.TIANYAN_ITEM_ID, "num": owned},
    }
    return SimpleNamespace(
        reader=_Reader(fields),
        binding=SimpleNamespace(pid=11, process_start_ticks=22),
    )


def test_item_batch_runtime_separates_selection_use_cap_inventory_and_track(
    monkeypatch,
) -> None:
    context = _runtime_context(
        current=7,
        use_max=120,
        owned=900,
        use_parameter_max=True,
    )
    monkeypatch.setattr(
        item_batch_use_dialog,
        "active_ui_component_objects",
        lambda _context: (LuaRef("table", 100),),
    )
    monkeypatch.setattr(
        item_batch_use_dialog,
        "read_ui_object_field",
        lambda ctx, address, name: ctx.reader.fields(LuaRef("table", address)).get(name),
    )

    snapshot = item_batch_use_dialog._read_snapshot(
        context,
        expected_item_id=magic_invasion.TIANYAN_ITEM_ID,
    )

    assert snapshot["current"] == 7
    assert snapshot["single_use_maximum"] == 120
    assert snapshot["owned_count"] == 900
    assert snapshot["slider_maximum"] == 120
    assert snapshot["evidence"]["current_field"] == "useNum"
    assert snapshot["evidence"]["owned_count_field"] == "itemvo.num"


def test_item_batch_runtime_uses_inventory_as_track_max_without_parameter_cap(
    monkeypatch,
) -> None:
    context = _runtime_context(use_max=120, owned=900, use_parameter_max=False)
    monkeypatch.setattr(
        item_batch_use_dialog,
        "active_ui_component_objects",
        lambda _context: (LuaRef("table", 100),),
    )
    monkeypatch.setattr(
        item_batch_use_dialog,
        "read_ui_object_field",
        lambda ctx, address, name: ctx.reader.fields(LuaRef("table", address)).get(name),
    )

    snapshot = item_batch_use_dialog._read_snapshot(
        context,
        expected_item_id=magic_invasion.TIANYAN_ITEM_ID,
    )

    assert snapshot["single_use_maximum"] == 120
    assert snapshot["owned_count"] == 900
    assert snapshot["slider_maximum"] == 900


def test_item_batch_runtime_rejects_wrong_item_identity(monkeypatch) -> None:
    context = _runtime_context()
    monkeypatch.setattr(
        item_batch_use_dialog,
        "active_ui_component_objects",
        lambda _context: (LuaRef("table", 100),),
    )
    monkeypatch.setattr(
        item_batch_use_dialog,
        "read_ui_object_field",
        lambda ctx, address, name: ctx.reader.fields(LuaRef("table", address)).get(name),
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="count=0"):
        item_batch_use_dialog._read_snapshot(context, expected_item_id=123)


class _QuantityContext:
    def __init__(self):
        self.waits = []

    def wait_action_settle(self, seconds):
        self.waits.append(seconds)
        if False:
            yield None


def _dialog_snapshot(
    *,
    current,
    use_max=120,
    owned=900,
    slider_max=120,
    pid=11,
    process_start_ticks=22,
):
    return {
        "item_id": magic_invasion.TIANYAN_ITEM_ID,
        "current": current,
        "single_use_maximum": use_max,
        "owned_count": owned,
        "slider_maximum": slider_max,
        "evidence": {
            "pid": pid,
            "process_start_ticks": process_start_ticks,
        },
    }


def test_tianyan_runtime_stable_read_absorbs_transition(monkeypatch) -> None:
    snapshots = iter(
        (
            _dialog_snapshot(current=40),
            _dialog_snapshot(current=401),
            _dialog_snapshot(current=401),
        )
    )
    monkeypatch.setattr(
        magic_invasion,
        "read_item_batch_use_dialog_snapshot",
        lambda **_kwargs: next(snapshots),
    )
    context = _QuantityContext()

    result = _drain(magic_invasion._read_stable_tianyan_use_dialog(context))

    assert result["current"] == 401
    assert context.waits == [0.35, 0.25, 0.25]


def test_tianyan_runtime_stable_read_rejects_continuous_changes(monkeypatch) -> None:
    snapshots = iter(_dialog_snapshot(current=value) for value in range(1, 7))
    monkeypatch.setattr(
        magic_invasion,
        "read_item_batch_use_dialog_snapshot",
        lambda **_kwargs: next(snapshots),
    )

    with pytest.raises(RuntimeError, match=r"观测轨迹=.*'current': 6"):
        _drain(magic_invasion._read_stable_tianyan_use_dialog(_QuantityContext()))


def test_configure_tianyan_uses_runtime_track_max_and_final_runtime_readback(
    monkeypatch,
) -> None:
    snapshots = iter(
        (
            _dialog_snapshot(current=1),
            _dialog_snapshot(current=1),
            _dialog_snapshot(current=100),
            _dialog_snapshot(current=100),
        )
    )
    monkeypatch.setattr(
        magic_invasion,
        "read_item_batch_use_dialog_snapshot",
        lambda **_kwargs: next(snapshots),
    )
    calls = []

    def set_count(_context, _assets, desired, **options):
        calls.append((desired, options))
        if False:
            yield None
        return {"before": 1, "after": desired, "maximum": options["maximum"]}

    monkeypatch.setattr(magic_invasion, "_set_verified_slider_count", set_count)
    context = _QuantityContext()

    result = _drain(magic_invasion._configure_use_quantity(context, quantity=100))

    assert calls[0][0] == 100
    assert calls[0][1]["maximum"] == 120
    assert callable(calls[0][1]["runtime_count_reader"])
    assert result["owned_count"] == 900
    assert result["single_use_maximum"] == 120
    assert result["slider_maximum"] == 120
    assert result["selected_count"] == 100


def test_configure_tianyan_rejects_native_use_cap_before_slider_action(
    monkeypatch,
) -> None:
    snapshots = iter(
        (
            _dialog_snapshot(current=1, use_max=80, owned=900, slider_max=900),
            _dialog_snapshot(current=1, use_max=80, owned=900, slider_max=900),
        )
    )
    monkeypatch.setattr(
        magic_invasion,
        "read_item_batch_use_dialog_snapshot",
        lambda **_kwargs: next(snapshots),
    )
    monkeypatch.setattr(
        magic_invasion,
        "_set_verified_slider_count",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不得调滑轨")),
    )

    with pytest.raises(RuntimeError, match="单次使用上限不足"):
        _drain(magic_invasion._configure_use_quantity(_QuantityContext(), quantity=100))


def test_configure_tianyan_rejects_cross_process_runtime_readback(
    monkeypatch,
) -> None:
    snapshots = iter(
        (
            _dialog_snapshot(current=1, pid=11, process_start_ticks=22),
            _dialog_snapshot(current=1, pid=11, process_start_ticks=22),
            _dialog_snapshot(current=100, pid=12, process_start_ticks=33),
            _dialog_snapshot(current=100, pid=12, process_start_ticks=33),
        )
    )
    monkeypatch.setattr(
        magic_invasion,
        "read_item_batch_use_dialog_snapshot",
        lambda **_kwargs: next(snapshots),
    )

    def set_count(_context, _assets, desired, **_options):
        if False:
            yield None
        return {"before": 1, "after": desired}

    monkeypatch.setattr(magic_invasion, "_set_verified_slider_count", set_count)

    with pytest.raises(RuntimeError, match="游戏进程身份变化"):
        _drain(magic_invasion._configure_use_quantity(_QuantityContext(), quantity=100))
