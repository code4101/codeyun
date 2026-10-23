"""Language joins and process-bound caches, without simulating game actions."""
import json
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.instrumentation import lilian_event as module
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError


def test_live_name_replaces_stale_export_and_preserves_condition_identity():
    partner = {"id": 9, "name_key": 13599, "name": "一千零七十三",
               "npc_id": 1015, "sex": 2, "career": 4}
    partners = module.resolve_lilian_partner_names([partner], {
        "complete": True, "source": "LuaLocalization.LangTable",
        "texts_by_id": {13599: "银月", 9: "错误的直接ID映射"},
    })
    assert partners == [{**partner, "name": "银月"}]
    selected = module.select_lilian_condition_partner(
        {"complete": True, "partners": partners}, "IncludeXianLv|9")
    assert selected["name"] == "银月"
    assert selected["npc_id"] == 1015
    assert partner["name"] == "一千零七十三"


@pytest.mark.parametrize("change", [
    {"complete": False}, {"source": "exported_lang.lua"},
    {"texts_by_id": {}}, {"texts_by_id": {13599: " "}},
    {"texts_by_id": {13599: 13599}},
])
def test_missing_or_foreign_localization_cannot_fall_back_to_old_name(change):
    localization = {"complete": True, "source": "LuaLocalization.LangTable",
                    "texts_by_id": {13599: "银月"}, **change}
    with pytest.raises(FanxiuRuntimeMemoryError):
        module.resolve_lilian_partner_names(
            [{"id": 9, "name_key": 13599, "name": "一千零七十三"}], localization)


@pytest.mark.parametrize("change,accepted", [
    ({}, True), ({"schema_version": None}, False),
    ({"pid": 42}, False), ({"process_start_ticks": 8}, False),
    ({"snapshot": {"complete": True, "evidence": {"lang_path": "old.lua"}}}, False),
    ({"snapshot": {"complete": False}}, False),
])
def test_snapshot_cache_requires_current_contract_and_process(tmp_path, monkeypatch, change, accepted):
    path = tmp_path / "partners.json"
    payload = {"schema_version": 2, "pid": 41, "process_start_ticks": 7,
               "updated_at": 1, "snapshot": {"complete": True,
               "evidence": {"localization_source": "LuaLocalization.LangTable"}}, **change}
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(module, "_lilian_partner_snapshot_cache_path", lambda: path)
    result = module._read_lilian_partner_snapshot_cache(
        SimpleNamespace(pid=41, process_start_ticks=7))
    assert (result is not None) is accepted
