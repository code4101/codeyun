from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.storage_bag_settings import (
    apply_storage_bag_item_settings,
    delete_storage_bag_item_setting,
    set_storage_bag_auto_claim,
    set_storage_bag_note,
)
from backend.models import FanxiuStorageBagItemSetting
from backend.models import FanxiuStorageBagOpenEvent, FanxiuStorageBagYieldAggregate


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[
        FanxiuStorageBagItemSetting.__table__,
        FanxiuStorageBagOpenEvent.__table__,
        FanxiuStorageBagYieldAggregate.__table__,
    ])
    return Session(engine)


def test_storage_bag_auto_claim_defaults_false_and_persists_by_base_id() -> None:
    with _session() as session:
        atlas = {"items": [{"base_id": 101}, {"base_id": 202}]}
        initial = apply_storage_bag_item_settings(session, atlas)
        assert [row["auto_claim"] for row in initial["items"]] == [False, False]

        set_storage_bag_auto_claim(session, base_id=202, auto_claim=True)
        session.commit()

        reloaded = apply_storage_bag_item_settings(session, atlas)
        assert [row["auto_claim"] for row in reloaded["items"]] == [False, True]


def test_storage_bag_auto_claim_is_idempotent_and_deleted_with_atlas_setting() -> None:
    with _session() as session:
        set_storage_bag_auto_claim(session, base_id=202, auto_claim=True)
        set_storage_bag_auto_claim(session, base_id=202, auto_claim=False)
        session.commit()

        record = session.get(FanxiuStorageBagItemSetting, 202)
        assert record is not None
        assert record.auto_claim is False
        assert delete_storage_bag_item_setting(session, base_id=202) is True
        session.commit()
        assert session.get(FanxiuStorageBagItemSetting, 202) is None


def test_storage_bag_note_defaults_empty_and_persists_by_base_id() -> None:
    with _session() as session:
        atlas = {"items": [{"base_id": 101}, {"base_id": 202}]}
        set_storage_bag_note(session, base_id=202, note="  随机箱，直接开启  ")
        session.commit()

        reloaded = apply_storage_bag_item_settings(session, atlas)
        assert [row["note"] for row in reloaded["items"]] == ["", "随机箱，直接开启"]
        assert [row["auto_claim"] for row in reloaded["items"]] == [False, False]
