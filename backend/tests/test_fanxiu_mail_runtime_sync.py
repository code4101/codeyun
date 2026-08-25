from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import OperationalError
from sqlmodel import SQLModel, Session, create_engine, select

from backend.core.fanxiu.mail import runtime_sync
from backend.core.fanxiu.mail import runtime_store
from backend.core.fanxiu.mail import store as mail_store
from backend.models import FanxiuMailRecord


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _snapshot(*items: dict) -> dict:
    return {
        "ok": True,
        "complete": True,
        "decoded_count": len(items),
        "total": len(items),
        "captured_at": "2026-08-04 15:00:00",
        "items": list(items),
        "evidence": {"pid": 123},
    }


def test_runtime_snapshot_persists_exact_claim_and_attachment_facts(monkeypatch):
    monkeypatch.setattr(runtime_sync, "ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(mail_store, "ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(runtime_sync, "load_fanxiu_mail_envelope_titles", lambda: {61: {"title_plain": "环身·幻灵天火"}})
    session = _session()

    result = runtime_sync.sync_fanxiu_mail_runtime_snapshot(
        session,
        _snapshot(
            {
                "id": "101",
                "type": 61,
                "create_time": 1785838800000,
                "reward_getted": False,
                "locked": True,
                "has_attachment": True,
                "attachment_count": 1,
                "rewards": [{"type": 0, "code": 10001001, "amount": 100}],
            },
            {
                "id": "102",
                "type": 61,
                "create_time": 1785838700000,
                "reward_getted": True,
                "locked": False,
                "has_attachment": True,
                "attachment_count": 1,
                "rewards": [{"type": 0, "code": 10001001, "amount": 100}],
            },
        ),
    )

    rows = {row.mail_id: row for row in session.exec(select(FanxiuMailRecord)).all()}
    assert result == {
        "ok": True,
        "complete": True,
        "source": "runtime_memory",
        "record_count": 2,
        "inserted": 2,
        "updated": 0,
        "absent": 0,
        "captured_at": "2026-08-04 15:00:00",
        "sequence_fingerprint": result["sequence_fingerprint"],
    }
    assert result["sequence_fingerprint"]
    assert rows["101"].runtime_index == 0
    assert rows["102"].runtime_index == 1
    assert rows["101"].runtime_sequence_fingerprint == result["sequence_fingerprint"]

    sequence = runtime_store.current_runtime_mail_sequence(lambda: session.get_bind())
    assert [row.mail_id for row in sequence] == ["101", "102"]
    sequence_snapshot = runtime_store.current_runtime_mail_sequence_snapshot(
        lambda: session.get_bind()
    )
    assert sequence_snapshot["complete"] is True
    assert sequence_snapshot["sequence_fingerprint"] == result["sequence_fingerprint"]
    assert [item["mail_id"] for item in sequence_snapshot["items"]] == ["101", "102"]
    assert rows["101"].status == "锁定"
    assert rows["101"].runtime_status == "unclaimed"
    assert rows["101"].present_in_runtime is True
    assert rows["101"].attachment_count == 1
    assert rows["102"].status == "已领"
    assert rows["102"].runtime_status == "claimed"
    assert rows["102"].reward_getted is True
    assert rows["102"].action_policy == ""


def test_runtime_snapshot_preserves_rich_historical_payload_without_raw_source_metadata(monkeypatch):
    monkeypatch.setattr(runtime_sync, "ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(mail_store, "ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(runtime_sync, "load_fanxiu_mail_envelope_titles", lambda: {2107: {"title_plain": "论道被请离"}})
    session = _session()
    rich_params = {"items": [{"_class": "I18nParam2Name", "value": "对手", "_super": {"key": "NAME"}}]}
    session.add(
        FanxiuMailRecord(
            mail_key="id:rich",
            mail_id="rich",
            mail_type="2107",
            title="论道被请离",
            source="packet",
            payload={
                "mailVo": {"id": "rich", "type": 2107, "i18nParams": rich_params},
                "mail_content_text": "完整正文",
                "packet": {
                    "pcap_name": "real.pcap",
                    "mail_rewards": [{"item_id": "1001", "amount": 2}],
                },
                "seat_eviction_event": {"domain": "lundao", "event": "seat_eviction"},
            },
        )
    )
    session.commit()

    runtime_sync.sync_fanxiu_mail_runtime_snapshot(
        session,
        _snapshot({
            "id": "rich", "type": 2107, "title": "论道被请离",
            "create_time": 1785838800000, "reward_getted": False,
            "locked": False, "has_attachment": False, "rewards": [],
        }),
    )

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "rich")).one()
    assert row.source == "runtime_memory"
    assert row.payload["source_layers"] == ["historical_import", "runtime_memory"]
    assert row.payload["mailVo"]["i18nParams"] == rich_params
    assert row.payload["mail_content_text"] == "完整正文"
    assert "packet" not in row.payload
    assert row.payload["mail_rewards"] == [{"item_id": "1001", "amount": 2}]
    assert row.payload["seat_eviction_event"]["domain"] == "lundao"
    assert row.payload["runtime"]["id"] == "rich"


