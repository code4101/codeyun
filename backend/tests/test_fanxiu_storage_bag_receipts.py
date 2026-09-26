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
