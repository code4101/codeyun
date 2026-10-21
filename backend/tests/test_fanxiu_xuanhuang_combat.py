from types import SimpleNamespace

from backend.core.fanxiu.instrumentation import xuanhuang_combat as combat
from backend.core.fanxiu.instrumentation.runtime_memory import LuaRef, as_int


def fixture_context(monkeypatch, *, enable=False, monster_table=True, count=0):
    tables = {}

    def ref(fields):
        address = len(tables) + 1
        tables[address] = fields
        return LuaRef("table", address)

    position = ref({"x": 0, "y": 0, "z": 0})
    monsters = ref({i: ref({"Entity": ref({"V_ID": 100+i,
        "V_GridPosition": ref({"x": 3, "y": 0, "z": 4})})}) for i in range(count)})
    user = ref({"Entity": ref({"V_ID": 123, "V_GridPosition": position}),
                "SkillActor": ref({"AutoFight": ref({"enable": enable, "status": 4})})})
    managers = {"EntityMgr": {"inst": ref({"UserView": user,
        **({"MonsterViewDic": monsters} if monster_table else {})})},
        "SceneMgr": {"inst": ref({"V_CurrentMapId": 10012})},
        "FightMgr": {"inst": ref({})}}
    reader = SimpleNamespace(fields=lambda value: tables[value.address], long=as_int)
    ctx = SimpleNamespace(reader=reader, binding=SimpleNamespace(pid=5, process_start_ticks=6),
                          loaded_manager_fields=lambda name, methods: managers[name])
    monkeypatch.setattr(combat, "acquire_lua_global_runtime_context", lambda keys: ctx)
    return managers


def test_disabled_auto_and_empty_loaded_monsters_are_known(monkeypatch):
    fixture_context(monkeypatch)
    result = combat.read_xuanhuang_combat_snapshot()
    assert result["complete"] and result["monsters"] == []
    assert result["auto_fight"]["enable"] is False
    assert result["auto_status"] == 4 and result["target_id"] is None
    assert result["auto_fight"]["InSceneLoading"] is None


def test_absent_monster_table_and_non_boolean_enable_remain_unknown(monkeypatch):
    fixture_context(monkeypatch, monster_table=False, enable=1)
    result = combat.read_xuanhuang_combat_snapshot()
    assert not result["complete"] and result["monsters"] is None
    assert result["auto_fight"]["enable"] is None
    assert set(result["missing"]) == {"monsters", "auto_fight.enable"}


def test_monster_projection_is_bounded_and_preserves_unknown_gates(monkeypatch):
    fixture_context(monkeypatch, count=34)
    result = combat.read_xuanhuang_combat_snapshot()
    assert len(result["monsters"]) == 32 and result["monsters_truncated"]
    assert result["monsters"][0]["distance"] == 5
    assert result["monsters"][0]["can_select"] is None
    assert result["monsters"][0]["loaded"] is None


def test_runtime_read_failure_cannot_report_complete(monkeypatch):
    def fail(keys):
        raise RuntimeError("memory unavailable")
    monkeypatch.setattr(combat, "acquire_lua_global_runtime_context", fail)
    result = combat.read_xuanhuang_combat_snapshot()
    assert result["read_only"] and not result["complete"]
    assert "memory unavailable" in result["reason"]
