from __future__ import annotations

from types import SimpleNamespace

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


def test_box_with_unobserved_reward_inventory_is_deferred_before_open() -> None:
    box = StorageBagAutoClaimEntry(
        base_id=19010195, instance_id="box-1", name="二阶灵环宝匣",
        quantity=12, atlas_order=1, runtime_ui_index=0,
        disposition="action", reason="", template="open_random_box",
    )
    plan = StorageBagAutoClaimPlan(
        runtime_fingerprint="live", selected_base_count=1,
        action_queue=(box,), routed=(), deferred=(), failures=(),
    )

    result = execution._defer_unadapted_production_entries(
        plan,
        cards_by_id={
            "19010195": {"optional_gift_rewards": [{"id": 13010001}]},
            "13010001": {"type": 48, "name": "二阶离火灵环"},
        },
        spirit_stone_direct_use_enabled=False,
    )

    assert result.action_queue == ()
    assert len(result.deferred) == 1
    assert result.deferred[0].base_id == box.base_id
    assert result.deferred[0].disposition == "deferred"
