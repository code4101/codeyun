from sqlmodel import Session, SQLModel, create_engine, select

from backend.core.fanxiu.activity.exchange_event import upsert_exchange_activity_snapshot
from backend.core.fanxiu.activity.magic_occurrence_identity import repair_magic_occurrence_roots
from backend.models import FanxiuExchangeActivity, FanxiuExchangeShopItem, FanxiuRankingLifecycleCheckpoint


def payload(runtime_id):
    return dict(activity_type="magic-invasion", cross_count=1,
                game_activity_id=1070011, runtime_id=runtime_id,
                instance_key=f"runtime:{runtime_id}:activity:1070011:2026-09-14T10:00:00+08:00:2026-09-14T22:00:00+08:00",
                start_date="2026-09-14", end_date="2026-09-14",
                start_at="2026-09-14T10:00:00+08:00", end_at="2026-09-14T22:00:00+08:00")


def test_runtime_change_is_idempotent_but_interval_and_scope_are_distinct():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        first = upsert_exchange_activity_snapshot(session, payload("1070011400034"))
        assert upsert_exchange_activity_snapshot(session, payload("1070011400004")) == first
        changed = payload("1070011400004")
        changed["cross_count"] = 16
        assert upsert_exchange_activity_snapshot(session, changed) != first
        changed = payload("1070011400004")
        changed.update(start_at="2026-09-15T10:00:00+08:00", end_at="2026-09-15T22:00:00+08:00",
                       start_date="2026-09-15", end_date="2026-09-15")
        assert upsert_exchange_activity_snapshot(session, changed) != first
        assert len(session.exec(select(FanxiuExchangeActivity)).all()) == 3


def test_repair_preserves_shop_and_completed_checkpoint_and_is_repeatable():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for name, status in (("1070011400034", "error"), ("1070011400004", "completed")):
            data = payload(name)
            session.add(FanxiuExchangeActivity(id=name, family="gameplay_rank", **data))
            session.add(FanxiuRankingLifecycleCheckpoint(
                instance_key=data["instance_key"], activity_type="magic-invasion", family="gameplay_rank",
                checkpoint_kind="magic_initialization_0030", business_date="2026-09-14",
                due_at="2026-09-14T00:30:00+08:00", status=status, attempt_count=1))
        session.add(FanxiuExchangeShopItem(activity_id="1070011400004", goods_id=1, item_id=1,
                                         locked=True, purchased_count=3))
        session.commit()
        assert repair_magic_occurrence_roots(session)["deleted_roots"] == 1
        assert len(session.exec(select(FanxiuExchangeActivity)).all()) == 2
        assert repair_magic_occurrence_roots(session, apply=True)["deleted_roots"] == 1
        session.commit()
        root = session.exec(select(FanxiuExchangeActivity)).one()
        item = session.exec(select(FanxiuExchangeShopItem)).one()
        checkpoint = session.exec(select(FanxiuRankingLifecycleCheckpoint)).one()
        assert item.activity_id == root.id and item.locked and item.purchased_count == 3
        assert checkpoint.instance_key == root.instance_key
        assert checkpoint.status == "completed" and checkpoint.attempt_count == 2
        assert repair_magic_occurrence_roots(session, apply=True)["groups"] == []