def test_complete_snapshot_keeps_disappeared_mail_as_inferred_claimed_history(monkeypatch):
    monkeypatch.setattr(runtime_sync, "ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(mail_store, "ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(runtime_sync, "load_fanxiu_mail_envelope_titles", lambda: {})
    session = _session()
    item = {
        "id": "gone",
        "type": 1,
        "create_time": 1785838800000,
        "reward_getted": False,
        "locked": False,
        "has_attachment": True,
        "attachment_count": 1,
        "rewards": [{"code": 1, "amount": 1}],
    }
    runtime_sync.sync_fanxiu_mail_runtime_snapshot(session, _snapshot(item))

    result = runtime_sync.sync_fanxiu_mail_runtime_snapshot(session, _snapshot())
    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "gone")).one()

    assert result["absent"] == 1
    assert row.runtime_status == "claimed_absent"
    assert row.status == "已领"
    assert row.present_in_runtime is False
    assert row.reward_getted is False
    assert row.evidence["runtime_absence_claim"]["inferred"] is True


def test_incomplete_snapshot_never_changes_database(monkeypatch):
    monkeypatch.setattr(runtime_sync, "ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(runtime_sync, "load_fanxiu_mail_envelope_titles", lambda: {})
    session = _session()
    result = runtime_sync.sync_fanxiu_mail_runtime_snapshot(
        session,
        {"ok": False, "complete": False, "reason": "model_not_loaded"},
    )
    assert result["ok"] is False
    assert session.exec(select(FanxiuMailRecord)).all() == []


def test_runtime_sync_reports_read_projection_and_cache_timings(monkeypatch):
    snapshot = {
        "ok": True,
        "complete": True,
        "elapsed_seconds": 8.25,
        "timings": {"snapshot_decode": 7.75},
        "evidence": {"root_cache_hit": True},
    }
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.mail.read_mail_snapshot",
        lambda: snapshot,
    )
    monkeypatch.setattr(
        runtime_sync,
        "sync_fanxiu_mail_runtime_snapshot",
        lambda _session, received: {"ok": received is snapshot},
    )

    result = runtime_sync.sync_fanxiu_mail_from_runtime(object())  # type: ignore[arg-type]

    assert result["ok"] is True
    assert result["runtime_elapsed_seconds"] == 8.25
    assert result["runtime_timings"] == {"snapshot_decode": 7.75}
    assert result["projection_elapsed_seconds"] >= 0
    assert result["elapsed_seconds"] >= result["projection_elapsed_seconds"]
    assert result["root_cache_hit"] is True


def test_runtime_snapshot_retries_same_verified_snapshot_after_sqlite_writer_race(monkeypatch):
    class _Session:
        rollback_count = 0

        def rollback(self):
            self.rollback_count += 1

    session = _Session()
    seen_snapshots = []

    def _once(received_session, received_snapshot):
        seen_snapshots.append((received_session, received_snapshot))
        if len(seen_snapshots) == 1:
            raise OperationalError("INSERT", {}, Exception("database is locked"))
        return {"ok": True}

    monkeypatch.setattr(runtime_sync, "_sync_fanxiu_mail_runtime_snapshot_once", _once)
    monkeypatch.setattr(runtime_sync.time, "sleep", lambda _seconds: None)
    snapshot = _snapshot({"id": "retryable"})

    assert runtime_sync.sync_fanxiu_mail_runtime_snapshot(session, snapshot) == {"ok": True}
    assert session.rollback_count == 1
    assert [received for _, received in seen_snapshots] == [snapshot, snapshot]


