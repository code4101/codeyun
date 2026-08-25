from __future__ import annotations

from types import SimpleNamespace

from backend.core.fanxiu.instrumentation import storage_bag_partner
from backend.core.fanxiu.instrumentation.runtime_memory import LuaRef


class _Reader:
    def fields(self, value):
        if value == {"sentinel": "inst"}:
            return {"Model": {"sentinel": "model"}}
        if value == {"sentinel": "model"}:
            return {"PartnerData": {"sentinel": "data"}}
        if value == {"sentinel": "data"}:
            return {"PartnerInfoVoList": {"sentinel": "list"}}
        if value == LuaRef("table", 0x1600):
            return {"level": 230, "stage": 18}
        return value if isinstance(value, dict) else {}

    def list_items(self, value):
        assert value == {"sentinel": "list"}
        return (
            [
                {"id": 16, "partnerVO": LuaRef("table", 0x1600)},
                {"id": 3},
            ],
            2,
        )

    def long(self, value):
        return value if isinstance(value, int) else None


def test_partner_snapshot_reads_loaded_partnermgr_without_lua_execution(monkeypatch) -> None:
    memory = SimpleNamespace(pid=123, process_start_ticks=456)
    reader = _Reader()
    monkeypatch.setattr(
        storage_bag_partner.MumuProcessMemory,
        "discover_cached",
        staticmethod(lambda: memory),
    )
    monkeypatch.setattr(storage_bag_partner, "_lua_addresses", lambda _memory: {"state": "0x10"})
    monkeypatch.setattr(storage_bag_partner, "LuaJitReader", lambda _memory: reader)
    def manager_fields(_reader, _root, methods):
        assert methods == storage_bag_partner.PARTNER_MANAGER_METHODS
        return {"inst": {"sentinel": "inst"}}

    monkeypatch.setattr(storage_bag_partner, "manager_index_fields", manager_fields)

    def resolve(_memory, **options):
        assert options["global_name"] == "PartnerMgr"
        assert options["required_methods"] == storage_bag_partner.PARTNER_MANAGER_METHODS
        options["validate"](reader, 0xABC)
        return 0xABC, False, 0x10

    monkeypatch.setattr(storage_bag_partner, "resolve_lua_global_manager_root", resolve)

    snapshot = storage_bag_partner.read_storage_bag_partner_snapshot()

    assert snapshot["complete"] is True
    assert snapshot["source"] == storage_bag_partner.PARTNER_SNAPSHOT_SOURCE
    assert snapshot["evidence"]["read_only"] is True
    assert snapshot["evidence"]["pid"] == 123
    assert snapshot["partners"] == [
        {"id": 3, "owned": False, "level": None, "stage": None},
        {"id": 16, "owned": True, "level": 230, "stage": 18},
    ]
    assert len(snapshot["fingerprint"]) == 64
