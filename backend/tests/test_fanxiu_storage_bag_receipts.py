import json

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu import storage_bag_receipts as receipts
from backend.core.fanxiu.storage_bag_usage import read_storage_bag_open_event
from backend.models import FanxiuStorageBagItemSetting, FanxiuStorageBagOpenEvent, FanxiuStorageBagYieldAggregate


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[
        FanxiuStorageBagItemSetting.__table__, FanxiuStorageBagOpenEvent.__table__,
        FanxiuStorageBagYieldAggregate.__table__,
    ])
    with Session(engine) as session:
        session.add(FanxiuStorageBagItemSetting(base_id=10, analysis_status="classified", operation_template="random_box", yield_mode="random"))
        session.commit()
    return lambda: Session(engine)


@pytest.fixture
def receipt():
    return dict(action_key="verified-open", base_id=10, operation_template="random_box",
                opened_count=2, rewards=[dict(item_id=20, name="奖励", quantity=7)],
                runtime_before_fingerprint="before", runtime_after_fingerprint="after", evidence={})


def test_locked_database_preserves_fact_for_later_replay(tmp_path, monkeypatch, database, receipt):
    with monkeypatch.context() as patch:
        def locked(*args, **kwargs):
            raise OperationalError("INSERT", {}, Exception("database is locked"))
        patch.setattr(receipts, "record_storage_bag_open_event", locked)
        with pytest.raises(OperationalError):
            receipts.persist_storage_bag_open_receipt(receipt, session_factory=database, directory=tmp_path)
    document = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert document == {"version": 1, "state": "pending", "receipt": receipt}
    assert receipts.replay_storage_bag_open_receipts(session_factory=database, directory=tmp_path) == 1
    assert receipts.replay_storage_bag_open_receipts(session_factory=database, directory=tmp_path) == 0
    with database() as session:
        assert read_storage_bag_open_event(session, action_key="verified-open")["opened_count"] == 2


def test_commit_before_acknowledgement_is_not_counted_twice(tmp_path, monkeypatch, database, receipt):
    write = receipts._atomic_json
    with monkeypatch.context() as patch:
        def crash(path, document):
            if document["state"] == "committed":
                raise OSError("acknowledgement failed")
            write(path, document)
        patch.setattr(receipts, "_atomic_json", crash)
        with pytest.raises(OSError):
            receipts.persist_storage_bag_open_receipt(receipt, session_factory=database, directory=tmp_path)
    assert receipts.replay_storage_bag_open_receipts(session_factory=database, directory=tmp_path) == 1
    with database() as session:
        aggregate = session.get(FanxiuStorageBagYieldAggregate, 10)
        assert aggregate.opened_count == 2
        assert aggregate.total_rewards[0]["quantity"] == 7


def test_same_identity_with_conflicting_evidence_is_rejected(tmp_path, database, receipt):
    receipts.persist_storage_bag_open_receipt(receipt, session_factory=database, directory=tmp_path)
    with pytest.raises(ValueError, match="不一致"):
        receipts.persist_storage_bag_open_receipt({**receipt, "opened_count": 3}, session_factory=database, directory=tmp_path)


def test_receipt_listing_does_not_create_storage(tmp_path):
    absent = tmp_path / "absent"
    assert receipts.list_storage_bag_open_receipts(directory=absent) == []
    assert not absent.exists()


def test_native_receipt_time_and_process_filter_excludes_old_or_unknown(tmp_path):
    from datetime import datetime
    documents = [
        ('z.json', {'observed_at_after': 1791623881, 'pid': 2688, 'process_start_ticks': 5832}),
        ('a.json', {'wallet_captured_at': '2026-10-10T17:20:09+08:00', 'process_identity': [2688, 5832]}),
        ('y.json', {'wallet_captured_at': '2026-10-10T02:49:09+08:00', 'process_identity': [2688, 5832]}),
        ('b.json', {'wallet_captured_at': '2026-10-10T17:20:09', 'process_identity': [2688, 5832]}),
        ('c.json', {'observed_at_after': 1791623881, 'pid': 2688, 'process_start_ticks': 9999}),
        ('d.json', {'observed_at_after': float('nan'), 'pid': 2688, 'process_start_ticks': 5832}),
    ]
    for name, evidence in documents:
        (tmp_path / name).write_text(json.dumps({'receipt': {'action_key': name, 'evidence': evidence}}), encoding='utf-8')
    rows = receipts.list_storage_bag_open_receipts(
        directory=tmp_path, observed_after=datetime.fromisoformat('2026-10-10T17:17:01+08:00'),
        process_identity=(2688, 5832))
    assert [r['receipt']['action_key'] for r in rows] == ['z.json', 'a.json']
    with pytest.raises(ValueError):
        receipts.list_storage_bag_open_receipts(directory=tmp_path, observed_after=datetime(2026, 10, 10))


def test_direct_use_receipt_replays_only_verified_wallet_fact(tmp_path, database):
    from dataclasses import replace
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_direct_use import (
        StorageBagDirectUseExecution, StorageBagDirectUseRequest, StorageBagDirectUseBlocked,
    )
    execution = StorageBagDirectUseExecution(
        request=StorageBagDirectUseRequest(1001, "stone-instance", "灵石", 7),
        before_fingerprint="bag-before", after_fingerprint="bag-after",
        wallet_before=100, wallet_after=107, detail_observed_name="灵石",
        quantity_observed_name="灵石", scroll_count=0,
        process_identity=(123, 456), wallet_captured_at="2026-10-07T22:37:00+08:00",
    )
    with database() as session:
        session.add(FanxiuStorageBagItemSetting(base_id=1001,
            analysis_status="classified", operation_template="direct_use", yield_mode="fixed"))
        session.commit()
    fact = execution.receipt()
    for _ in range(2):
        receipts.persist_storage_bag_open_receipt(fact, session_factory=database, directory=tmp_path)
    assert receipts.list_storage_bag_open_receipts(directory=tmp_path)[0]["state"] == "committed"
    with database() as session:
        aggregate = session.get(FanxiuStorageBagYieldAggregate, 1001)
        assert aggregate.opened_count == 7
        assert aggregate.total_rewards == [dict(item_id=None, name="灵石", reward_key="wallet:1", quantity=7)]
    assert replace(execution, wallet_captured_at="2026-10-08T22:37:00+08:00").receipt()["action_key"] != fact["action_key"]
    with pytest.raises(StorageBagDirectUseBlocked):
        replace(execution, wallet_after=108).receipt()
    with pytest.raises(StorageBagDirectUseBlocked):
        replace(execution, process_identity=(0, 0)).receipt()


def test_spirit_stone_research_requires_exact_declared_consumption_scope():
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_execution import (
        parse_spirit_stone_research_request,
    )
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_plan import StorageBagAutoClaimBlocked
    scope = dict(base_id=1001, instance_id="stone", quantity=57, maximum_quantity=57)
    request = parse_spirit_stone_research_request(scope)
    assert (request.base_id, request.instance_id, request.quantity) == (1001, "stone", 57)
    for invalid in ({}, {**scope, "base_id": 1010}, {**scope, "maximum_quantity": 56},
                    {**scope, "quantity": True}, {**scope, "instance_id": " "},
                    {**scope, "quantity": 0}, {**scope, "quantity": "57"}):
        with pytest.raises(StorageBagAutoClaimBlocked):
            parse_spirit_stone_research_request(invalid)
