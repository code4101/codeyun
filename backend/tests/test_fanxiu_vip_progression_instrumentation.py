from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.core.fanxiu.instrumentation import redbag_runtime_loader, vip_progression
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
)


class _Memory:
    pid = 9348
    process_start_ticks = 123


class _Reader:
    def __init__(self, fields: dict[str, dict[object, object]]) -> None:
        self._fields = fields

    def fields(self, value):
        return self._fields.get(value, {})


def test_charge_vip_exp_value_reads_authoritative_model_field(monkeypatch) -> None:
    reader = _Reader(
        {
            "instance": {"Model": "model"},
            "model": {"ChargeData": "data", "vipExp": 3100.0},
            "data": {"vipLevelCfg": "cfg"},
        }
    )
    monkeypatch.setattr(
        vip_progression,
        "manager_index_fields",
        lambda *_args, **_kwargs: {"inst": "instance"},
    )

    assert vip_progression.charge_vip_exp_value(reader, 0xABCD) == 3100


@pytest.mark.parametrize("value", [0, -1, 1.5, None, True])
def test_charge_vip_exp_value_fails_closed_for_unproved_values(
    monkeypatch,
    value,
) -> None:
    reader = _Reader(
        {
            "instance": {"Model": "model"},
            "model": {"ChargeData": "data", "vipExp": value},
            "data": {"vipLevelCfg": "cfg"},
        }
    )
    monkeypatch.setattr(
        vip_progression,
        "manager_index_fields",
        lambda *_args, **_kwargs: {"inst": "instance"},
    )

    with pytest.raises(FanxiuRuntimeMemoryError):
        vip_progression.charge_vip_exp_value(reader, 0xABCD)


def _patch_snapshot_common(monkeypatch) -> None:
    monkeypatch.setattr(
        vip_progression.MumuProcessMemory,
        "discover_cached",
        lambda **kwargs: _Memory(),
    )
    monkeypatch.setattr(vip_progression, "LuaJitReader", lambda _memory: object())
    monkeypatch.setattr(
        redbag_runtime_loader,
        "_lua_addresses",
        lambda _memory: {"state": "0x1234"},
    )
    monkeypatch.setattr(
        vip_progression,
        "_lua_addresses",
        lambda _memory: {"state": "0x1234"},
    )
    monkeypatch.setattr(
        vip_progression,
        "charge_vip_exp_value",
        lambda _reader, _root: 3100,
    )


def test_snapshot_resolves_charge_global_before_marker(monkeypatch) -> None:
    _patch_snapshot_common(monkeypatch)
    marker_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        vip_progression,
        "resolve_lua_global_manager_root",
        lambda *_args, **_kwargs: (0xABCD, True, 0x9999),
    )
    monkeypatch.setattr(
        vip_progression,
        "resolve_manager_root",
        lambda *_args, **kwargs: marker_calls.append(kwargs),
    )

    result = vip_progression.read_charge_vip_exp_snapshot()

    assert result["ok"] is True
    assert result["vip_exp"] == 3100
    assert result["evidence"]["pid"] == 9348
    assert result["evidence"]["process_start_ticks"] == 123
    assert result["evidence"]["root_cache_hit"] is True
    assert result["evidence"]["root_resolver"] == "lua_global"
    assert marker_calls == []


def test_snapshot_uses_only_preheated_marker_cache_by_default(monkeypatch) -> None:
    _patch_snapshot_common(monkeypatch)
    marker_options: list[bool] = []
    monkeypatch.setattr(
        vip_progression,
        "resolve_lua_global_manager_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("global missing")
        ),
    )

    def resolve_marker(*_args, **kwargs):
        marker_options.append(kwargs["allow_discovery"])
        return 0xBCDE, True

    monkeypatch.setattr(vip_progression, "resolve_manager_root", resolve_marker)

    result = vip_progression.read_charge_vip_exp_snapshot()

    assert result["ok"] is True
    assert result["evidence"]["root_resolver"] == "constructor_marker"
    assert result["evidence"]["root_cache_hit"] is True
    assert marker_options == [False]
    assert "marker_discovery" not in result["evidence"]["resolution_path"]


def test_snapshot_marker_discovery_requires_explicit_opt_in(monkeypatch) -> None:
    _patch_snapshot_common(monkeypatch)
    monkeypatch.setattr(
        vip_progression.MumuProcessMemory,
        "discover",
        lambda: _Memory(),
    )
    monkeypatch.setattr(
        vip_progression,
        "resolve_lua_global_manager_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("global missing")
        ),
    )
    marker_options: list[bool] = []

    def resolve_marker(*_args, **kwargs):
        marker_options.append(kwargs["allow_discovery"])
        return 0xBCDE, False

    monkeypatch.setattr(vip_progression, "resolve_manager_root", resolve_marker)

    result = vip_progression.read_charge_vip_exp_snapshot(allow_discovery=True)

    assert result["ok"] is True
    assert marker_options == [True]
    assert "marker_discovery" in result["evidence"]["resolution_path"]


def test_snapshot_returns_typed_incomplete_result(monkeypatch) -> None:
    _patch_snapshot_common(monkeypatch)
    marker_calls: list[object] = []
    monkeypatch.setattr(
        vip_progression,
        "resolve_lua_global_manager_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("not synced", code="data_not_loaded")
        ),
    )
    monkeypatch.setattr(
        vip_progression,
        "resolve_manager_root",
        lambda *_args, **kwargs: marker_calls.append(kwargs),
    )

    result = vip_progression.read_charge_vip_exp_snapshot()

    assert result["ok"] is False
    assert result["complete"] is False
    assert result["vip_exp"] is None
    assert result["reason_code"] == "data_not_loaded"
    assert result["evidence"]["pid"] == 9348
    assert result["evidence"]["process_start_ticks"] == 123
    assert marker_calls == []
