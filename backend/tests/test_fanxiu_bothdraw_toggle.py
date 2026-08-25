from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.core.fanxiu.instrumentation import bothdraw_toggle as target


class _Context:
    def __init__(self, fields):
        self.memory = SimpleNamespace(pid=1, process_start_ticks=2)
        self.fields = fields

    def field(self, address, name):
        return self.fields[(address, name)]


class _TicketReader:
    def __init__(self, arrays):
        self._arrays = arrays

    def table(self, address):
        return {"array": self._arrays[address]}


def test_snapshot_reads_authoritative_active_panel_boolean(monkeypatch) -> None:
    context = _Context(
        {
            (100, "V_ActivityId"): 101,
            (100, "V_UseTenTimes"): True,
            (100, "toggle"): SimpleNamespace(kind="table", address=200),
            (100, "V_BothDrawPlayVO"): SimpleNamespace(kind="table", address=300),
            (300, "times"): 0,
            (300, "hitBigTotal"): 0,
        }
    )
    monkeypatch.setattr(target, "table_ref", lambda value: value)
    monkeypatch.setattr(target, "_active_bothdraw_panel", lambda _context: (100, 0))

    result = target._snapshot(context, expected_activity_id=101)

    assert result["ten_draw_enabled"] is True
    assert result["batch_size"] == 10


def test_snapshot_rejects_non_boolean_toggle_mirror(monkeypatch) -> None:
    context = _Context(
        {
            (100, "V_ActivityId"): 101,
            (100, "V_UseTenTimes"): "TRUE",
            (100, "toggle"): SimpleNamespace(kind="table", address=200),
            (100, "V_BothDrawPlayVO"): SimpleNamespace(kind="table", address=300),
            (300, "times"): 0,
            (300, "hitBigTotal"): 0,
        }
    )
    monkeypatch.setattr(target, "table_ref", lambda value: value)
    monkeypatch.setattr(target, "_active_bothdraw_panel", lambda _context: (100, 0))
    monkeypatch.setattr(
        target,
        "read_ui_runtime_snapshot",
        lambda _keys, reader_fn: reader_fn(context),
    )

    result = target.read_bothdraw_ten_draw_runtime(expected_activity_id=101)

    assert result["complete"] is False


def test_active_panel_finds_a_deep_active_ui_child_once(monkeypatch) -> None:
    context = _Context({})
    component = SimpleNamespace(address=100)
    panel = SimpleNamespace(kind="table", address=200)
    play = SimpleNamespace(kind="table", address=300)
    toggle = SimpleNamespace(kind="table", address=400)
    values = {
        (100, "m_panel"): panel,
        (100, "V_ActivityId"): None,
        (100, "V_BothDrawPlayVO"): None,
        (100, "toggle"): None,
        (100, "V_UseTenTimes"): None,
        (200, "V_ActivityId"): 3001003,
        (200, "V_BothDrawPlayVO"): play,
        (200, "toggle"): toggle,
        (200, "V_UseTenTimes"): True,
    }
    monkeypatch.setattr(target, "_direct_active_bothdraw_panels", lambda _ctx: [])
    monkeypatch.setattr(target, "active_ui_component_objects", lambda _ctx, **_kwargs: (component,))
    monkeypatch.setattr(target, "read_ui_object_field", lambda _ctx, address, name: values.get((address, name)))
    monkeypatch.setattr(target, "table_ref", lambda value: value)

    assert target._active_bothdraw_panel(context) == (200, None)


