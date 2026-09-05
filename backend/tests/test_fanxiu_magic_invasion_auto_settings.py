from __future__ import annotations

import pytest
from types import SimpleNamespace

from backend.core.fanxiu.instrumentation import magic_invasion_auto_settings as runtime
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
)


class _Reader:
    def __init__(self, lists: dict[int, list[int]]) -> None:
        self.lists = lists

    def list_items(self, value: LuaRef):
        items = self.lists[value.address]
        return list(items), len(items)


def _panel_fields(**overrides):
    values = {
        "SliderNum": LuaRef("table", 0x10),
        "SureBtn": LuaRef("table", 0x11),
        "selectBtn6": LuaRef("table", 0x12),
        "selectList": LuaRef("table", 0x20),
        "selectedFourTimesList": LuaRef("table", 0x21),
        "selectedSpecialList": LuaRef("table", 0x22),
        "autoTimes": 37,
        "useNum": 37,
        "useMax": 120,
    }
    values.update(overrides)
    return lambda _address, name: values.get(name)


def test_decode_active_auto_settings_exposes_all_business_switches() -> None:
    result = runtime._decode_settings_panel(
        _Reader(
            {
                0x20: [4, 5, 6, 8, 9, 10, 11],
                0x21: [4, 5, 11],
                0x22: [4, 5, 11],
            }
        ),
        0x100,
        field=_panel_fields(),
    )

    assert result["qualities"] == {
        "1": {
            "type_id": 1,
            "name": "堂主",
            "selected": False,
            "fourfold_merit": False,
            "pursuit_chain": False,
            "augment_configurable": False,
        },
        "2": {
            "type_id": 2,
            "name": "长老",
            "selected": False,
            "fourfold_merit": False,
            "pursuit_chain": False,
            "augment_configurable": True,
        },
        "3": {
            "type_id": 3,
            "name": "宗主",
            "selected": False,
            "fourfold_merit": False,
            "pursuit_chain": False,
            "augment_configurable": True,
        },
        "4": {
            "type_id": 4,
            "name": "太上长老",
            "selected": True,
            "fourfold_merit": True,
            "pursuit_chain": True,
            "augment_configurable": True,
        },
        "5": {
            "type_id": 5,
            "name": "天外魔神",
            "selected": True,
            "fourfold_merit": True,
            "pursuit_chain": True,
            "augment_configurable": True,
        },
        "11": {
            "type_id": 11,
            "name": "圣主",
            "selected": True,
            "fourfold_merit": True,
            "pursuit_chain": True,
            "augment_configurable": True,
        },
    }
    assert result["options"] == {
        "auto_use_demon_token": True,
        "ten_banish": False,
        "skip_animation": True,
        "quick_banish": True,
    }
    assert result["times"] == {"selected": 37, "maximum": 120}
    assert result["hidden_targets"] == {
        "10": {
            "type_id": 10,
            "name": "武始",
            "selected": True,
            "fourfold_merit": False,
            "pursuit_chain": False,
        },
    }
    assert result["auto_exorcism_choices"] == {
        "quality_hall_master": False,
        "quality_elder": False,
        "elder_quadruple_merit": False,
        "elder_chase_chain": False,
        "quality_sect_master": False,
        "sect_master_quadruple_merit": False,
        "sect_master_chase_chain": False,
        "quality_supreme_elder": True,
        "supreme_elder_quadruple_merit": True,
        "supreme_elder_chase_chain": True,
        "quality_extraterrestrial_demon": True,
        "extraterrestrial_demon_quadruple_merit": True,
        "extraterrestrial_demon_chase_chain": True,
        "quality_saint_master": True,
        "saint_master_quadruple_merit": True,
        "saint_master_chase_chain": True,
        "auto_use_exorcism_order": True,
        "skip_animation": True,
        "fast_exorcism": True,
        "tenfold_exorcism": False,
    }


def test_decode_auto_settings_fails_closed_on_unknown_type() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match="无效类型") as exc_info:
        runtime._decode_settings_panel(
            _Reader({0x20: [4, 99], 0x21: [4], 0x22: [4]}),
            0x100,
            field=_panel_fields(),
        )

    assert exc_info.value.code == "schema_mismatch"


def test_decode_auto_settings_fails_closed_on_incoherent_count() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match="次数状态不一致"):
        runtime._decode_settings_panel(
            _Reader({0x20: [4], 0x21: [4], 0x22: [4]}),
            0x100,
            field=_panel_fields(autoTimes=36),
        )


def test_snapshot_exposes_exact_active_panel_identity(monkeypatch) -> None:
    context = SimpleNamespace(
        reader=_Reader({0x20: [4], 0x21: [4], 0x22: [4]}),
        memory=SimpleNamespace(pid=7, process_start_ticks=11),
        cache_mode="layered",
        timings={},
    )
    fields = _panel_fields(autoTimes=1, useNum=1)
    monkeypatch.setattr(
        runtime,
        "active_ui_component_objects",
        lambda _context: [SimpleNamespace(address=0x698)],
    )
    monkeypatch.setattr(
        runtime,
        "read_ui_object_field",
        lambda _context, address, name: fields(address, name),
    )

    result = runtime._snapshot(context)

    assert result["evidence"] == {
        "pid": 7,
        "process_start_ticks": 11,
        "panel_address": 0x698,
        "active_membership": "UIShowMgr.V_M_compDic",
        "read_only": True,
        "lua_methods_invoked": False,
    }