def test_directly_claimed_mail_keeps_direct_evidence_after_leaving_list(monkeypatch):
    monkeypatch.setattr(runtime_sync, "ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(mail_store, "ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(runtime_sync, "load_fanxiu_mail_envelope_titles", lambda: {})
    session = _session()
    item = {
        "id": "claimed",
        "type": 1,
        "create_time": 1785838800000,
        "reward_getted": True,
        "locked": False,
        "has_attachment": True,
        "attachment_count": 1,
        "rewards": [{"code": 1, "amount": 1}],
    }
    runtime_sync.sync_fanxiu_mail_runtime_snapshot(session, _snapshot(item))

    runtime_sync.sync_fanxiu_mail_runtime_snapshot(session, _snapshot())
    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "claimed")).one()

    assert row.runtime_status == "claimed"
    assert row.status == "已领"
    assert row.present_in_runtime is False
    assert row.reward_getted is True
    assert row.evidence["runtime_absence_claim"]["inferred"] is False


def test_new_runtime_mail_rewards_are_enriched_from_item_catalog(monkeypatch):
    monkeypatch.setattr(runtime_sync, "ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(mail_store, "ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(runtime_sync, "load_fanxiu_mail_envelope_titles", lambda: {})
    monkeypatch.setattr(
        runtime_sync,
        "_load_mail_item_name_index",
        lambda: {
            "1001": {
                "name": "灵石",
                "type": "货币",
                "source": "item_catalog",
            }
        },
    )
    session = _session()

    runtime_sync.sync_fanxiu_mail_runtime_snapshot(
        session,
        _snapshot(
            {
                "id": "new-mail",
                "title": "香车馈赠",
                "create_time": 1785838800000,
                "reward_getted": False,
                "locked": False,
                "has_attachment": True,
                "attachment_count": 1,
                "rewards": [{"type": 0, "code": 1001, "amount": 153}],
            }
        ),
    )

    row = session.exec(
        select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "new-mail")
    ).one()
    rewards = row.payload["mail_rewards"]
    assert rewards[0]["item_name"] == "灵石"
    assert rewards[0]["name_source"] == "item_catalog"
    assert row.desired_status == "可领"
    assert row.action_policy == "claim"


def test_title_whitelist_never_overrides_faze_lock_in_runtime_snapshot(monkeypatch):
    monkeypatch.setattr(runtime_sync, "ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(mail_store, "ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(runtime_sync, "load_fanxiu_mail_envelope_titles", lambda: {})
    monkeypatch.setattr(
        runtime_sync,
        "_load_mail_item_name_index",
        lambda: {
            "10080012": {
                "name": "魔道法则",
                "type": "法则",
                "source": "item_catalog",
            }
        },
    )
    session = _session()

    runtime_sync.sync_fanxiu_mail_runtime_snapshot(
        session,
        _snapshot(
            {
                "id": "faze-whitelist-mail",
                "title": "魔狱封阵奖励",
                "create_time": 1785838800000,
                "reward_getted": False,
                "locked": False,
                "has_attachment": True,
                "attachment_count": 1,
                "rewards": [{"type": 0, "code": 10080012, "amount": 1}],
            }
        ),
    )

    row = session.exec(
        select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "faze-whitelist-mail")
    ).one()
    assert row.desired_status == "锁定"
    assert row.action_policy == ""


def test_runtime_snapshot_overrides_stale_ui_lock_evidence(monkeypatch):
    monkeypatch.setattr(runtime_sync, "ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(mail_store, "ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(runtime_sync, "load_fanxiu_mail_envelope_titles", lambda: {})
    session = _session()
    item = {
        "id": "ui-locked-mail",
        "title": "未取之宝",
        "create_time": 1785838800000,
        "reward_getted": False,
        "locked": False,
        "has_attachment": True,
        "attachment_count": 1,
        "rewards": [{"type": 0, "code": 1001, "amount": 1}],
    }
    runtime_sync.sync_fanxiu_mail_runtime_snapshot(session, _snapshot(item))
    row = session.exec(
        select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "ui-locked-mail")
    ).one()
    row.locked = True
    row.evidence = {
        **(row.evidence or {}),
        "mail_lock": {"source": "ui_lock_icon", "score": 100},
    }
    session.add(row)
    session.commit()

    runtime_sync.sync_fanxiu_mail_runtime_snapshot(session, _snapshot(item))
    session.refresh(row)

    assert row.locked is False
    assert row.status == "留存"
    assert row.desired_status == "可领"
    assert row.action_policy == "claim"
    assert row.evidence["mail_lock"]["source"] == "ui_lock_icon"
