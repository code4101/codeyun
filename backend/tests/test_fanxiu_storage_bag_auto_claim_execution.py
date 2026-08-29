from __future__ import annotations

import threading

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.data_annotation.tasks import (
    storage_bag_auto_claim_execution as execution,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_plan import (
    StorageBagAutoClaimBlocked,
)
from backend.core.fanxiu.storage_bag_settings import set_storage_bag_auto_claim


def _consume(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def _yielding(value=None):
    yield None
    return value


class _Runtime:
    def __init__(self):
        self.clicks = []

    def goto_view(self, scene_id):
        return _yielding(scene_id)

    def wait_click(self, scene_id, title, **_kwargs):
        self.clicks.append((scene_id, title))
        return _yielding(None)

    def wait_scene(self, scene_id, **_kwargs):
        return _yielding(scene_id)


class _Runner:
    def __init__(self, runtime):
        self.runtime = runtime

    def _fanxiu_runtime(self, *_args, **_kwargs):
        return self.runtime


def _db():
    db_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(db_engine)
    return db_engine


def _runtime_item(base_id=10):
    return {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "fingerprint": "live-1",
        "items": [{
            "ui_index": 0,
            "base_id": base_id,
            "instance_id": "1001",
            "num": 2,
        }],
    }


def _atlas_row(*, base_id=10, name="随机匣", effect="随机获得以下道具", can_use=1):
    return {
        "source": "storage_bag_runtime_atlas",
        "complete": True,
        "items": [{
            "atlas_order": 1,
            "base_id": base_id,
            "num": 2,
            "item": {
                "name": name,
                "type_name": "礼包宝匣",
                "effect_description": effect,
                "can_use": can_use,
            },
        }],
    }


def test_production_bridge_dispatches_a_selected_random_box_via_shared_adapter(monkeypatch):
    db_engine = _db()
    with Session(db_engine) as session:
        set_storage_bag_auto_claim(session, base_id=10, auto_claim=True)
        session.commit()

    atlas = _atlas_row()
    monkeypatch.setattr(execution, "sync_storage_bag_atlas", lambda *_args, **_kwargs: atlas)
    observed = []

    class Adapter:
        def __init__(self, **_kwargs):
            pass

        def execute(self, request):
            observed.append(request)
            return _yielding(object())

    monkeypatch.setattr(execution, "StorageBagRandomBoxGuiAdapter", Adapter)
    monkeypatch.setattr(execution, "StorageBagFixedBoxGuiAdapter", Adapter)
    monkeypatch.setattr(execution, "StorageBagChoiceBoxGuiAdapter", Adapter)
    runtime = _Runtime()
    result = _consume(execution.execute_storage_bag_auto_claim_task(
        _Runner(runtime),
        {},
        {},
        threading.Event(),
        snapshot_reader=_runtime_item,
        catalog_reader=lambda: {},
        session_factory=lambda: Session(db_engine),
    ))

    assert result["ok"] is True
    assert result["executed_count"] == 1
    assert observed[0].base_id == 10
    assert observed[0].instance_id == "1001"
    assert runtime.clicks == [(34, "右侧菜单/储物袋"), (525, "返回")]


def test_unsupported_selected_template_is_deferred_without_any_item_action(monkeypatch):
    db_engine = _db()
    with Session(db_engine) as session:
        set_storage_bag_auto_claim(session, base_id=20, auto_claim=True)
        session.commit()

    atlas = _atlas_row(base_id=20, name="直接使用物", effect="", can_use=1)
    atlas["items"][0]["item"]["type_name"] = "消耗品"
    monkeypatch.setattr(execution, "sync_storage_bag_atlas", lambda *_args, **_kwargs: atlas)
    runtime = _Runtime()
    result = _consume(execution.execute_storage_bag_auto_claim_task(
        _Runner(runtime),
        {},
        {},
        threading.Event(),
        snapshot_reader=lambda: _runtime_item(20),
        catalog_reader=lambda: {},
        session_factory=lambda: Session(db_engine),
    ))

    assert result["ok"] is True
    assert result["executed_count"] == 0
    assert result["deferred_count"] == 1
    assert result["quick_operation_blockers"] == [{
        "base_id": 20,
        "name": "直接使用物",
        "template": "direct_use",
        "quantity": 2,
        "reason": "尚无已完成真实验收的正式生产适配器；本轮失败关闭并保持物品未消费",
    }]
    assert runtime.clicks == [(34, "右侧菜单/储物袋"), (525, "返回")]


def test_special_use_is_deferred_and_blocks_broad_use_quick_operation(monkeypatch):
    db_engine = _db()
    with Session(db_engine) as session:
        set_storage_bag_auto_claim(session, base_id=30, auto_claim=True)
        session.commit()

    atlas = _atlas_row(base_id=30, name="宗门拜师函", effect="", can_use=1)
    atlas["items"][0]["item"]["type_name"] = "消耗品"
    monkeypatch.setattr(execution, "sync_storage_bag_atlas", lambda *_args, **_kwargs: atlas)
    runtime = _Runtime()

    result = _consume(execution.preflight_storage_bag_auto_claim_task(
        _Runner(runtime),
        {},
        {},
        threading.Event(),
        snapshot_reader=lambda: _runtime_item(30),
        catalog_reader=lambda: {},
        session_factory=lambda: Session(db_engine),
    ))

    assert result["ok"] is True
    assert result["action_count"] == 0
    assert result["deferred_count"] == 1
    assert result["quick_operation_allowed"] is False
    assert result["quick_operation_blockers"][0]["template"] == "special_use"
    assert runtime.clicks == [(34, "右侧菜单/储物袋"), (525, "返回")]


def test_spirit_stone_direct_use_is_wired_only_behind_explicit_research_gate(
    monkeypatch,
):
    db_engine = _db()
    with Session(db_engine) as session:
        set_storage_bag_auto_claim(session, base_id=1001, auto_claim=True)
        session.commit()

    atlas = _atlas_row(base_id=1001, name="灵石", effect="", can_use=1)
    atlas["items"][0]["item"]["type_name"] = "货币"
    monkeypatch.setattr(execution, "sync_storage_bag_atlas", lambda *_args, **_kwargs: atlas)
    observed = []

    class Adapter:
        def __init__(self, **_kwargs):
            pass

        def execute(self, request):
            observed.append(request)
            return _yielding(object())

    monkeypatch.setattr(execution, "StorageBagSpiritStoneGuiAdapter", Adapter)
    runtime = _Runtime()
    result = _consume(execution.execute_storage_bag_auto_claim_task(
        _Runner(runtime),
        {},
        {},
        threading.Event(),
        snapshot_reader=lambda: _runtime_item(1001),
        catalog_reader=lambda: {},
        session_factory=lambda: Session(db_engine),
        spirit_stone_direct_use_enabled=True,
    ))

    assert result["ok"] is True
    assert result["executions"] == [{
        "base_id": 1001,
        "instance_id": "1001",
        "template": "direct_use",
        "verified": True,
    }]
    assert observed[0].base_id == 1001
    assert observed[0].name == "灵石"
    assert observed[0].instance_id == "1001"


def test_spirit_stone_direct_use_stays_unconsumed_for_standard_job(monkeypatch):
    db_engine = _db()
    with Session(db_engine) as session:
        set_storage_bag_auto_claim(session, base_id=1001, auto_claim=True)
        session.commit()

    atlas = _atlas_row(base_id=1001, name="灵石", effect="", can_use=1)
    atlas["items"][0]["item"]["type_name"] = "货币"
    monkeypatch.setattr(execution, "sync_storage_bag_atlas", lambda *_args, **_kwargs: atlas)
    runtime = _Runtime()

    result = _consume(execution.execute_storage_bag_auto_claim_task(
        _Runner(runtime),
        {},
        {},
        threading.Event(),
        snapshot_reader=lambda: _runtime_item(1001),
        catalog_reader=lambda: {},
        session_factory=lambda: Session(db_engine),
    ))

    assert result["executed_count"] == 0
    assert result["quick_operation_blockers"][0]["base_id"] == 1001
    assert runtime.clicks == [(34, "右侧菜单/储物袋"), (525, "返回")]


def test_direct_use_gate_does_not_authorize_or_consume_vip_experience(monkeypatch):
    db_engine = _db()
    with Session(db_engine) as session:
        set_storage_bag_auto_claim(session, base_id=1010, auto_claim=True)
        session.commit()

    atlas = _atlas_row(base_id=1010, name="VIP经验", effect="", can_use=1)
    atlas["items"][0]["item"]["type_name"] = "货币"
    monkeypatch.setattr(execution, "sync_storage_bag_atlas", lambda *_args, **_kwargs: atlas)
    runtime = _Runtime()

    result = _consume(execution.preflight_storage_bag_auto_claim_task(
        _Runner(runtime),
        {},
        {},
        threading.Event(),
        snapshot_reader=lambda: _runtime_item(1010),
        catalog_reader=lambda: {},
        session_factory=lambda: Session(db_engine),
        spirit_stone_direct_use_enabled=True,
    ))

    assert result["action_count"] == 0
    assert result["quick_operation_allowed"] is False
    assert result["quick_operation_blockers"][0]["base_id"] == 1010
    assert runtime.clicks == [(34, "右侧菜单/储物袋"), (525, "返回")]


def test_preflight_validates_supported_batch_without_item_action(monkeypatch):
    db_engine = _db()
    with Session(db_engine) as session:
        set_storage_bag_auto_claim(session, base_id=10, auto_claim=True)
        session.commit()

    atlas = _atlas_row()
    monkeypatch.setattr(execution, "sync_storage_bag_atlas", lambda *_args, **_kwargs: atlas)
    runtime = _Runtime()
    result = _consume(execution.preflight_storage_bag_auto_claim_task(
        _Runner(runtime),
        {},
        {},
        threading.Event(),
        snapshot_reader=_runtime_item,
        catalog_reader=lambda: {},
        session_factory=lambda: Session(db_engine),
    ))

    assert result["ok"] is True
    assert result["outcome"] == "ready"
    assert result["action_count"] == 1
    assert runtime.clicks == [(34, "右侧菜单/储物袋"), (525, "返回")]
