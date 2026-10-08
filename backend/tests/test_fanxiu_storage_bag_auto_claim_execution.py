from __future__ import annotations

from types import SimpleNamespace
import threading
import pytest

from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.data_annotation.tasks import (
    storage_bag_auto_claim_execution as execution,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_plan import (
    StorageBagAutoClaimEntry,
    StorageBagAutoClaimPlan,
)


def _db():
    db_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(db_engine)
    return db_engine


def test_verified_open_projection_retries_sqlite_writer_lock_without_reopening_box(monkeypatch):
    db_engine = _db()
    calls = []
    delays = []

    def record(event, **kwargs):
        calls.append(event)
        if len(calls) < 3:
            raise OperationalError("INSERT", {}, Exception("database is locked"))

    monkeypatch.setattr(execution, "persist_storage_bag_open_receipt", record)
    monkeypatch.setattr(execution.time, "sleep", delays.append)
    verified_event = SimpleNamespace(
        action_key="verified", request=SimpleNamespace(base_id=10),
        operation_template="random_box",
        delta=SimpleNamespace(opened_count=1, rewards=(), before_fingerprint="before", after_fingerprint="after"),
        evidence=lambda: {},
    )

    execution.persist_box_execution_after_verified_open(
        verified_event,
        session_factory=lambda: Session(db_engine),
    )

    assert len(calls) == 3
    assert all(call["action_key"] == "verified" for call in calls)
    assert delays == [0.2, 0.4]


@pytest.mark.parametrize("base_id,template,executable", [
    (19010195, "open_random_box", True),
    (19010277, "open_random_box", True),
    (19010195, "open_fixed_box", False),
    (999001, "open_random_box", False),
])
def test_independent_reward_inventory_requires_an_accepted_box_adapter(
    base_id, template, executable,
) -> None:
    # The two accepted ring boxes use independent equipment-gem receipts.
    # Neither a different template nor another type48 box inherits that gate.
    box = StorageBagAutoClaimEntry(
        base_id=base_id, instance_id="box-1", name="二阶灵环宝匣",
        quantity=12, atlas_order=1, runtime_ui_index=0,
        disposition="action", reason="", template=template,
    )
    plan = StorageBagAutoClaimPlan(
        runtime_fingerprint="live", selected_base_count=1,
        action_queue=(box,), routed=(), deferred=(), failures=(),
    )

    result = execution._defer_unadapted_production_entries(
        plan,
        cards_by_id={
            str(base_id): {"optional_gift_rewards": [{"id": 13010001}]},
            "13010001": {"type": 48, "name": "二阶离火灵环"},
        },
        spirit_stone_direct_use_enabled=False,
    )

    if executable:
        assert result.action_queue == (box,)
        assert result.deferred == ()
        return
    assert result.action_queue == ()
    assert len(result.deferred) == 1
    assert result.deferred[0].base_id == box.base_id
    assert result.deferred[0].disposition == "deferred"


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "2"])
def test_batch_limit_is_rejected_before_receipt_replay_or_gui(monkeypatch, limit):
    def unexpected_io(**kwargs):
        raise AssertionError("invalid request reached receipt replay")
    monkeypatch.setattr(execution, "replay_storage_bag_open_receipts", unexpected_io)
    with pytest.raises(ValueError, match="批量上限"):
        next(execution.execute_storage_bag_auto_claim_task(
            None, {}, {}, threading.Event(), max_actions=limit,
        ))


@pytest.mark.parametrize("limit,expected", [(None, 3), (2, 2), (10, 3)])
def test_batch_contract_distinguishes_partial_from_complete(monkeypatch, limit, expected):
    # Only orchestration boundaries are checked here; GUI acceptance is real-game work.
    called, navigated = [], []
    entries = tuple(StorageBagAutoClaimEntry(
        base_id=i, instance_id=str(i), name=str(i), quantity=1,
        atlas_order=i, runtime_ui_index=i, disposition="action", reason="",
        template="open_random_box",
    ) for i in (11, 12, 13))
    plan = StorageBagAutoClaimPlan(
        runtime_fingerprint="before", selected_base_count=3,
        action_queue=entries, routed=(), deferred=(), failures=(),
    )
    monkeypatch.setattr(execution, "replay_storage_bag_open_receipts", lambda **kw: None)
    monkeypatch.setattr(execution, "_build_validated_production_plan", lambda *a, **kw: ({}, plan))
    monkeypatch.setattr(execution, "storage_bag_balanced_profiles", lambda *a: {})
    monkeypatch.setattr(execution, "sync_storage_bag_atlas", lambda *a, **kw: None)
    class Adapter:
        def __init__(self, **kw): pass
        def invalidate_reusable_snapshot(self): pass
        def execute(self, request):
            called.append(request.base_id)
            yield from ()
            return object()
    for name in ("StorageBagRandomBoxGuiAdapter", "StorageBagFixedBoxGuiAdapter",
                 "StorageBagChoiceBoxGuiAdapter", "StorageBagSpiritStoneGuiAdapter"):
        monkeypatch.setattr(execution, name, Adapter)
    def navigate(scene):
        navigated.append(scene)
        return iter(())
    ctx = SimpleNamespace(current_scene=lambda: iter(()), go_scene=navigate,
                          wait_click=lambda *a, **kw: iter(()),
                          wait_scene=lambda *a, **kw: iter(()))
    runner = SimpleNamespace(_behavior_tree_context=lambda *a, **kw: ctx)
    operation = execution.execute_storage_bag_auto_claim_task(
        runner, {}, {}, threading.Event(), max_actions=limit,
        snapshot_reader=lambda: {"fingerprint": "after"},
    )
    with pytest.raises(StopIteration) as done:
        while True: next(operation)
    result = done.value.value
    assert called == [11, 12, 13][:expected]
    assert result["outcome"] == ("complete" if expected == 3 else "partial")
    assert result["remaining_planned_action_count"] == 3 - expected
    assert result["final_runtime_fingerprint"] == "after"
    assert navigated[0] == execution.WORLD_SCENE
