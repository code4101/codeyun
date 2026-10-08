"""A transitioning gift page must not prove a recipient identity."""

from types import SimpleNamespace

import pytest

from backend.core.fanxiu.instrumentation import xianyuan_gift_page as page
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
)


@pytest.mark.parametrize("native_id", [4005, 4013])
def test_recipient_must_match_the_native_person_before_page_is_complete(monkeypatch, native_id):
    panel, person, items = (LuaRef("table", address) for address in (100, 200, 300))
    fields = {
        (100, "_CurNpcId"): 4005,
        (100, "npcInfoVo"): person,
        (100, "_CurSelectItemList"): items,
        (100, "hobbyList"): items,
        (100, "_CurWillAddValue"): 0,
        (100, "_CurFavor"): 4000,
        (200, "id"): native_id,
    }
    ctx = SimpleNamespace(
        reader=SimpleNamespace(indexed_list_items=lambda value: ([], 0)),
        binding=SimpleNamespace(pid=1, process_start_ticks=2),
    )
    monkeypatch.setattr(page, "read_ui_runtime_snapshot", lambda windows, observe, **kw: observe(ctx))
    monkeypatch.setattr(page, "read_active_ui_window_panels", lambda ctx, name: [panel] if name == "NpcGiftGivingView" else [])
    monkeypatch.setattr(page, "read_ui_object_field", lambda ctx, address, name: fields[(address, name)])

    if native_id != 4005:
        with pytest.raises(FanxiuRuntimeMemoryError):
            page.read_xianyuan_gift_page_snapshot()
    else:
        snapshot = page.read_xianyuan_gift_page_snapshot()
        assert snapshot["complete"] is True
        assert snapshot["npc_id"] == snapshot["native_npc_id"] == 4005
        assert snapshot["selected_items"] == []
