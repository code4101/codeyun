from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.storage_bag_settings import (
    apply_storage_bag_item_settings,
    delete_storage_bag_item_setting,
    set_storage_bag_auto_claim,
    set_storage_bag_note,
    read_storage_bag_auto_claim_item_ids,
)
from backend.models import FanxiuStorageBagItemSetting
from backend.models import FanxiuStorageBagOpenEvent, FanxiuStorageBagYieldAggregate


def test_mail_follow_up_reads_current_selection_and_respects_scheduler_backoff(tmp_path):
    import json
    from datetime import datetime
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_operation import (
        schedule_storage_bag_after_mail_claim,
    )

    path = tmp_path / "scheduler_tasks.json"
    task = {"id": "storage-bag-operation", "task_type": "storage_bag_operation",
            "next_time": "2026-10-11 00:00:00", "last_result": "success"}
    path.write_text(json.dumps([task]), encoding="utf-8")
    claim = {"result": "success", "claimed_reward_item_ids": ["202", "303"]}
    with _session() as session:
        set_storage_bag_auto_claim(session, base_id=202, auto_claim=True)
        set_storage_bag_auto_claim(session, base_id=303, auto_claim=False)
        session.commit()
        assert read_storage_bag_auto_claim_item_ids(session, base_ids=[202, 303, 404]) == [202]
        outcome = schedule_storage_bag_after_mail_claim(
            session, claim, observed_at=datetime(2026, 10, 10, 5, 20), scheduler_state_path=path)
        assert outcome["woke"] is True
        assert outcome["matched_item_ids"] == ["202"]
        assert outcome["next_time"] == "2026-10-10 05:21:00"

        task.update(last_result="error", next_time="2026-10-10 05:30:00")
        path.write_text(json.dumps([task]), encoding="utf-8")
        outcome = schedule_storage_bag_after_mail_claim(
            session, claim, observed_at=datetime(2026, 10, 10, 5, 20), scheduler_state_path=path)
        assert outcome["woke"] is False
        assert json.loads(path.read_text(encoding="utf-8"))[0]["next_time"] == "2026-10-10 05:30:00"

        set_storage_bag_auto_claim(session, base_id=202, auto_claim=False)
        session.commit()
        assert read_storage_bag_auto_claim_item_ids(session, base_ids=[202]) == []
        outcome = schedule_storage_bag_after_mail_claim(
            session, claim, observed_at=datetime(2026, 10, 10, 5, 20), scheduler_state_path=path)
        assert outcome["reason"] == "missing_authorized_selection"


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