def test_active_panel_rejects_two_distinct_deep_bothdraw_panels(monkeypatch) -> None:
    context = _Context({})
    components = (SimpleNamespace(address=100), SimpleNamespace(address=101))
    values = {
        (100, "m_panel"): None,
        (101, "m_panel"): None,
    }
    for address in (100, 101):
        values.update(
            {
                (address, "V_ActivityId"): 3001003,
                (address, "V_BothDrawPlayVO"): SimpleNamespace(kind="table", address=address + 200),
                (address, "toggle"): SimpleNamespace(kind="table", address=address + 300),
                (address, "V_UseTenTimes"): False,
            }
        )
    monkeypatch.setattr(target, "_direct_active_bothdraw_panels", lambda _ctx: [])
    monkeypatch.setattr(target, "active_ui_component_objects", lambda _ctx, **_kwargs: components)
    monkeypatch.setattr(target, "read_ui_object_field", lambda _ctx, address, name: values.get((address, name)))
    monkeypatch.setattr(target, "table_ref", lambda value: value)

    with pytest.raises(target.FanxiuRuntimeMemoryError, match="Ambiguous"):
        target._active_bothdraw_panel(context)


def test_text_component_locators_require_distinct_complete_identities(monkeypatch) -> None:
    context = _Context({})
    values = {(100, "V_ActivityId"): 3001003}
    for offset, name in enumerate(("costTF", "costTF2", "numTF"), start=1):
        wrapper = SimpleNamespace(kind="table", address=200 + offset)
        values[(100, name)] = wrapper
        values[(wrapper.address, "FatherId")] = 10
        values[(wrapper.address, "ComponentId")] = offset
        values[(wrapper.address, "OrginComponentId")] = 100 + offset
    monkeypatch.setattr(target, "_active_bothdraw_panel", lambda _ctx: (100, 0))
    monkeypatch.setattr(target, "read_ui_object_field", lambda _ctx, address, name: values.get((address, name)))
    monkeypatch.setattr(target, "table_ref", lambda value: value)

    snapshot = target._label_locator_snapshot(context, expected_activity_id=3001003)

    assert snapshot["components"]["costTF"]["ComponentId"] == 1
    assert snapshot["components"]["numTF"]["ComponentId"] == 3


def test_ticket_binding_reads_primary_and_bound_runtime_config_rows(monkeypatch) -> None:
    context = _Context({})
    context.reader = _TicketReader({201: (None, 29726), 202: (None, 29727)})
    values = {
        (100, "V_ActivityId"): 3001003,
        (100, "V_CostResourceType"): 29726,
        (100, "V_CostNum"): 1,
        (100, "V_CostItemId"): 29726,
        (100, "V_CostItemlo"): SimpleNamespace(kind="table", address=201),
        (100, "V_CostItemlo2"): SimpleNamespace(kind="table", address=202),
    }
    monkeypatch.setattr(target, "_active_bothdraw_panel", lambda _ctx: (100, 0))
    monkeypatch.setattr(
        target,
        "read_ui_object_field",
        lambda _ctx, address, name: values.get((address, name)),
    )
    monkeypatch.setattr(target, "table_ref", lambda value: value)

    snapshot = target._ticket_binding_snapshot(context, expected_activity_id=3001003)

    assert snapshot["primary_resource_id"] == 29726
    assert snapshot["bound_replacement_item_id"] == 29727
    assert snapshot["cost_per_draw"] == 1


def test_ticket_binding_rejects_unproven_or_same_replacement_item(monkeypatch) -> None:
    context = _Context({})
    context.reader = _TicketReader({201: (None, 29726), 202: (None, 29726)})
    values = {
        (100, "V_ActivityId"): 3001003,
        (100, "V_CostResourceType"): 29726,
        (100, "V_CostNum"): 1,
        (100, "V_CostItemId"): 29726,
        (100, "V_CostItemlo"): SimpleNamespace(kind="table", address=201),
        (100, "V_CostItemlo2"): SimpleNamespace(kind="table", address=202),
    }
    monkeypatch.setattr(target, "_active_bothdraw_panel", lambda _ctx: (100, 0))
    monkeypatch.setattr(target, "read_ui_object_field", lambda _ctx, address, name: values.get((address, name)))
    monkeypatch.setattr(target, "table_ref", lambda value: value)

    with pytest.raises(target.FanxiuRuntimeMemoryError, match="不得与主券同 id"):
        target._ticket_binding_snapshot(context, expected_activity_id=3001003)
