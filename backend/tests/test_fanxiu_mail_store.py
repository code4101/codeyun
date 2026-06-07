import os
import json
import time

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from backend.core.fanxiu_tcp_flow import _patch_fanxiu_schema_long_list, _trim_value
from backend.core.fanxiu_tcp_flow import _resolve_fanxiu_message_text_assets
from backend.core.fanxiu_mail_store import (
    mark_fanxiu_mail_action,
    merge_duplicate_fanxiu_mail_records,
    upsert_fanxiu_mail_fact,
)
from backend.core.fanxiu_mail_policy import fanxiu_mail_action_policy_for_rewards
from backend.core import fanxiu_mail_packet_sync
from backend.core import fanxiu_packet_insight_worker
from backend.api import fanxiu as fanxiu_api
from backend.models import FanxiuMailRecord
from scripts.verify_fanxiu_mail_records import _missing_reward_field_names


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_packet_maintenance_can_prioritize_newest_live_pcaps(tmp_path, monkeypatch):
    live_dir = tmp_path / "live-captures"
    live_dir.mkdir()
    old_pcap = live_dir / "old.pcap"
    new_pcap = live_dir / "new.pcap"
    old_pcap.write_bytes(b"\xd4\xc3\xb2\xa1" + b"0" * 32)
    new_pcap.write_bytes(b"\xd4\xc3\xb2\xa1" + b"1" * 32)
    now = time.time()
    os.utime(old_pcap, (now - 100, now - 100))
    os.utime(new_pcap, (now - 50, now - 50))

    monkeypatch.setattr(fanxiu_packet_insight_worker, "resolve_fanxiu_tcp_live_capture_dir", lambda _data_dir=None: live_dir)

    rows = fanxiu_packet_insight_worker._iter_stable_live_pcaps(
        stable_seconds=1,
        max_age_seconds=None,
        newest_first=True,
        now=now,
    )

    assert rows[:2] == [new_pcap, old_pcap]


def test_packet_mail_observation_updates_existing_packet_record():
    session = _session()
    packet, created_packet = upsert_fanxiu_mail_fact(
        session,
        title="分红发放",
        mail_id="24082878061626723",
        mail_type="1101",
        create_time_text="2026年06月05日13:07",
        source="packet",
        status="seen",
        payload={"packet": True},
        evidence={"protocol": "SM_NewMail"},
    )
    packet_again, created_again = upsert_fanxiu_mail_fact(
        session,
        title="分红发放",
        mail_id="24082878061626723",
        mail_type="1101",
        create_time_text="2026年06月05日13:07",
        source="packet",
        status="seen",
        payload={"mail_rewards_summary": "灵石 x200"},
        evidence={"protocol": "SM_NewMail"},
    )
    session.commit()

    rows = session.exec(select(FanxiuMailRecord)).all()
    assert created_packet is True
    assert created_again is False
    assert packet.id == packet_again.id
    assert len(rows) == 1
    assert rows[0].source == "packet"
    assert rows[0].mail_id == "24082878061626723"
    assert rows[0].mail_type == "1101"
    assert rows[0].seen_count == 2
    assert rows[0].payload["packet"]["mail_rewards_summary"] == "灵石 x200"


def test_packet_mail_upsert_creates_id_key():
    session = _session()
    packet, created_packet = upsert_fanxiu_mail_fact(
        session,
        title="仙财福礼",
        mail_id="24082878061626679",
        mail_type="10101",
        create_time_text="2026年06月05日12:00",
        source="packet",
        status="claimed",
        payload={"mail_rewards_summary": "灵石 x1688"},
    )
    session.commit()

    rows = session.exec(select(FanxiuMailRecord)).all()
    assert created_packet is True
    assert len(rows) == 1
    assert rows[0].mail_key == "id:24082878061626679"
    assert rows[0].source == "packet"
    assert rows[0].status == "claimed"
    assert rows[0].payload["mail_rewards_summary"] == "灵石 x1688"


def test_packet_mail_seen_does_not_downgrade_missing_from_list():
    session = _session()
    record, created = upsert_fanxiu_mail_fact(
        session,
        title="天地弈局同盟通知",
        mail_id="mail-missing",
        mail_type="1101",
        create_time_text="2026年06月06日11:30",
        source="packet",
        status="missing_from_list",
        action_policy="",
        payload={"mail_rewards": []},
    )
    again, created_again = upsert_fanxiu_mail_fact(
        session,
        title="天地弈局同盟通知",
        mail_id="mail-missing",
        mail_type="1101",
        create_time_text="2026年06月06日11:30",
        source="packet",
        status="seen",
        action_policy="delete",
        payload={"mail_rewards": []},
    )
    session.commit()

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-missing")).one()
    assert created is True
    assert created_again is False
    assert record.id == again.id
    assert row.status == "missing_from_list"
    assert row.action_policy == "delete"


def test_packet_mail_seen_does_not_downgrade_requested_action():
    session = _session()
    record, created = upsert_fanxiu_mail_fact(
        session,
        title="仙财福礼",
        mail_id="mail-claim-requested",
        mail_type="10101",
        create_time_text="2026年06月07日12:00",
        source="packet",
        status="seen",
        action_policy="claim",
        payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币", "amount": 1688}]},
    )
    assert created is True
    assert mark_fanxiu_mail_action(session, record.mail_key, status="claim_requested")
    again, created_again = upsert_fanxiu_mail_fact(
        session,
        title="仙财福礼",
        mail_id="mail-claim-requested",
        mail_type="10101",
        create_time_text="2026年06月07日12:00",
        source="packet",
        status="seen",
        action_policy="claim",
        payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币", "amount": 1688}]},
    )
    session.commit()

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-claim-requested")).one()
    assert created_again is False
    assert record.id == again.id
    assert row.status == "claim_requested"
    assert mark_fanxiu_mail_action(session, record.mail_key, status="seen")
    session.commit()

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-claim-requested")).one()
    assert row.status == "claim_requested"


def test_packet_mail_reward_claim_beats_followup_delete(monkeypatch, tmp_path):
    session = _session()
    decoded_path = tmp_path / "decoded.json"
    decoded_path.write_text(
        """
{
  "frames": [
    {
      "name": "SM_NewMail",
      "parsed": {
        "mailVo": {
          "id": "mail-claim-cleanup",
          "type": 1101,
          "title": "分红发放",
          "content": "奖励",
          "createTime": 1780636020000,
          "rewardGetted": false,
          "rewards": {
            "items": [
              {"_class": "RewardItem", "type": 0, "code": 1, "amount": 200}
            ]
          }
        }
      }
    },
    {"name": "CM_GetMailReward", "parsed": {"id": "mail-claim-cleanup"}},
    {"name": "SM_GetMailReward", "parsed": {"id": "mail-claim-cleanup"}},
    {"name": "CM_DeleteMail", "parsed": {"id": "mail-claim-cleanup"}},
    {"name": "SM_DeleteMail", "parsed": {"id": "mail-claim-cleanup"}}
  ]
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_iter_fanxiu_tcp_decoded_sources",
        lambda _data_dir=None: [{"decoded_path": decoded_path, "created_at": "2026-06-06 20:00:00"}],
    )
    monkeypatch.setattr(fanxiu_mail_packet_sync, "load_fanxiu_mail_envelope_titles", lambda _export_root=None: {})

    result = fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session)

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-claim-cleanup")).first()
    assert result["action_updated"] == 1
    assert row is not None
    assert row.status == "claimed"
    assert row.payload["mail_rewards"][0]["amount"] == 200


def test_mail_packet_sync_does_not_promote_runtime_action_to_final_status(monkeypatch):
    session = _session()
    upsert_fanxiu_mail_fact(
        session,
        title="修炼值自动领取通知",
        mail_id="mail-evidence-missing",
        mail_type="1101",
        create_time_text="2026年06月05日05:00",
        source="packet",
        status="seen",
        action_policy="delete",
        payload={"mail_rewards": []},
        evidence={"runtime_action": "missing_from_list"},
    )
    upsert_fanxiu_mail_fact(
        session,
        title="灵脉收益",
        mail_id="mail-evidence-claim",
        mail_type="1101",
        create_time_text="2026年06月06日17:44",
        source="packet",
        status="claimed",
        action_policy="claim",
        payload={"mail_rewards": [{"item_name": "玄神灵液"}]},
        evidence={"runtime_action": "claim"},
    )
    upsert_fanxiu_mail_fact(
        session,
        title="仙财福礼",
        mail_id="mail-evidence-requested",
        mail_type="10101",
        create_time_text="2026年06月07日12:00",
        source="packet",
        status="seen",
        action_policy="claim",
        payload={"mail_rewards": [{"item_name": "灵石", "item_type": "货币", "amount": 1688}]},
        evidence={"runtime_requested_action": "claim"},
    )
    session.commit()
    monkeypatch.setattr(fanxiu_mail_packet_sync, "_iter_fanxiu_tcp_decoded_sources", lambda _data_dir=None: [])

    fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session)

    missing = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-evidence-missing")).one()
    claimed = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-evidence-claim")).one()
    requested = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-evidence-requested")).one()
    assert missing.status == "missing_from_list"
    assert missing.action_policy == ""
    assert claimed.status == "seen"
    assert claimed.action_policy == "claim"
    assert requested.status == "claim_requested"
    assert requested.action_policy == ""


def test_packet_mail_upsert_updates_locked_state():
    session = _session()
    record, created = upsert_fanxiu_mail_fact(
        session,
        title="丹道问鼎奖励",
        mail_id="mail-locked",
        mail_type="1014",
        create_time_text="2026年06月04日23:59",
        source="packet",
        status="seen",
        locked=True,
    )
    upsert_fanxiu_mail_fact(
        session,
        title="丹道问鼎奖励",
        mail_id="mail-locked",
        mail_type="1014",
        create_time_text="2026年06月04日23:59",
        source="packet",
        status="seen",
        locked=False,
    )
    session.commit()

    rows = session.exec(select(FanxiuMailRecord)).all()
    assert created is True
    assert record.id == rows[0].id
    assert rows[0].locked is False


def test_mail_packet_sync_reads_mailbox_locks_and_lock_events(monkeypatch, tmp_path):
    session = _session()
    decoded_path = tmp_path / "decoded.json"
    source = {
        "decoded_path": decoded_path,
        "record_id": "record-1",
        "pcap_name": "sample.pcap",
        "created_at": "2026-06-05 13:00:00",
    }
    data = {
        "frames": [
            {
                "name": "SM_MailBox",
                "direction": "s2c",
                "parsed": {
                    "locks": {"items": ["mail-1"]},
                    "mailVos": {
                        "items": [
                            {"id": "mail-1", "type": 1014, "title": "丹道问鼎奖励", "createTime": 1780588740000, "rewardGetted": False},
                            {"id": "mail-2", "type": 1101, "title": "分红发放", "createTime": 1780636020000, "rewardGetted": False},
                        ],
                    },
                },
            },
            {
                "name": "SM_LockMail",
                "direction": "s2c",
                "parsed": {"id": "mail-2", "lock": True},
            },
        ],
    }

    monkeypatch.setattr(fanxiu_mail_packet_sync, "_iter_fanxiu_tcp_decoded_sources", lambda _data_dir=None: [source])
    monkeypatch.setattr(fanxiu_mail_packet_sync, "_load_json_file", lambda _path: data)
    monkeypatch.setattr(fanxiu_mail_packet_sync, "load_fanxiu_mail_envelope_titles", lambda _export_root=None: {})

    result = fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session)
    rows = {row.mail_id: row for row in session.exec(select(FanxiuMailRecord)).all()}

    assert result["inserted"] == 2
    assert result["lock_updated"] == 1
    assert rows["mail-1"].locked is True
    assert rows["mail-2"].locked is True


def test_mail_packet_sync_uses_history_title_when_new_mail_title_is_empty(monkeypatch):
    session = _session()
    upsert_fanxiu_mail_fact(
        session,
        title="仙财福礼",
        mail_id="old-mail",
        mail_type="10101",
        create_time_text="2026年06月06日12:00",
        source="packet",
        status="seen",
    )
    session.commit()

    source = {"decoded_path": "decoded.json", "record_id": "record-1", "pcap_name": "capture.pcap", "created_at": "2026-06-07 12:00:00"}
    data = {
        "frames": [
            {
                "name": "SM_NewMail",
                "direction": "s2c",
                "parsed": {
                    "mailVo": {
                        "id": "new-mail",
                        "type": 10101,
                        "title": "",
                        "content": "",
                        "createTime": 1780824000000,
                        "rewardGetted": False,
                        "rewards": {"items": [{"_class": "RewardItem", "type": 0, "code": 1001, "amount": 1688}]},
                    },
                },
            },
        ],
    }

    monkeypatch.setattr(fanxiu_mail_packet_sync, "_iter_fanxiu_tcp_decoded_sources", lambda _data_dir=None: [source])
    monkeypatch.setattr(fanxiu_mail_packet_sync, "_load_json_file", lambda _path: data)
    monkeypatch.setattr(fanxiu_mail_packet_sync, "load_fanxiu_mail_envelope_titles", lambda _export_root=None: {})

    result = fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session)
    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "new-mail")).one()

    assert result["inserted"] == 1
    assert result["skipped_mail_vo"] == 0
    assert row.title == "仙财福礼"
    assert row.payload["mail_rewards"][0]["item_id"] == "1001"


def test_mail_packet_sync_uses_parsed_envelope_title(tmp_path, monkeypatch):
    session = _session()
    source = {"decoded_path": "decoded.json", "record_id": "record-1", "pcap_name": "capture.pcap", "created_at": "2026-06-07 00:00:12"}
    data = {
        "frames": [
            {
                "name": "SM_NewMail",
                "direction": "s2c",
                "parsed": {
                    "mailVo": {
                        "id": "chess-mail",
                        "type": 1001032,
                        "title": "",
                        "content": "",
                        "createTime": 1780761540000,
                        "rewardGetted": False,
                        "rewards": {"items": [{"_class": "RewardItem", "type": 0, "code": 1001, "amount": 1688}]},
                    },
                },
            },
        ],
    }
    envelope_rows = tmp_path / "parsed_configs" / "Envelope" / "rows.json"
    envelope_rows.parent.mkdir(parents=True)
    envelope_rows.write_text(
        json.dumps([{"id": 1001032, "title": "联盟天地弈局奖励"}], ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(fanxiu_mail_packet_sync, "_iter_fanxiu_tcp_decoded_sources", lambda _data_dir=None: [source])
    monkeypatch.setattr(fanxiu_mail_packet_sync, "_load_json_file", lambda _path: data)

    result = fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session, export_root=tmp_path)
    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "chess-mail")).one()

    assert result["inserted"] == 1
    assert row.title == "联盟天地弈局奖励"
    assert row.normalized_title == "联盟天地弈局奖励"


def test_mail_sync_applies_action_policy_from_packet_attachments(monkeypatch):
    session = _session()
    source = {"decoded_path": "decoded.json", "record_id": "record-1", "pcap_name": "capture.pcap", "created_at": "2026-06-05 17:30:00"}
    data = {
        "frames": [
            {
                "name": "SM_MailBox",
                "direction": "s2c",
                "parsed": {
                    "mailVos": {
                        "items": [
                            {"id": "mail-a", "type": 81200, "title": "香车馈赠", "createTime": 1780397100000, "rewardGetted": False},
                            {"id": "mail-b", "type": 81200, "title": "香车馈赠", "createTime": 1780397100000, "rewardGetted": False},
                            {
                                "id": "mail-c",
                                "type": 4903,
                                "title": "修炼值自动领取通知",
                                "createTime": 1780612200000,
                                "rewardGetted": False,
                                "rewards": {"items": [{"_class": "RewardItem", "type": 0, "code": 37, "amount": 1}]},
                            },
                        ],
                    },
                },
            },
        ],
    }

    monkeypatch.setattr(fanxiu_mail_packet_sync, "_iter_fanxiu_tcp_decoded_sources", lambda _data_dir=None: [source])
    monkeypatch.setattr(fanxiu_mail_packet_sync, "_load_json_file", lambda _path: data)
    monkeypatch.setattr(fanxiu_mail_packet_sync, "load_fanxiu_mail_envelope_titles", lambda _export_root=None: {})

    result = fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session)
    rows = {row.mail_id: row for row in session.exec(select(FanxiuMailRecord)).all()}

    assert result["inserted"] == 3
    assert rows["mail-a"].action_policy == "delete"
    assert rows["mail-b"].action_policy == "delete"
    assert rows["mail-c"].action_policy == "claim"
    assert len([row for row in rows.values() if row.normalized_title == "香车馈赠"]) == 2


def test_mail_sync_reports_real_action_policy_counts(monkeypatch):
    session = _session()
    source = {"decoded_path": "decoded.json", "record_id": "record-1", "pcap_name": "capture.pcap", "created_at": "2026-06-05 17:30:00"}
    data = {
        "frames": [
            {
                "name": "SM_MailBox",
                "direction": "s2c",
                "parsed": {
                    "mailVos": {
                        "items": [
                            {"id": "mail-delete", "type": 81200, "title": "空邮件", "createTime": 1780397100000, "rewardGetted": False},
                            {
                                "id": "mail-claim",
                                "type": 4903,
                                "title": "灵脉收益",
                                "createTime": 1780612200000,
                                "rewardGetted": False,
                                "rewards": {"items": [{"_class": "RewardItem", "type": 0, "code": 37, "amount": 1}]},
                            },
                            {
                                "id": "mail-hold",
                                "type": 4903,
                                "title": "法则奖励",
                                "createTime": 1780615800000,
                                "rewardGetted": False,
                                "rewards": {"items": [{"_class": "RewardItem", "type": 0, "code": 10080012, "amount": 1}]},
                            },
                        ],
                    },
                },
            },
        ],
    }

    monkeypatch.setattr(fanxiu_mail_packet_sync, "_iter_fanxiu_tcp_decoded_sources", lambda _data_dir=None: [source])
    monkeypatch.setattr(fanxiu_mail_packet_sync, "_load_json_file", lambda _path: data)
    monkeypatch.setattr(fanxiu_mail_packet_sync, "load_fanxiu_mail_envelope_titles", lambda _export_root=None: {})
    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_load_mail_item_name_index",
        lambda **_kwargs: {
            "37": {"name": "玄神灵液", "type": "类型未知", "icon": "icon4_item_9310", "source": "test"},
            "10080012": {"name": "魔道法则", "type": "法则", "icon": "icon_item_10080012", "source": "test"},
        },
    )

    result = fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session)

    assert result["claim_policy_count"] == 1
    assert result["delete_policy_count"] == 1


def test_mail_policy_claims_known_safe_rewards():
    assert fanxiu_mail_action_policy_for_rewards([
        {"item_id": "1001", "item_name": "灵石", "item_type": "货币"},
        {"item_id": "37", "item_name": "玄神灵液", "item_type": "类型未知"},
    ]) == "claim"


def test_mail_policy_holds_faze_and_protected_resources():
    assert fanxiu_mail_action_policy_for_rewards([
        {"item_id": "10080012", "item_name": "魔道法则", "item_type": "法则"},
    ]) == ""
    assert fanxiu_mail_action_policy_for_rewards([
        {"item_id": "5030001", "item_name": "淬体精魄", "item_type": "丹药"},
    ]) == ""
    assert fanxiu_mail_action_policy_for_rewards([
        {"item_id": "19070082", "item_name": "炼丹灵草匣", "item_type": "礼包宝匣"},
    ]) == ""
    assert fanxiu_mail_action_policy_for_rewards([
        {"item_id": "7020014", "item_name": "瑶池玉莲", "item_type": "NPC礼物"},
    ]) == ""


def test_mail_policy_claims_four_ke_before_protected_resources_but_after_faze():
    assert fanxiu_mail_action_policy_for_rewards([
        {"item_id": "3080008", "item_name": "潜修心得·四刻", "item_type": "潜修道具"},
    ]) == "claim"
    assert fanxiu_mail_action_policy_for_rewards([
        {"item_id": "3080008", "item_name": "潜修心得·四刻", "item_type": "潜修道具"},
        {"item_id": "5030001", "item_name": "淬体精魄", "item_type": "丹药"},
    ]) == "claim"
    assert fanxiu_mail_action_policy_for_rewards([
        {"item_id": "3080008", "item_name": "潜修心得·四刻", "item_type": "潜修道具"},
        {"item_id": "10080012", "item_name": "魔道法则", "item_type": "法则"},
    ]) == ""


def test_mail_policy_holds_unknown_rewards():
    assert fanxiu_mail_action_policy_for_rewards([
        {"item_id": "999999", "item_name": "未知道具 #999999"},
    ]) == ""


def test_mail_sync_replaces_legacy_title_policy_with_packet_reward_policy(monkeypatch):
    session = _session()
    session.add(
        FanxiuMailRecord(
            mail_key="id:mail-legacy",
            mail_id="mail-legacy",
            title="香车馈赠",
            normalized_title="香车馈赠",
            create_time_text="2026年06月05日17:30",
            source="packet",
            status="seen",
            action_policy="claim",
            payload={"mail_rewards": []},
        )
    )
    session.commit()
    source = {"decoded_path": "decoded.json", "record_id": "record-1", "pcap_name": "capture.pcap", "created_at": "2026-06-05 17:30:00"}
    data = {
        "frames": [
            {
                "name": "SM_NewMail",
                "direction": "s2c",
                "parsed": {
                    "mailVo": {
                        "id": "mail-legacy",
                        "type": 81200,
                        "title": "香车馈赠",
                        "createTime": 1780392600000,
                        "rewardGetted": False,
                        "rewards": {"items": [{"_class": "RewardItem", "type": 0, "code": 37, "amount": 1}]},
                    },
                },
            },
        ],
    }

    monkeypatch.setattr(fanxiu_mail_packet_sync, "_iter_fanxiu_tcp_decoded_sources", lambda _data_dir=None: [source])
    monkeypatch.setattr(fanxiu_mail_packet_sync, "_load_json_file", lambda _path: data)
    monkeypatch.setattr(fanxiu_mail_packet_sync, "load_fanxiu_mail_envelope_titles", lambda _export_root=None: {})

    result = fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session)
    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-legacy")).first()

    assert result["updated"] == 1
    assert row is not None
    assert row.action_policy == "claim"


def test_mail_packet_sync_reports_unknown_mail_protocol_frames(monkeypatch):
    session = _session()
    source = {"decoded_path": "decoded.json", "record_id": "record-1", "pcap_name": "capture.pcap", "created_at": "2026-06-06 20:00:00"}
    data = {
        "frames": [
            {
                "pro_id": 30404,
                "direction": "s2c",
                "parsed": None,
            },
            {
                "pro_id": 30402,
                "name": "SM_MailBox",
                "direction": "s2c",
            },
        ],
    }

    monkeypatch.setattr(fanxiu_mail_packet_sync, "_iter_fanxiu_tcp_decoded_sources", lambda _data_dir=None: [source])
    monkeypatch.setattr(fanxiu_mail_packet_sync, "_load_json_file", lambda _path: data)
    monkeypatch.setattr(fanxiu_mail_packet_sync, "load_fanxiu_mail_envelope_titles", lambda _export_root=None: {})

    result = fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session)

    assert result["unknown_mail_protocol_packets"] == 1
    assert result["unparsed_mail_protocol_packets"] == 1
    assert result["unknown_mail_protocol_samples"][0]["pro_id"] == 30404


def test_mail_decoder_uses_latest_message_text_assets_when_pinned_hash_missing(tmp_path):
    first = tmp_path / "by_source" / "lscripts" / "gamesystem" / "game" / "message_old" / "text_assets"
    latest = tmp_path / "by_source" / "lscripts" / "gamesystem" / "game" / "message_new" / "text_assets"
    first.mkdir(parents=True)
    latest.mkdir(parents=True)
    os.utime(first, (1, 1))
    os.utime(latest, (2, 2))

    resolved = _resolve_fanxiu_message_text_assets(tmp_path)

    assert resolved == latest.resolve()


def test_mail_decoder_patch_reads_i18n_param_number_as_double():
    class FakeInfo:
        ops = [("primitive", "value", "Int"), ("super", "", None)]

    class FakeSchema:
        by_name = {"I18nParam2Num": FakeInfo()}

        def _read_list(self, *_args, **_kwargs):
            return []

    patched = _patch_fanxiu_schema_long_list(FakeSchema())

    assert patched.by_name["I18nParam2Num"].ops[0] == ("primitive", "value", "Double")


def test_mail_decoder_trim_keeps_full_mailvo_business_list():
    trimmed = _trim_value(
        {
            "mailVos": {"_type": "MailVo", "items": [{"id": i, "rewards": {"items": list(range(12))}} for i in range(12)]},
            "other": {"items": list(range(12))},
        }
    )

    assert len(trimmed["mailVos"]["items"]) == 12
    assert "_truncated_items" not in trimmed["mailVos"]
    assert len(trimmed["mailVos"]["items"][0]["rewards"]["items"]) == 8
    assert trimmed["mailVos"]["items"][0]["rewards"]["_truncated_items"] == 4
    assert len(trimmed["other"]["items"]) == 8
    assert trimmed["other"]["_truncated_items"] == 4


def test_mail_packet_sync_extracts_mail_rewards(monkeypatch, tmp_path):
    session = _session()
    decoded_path = tmp_path / "decoded.json"
    decoded_path.write_text(
        """
{
  "frames": [
    {
      "name": "SM_NewMail",
      "direction": "server",
      "parsed": {
        "mailVo": {
          "id": "mail-lingmai",
          "type": 4903,
          "title": "灵脉收益",
          "content": "本次聚灵已结束，以下是道友本次聚灵的收益：",
          "createTime": 1780704000000,
          "rewardGetted": false,
          "rewards": {
            "items": [
              {"_class": "RewardResult", "type": 1, "code": 20001, "amount": 301913},
              {"_class": "RewardItem", "type": 0, "code": 37, "amount": 174828}
            ]
          }
        }
      }
    }
  ]
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_iter_fanxiu_tcp_decoded_sources",
        lambda _data_dir=None: [{"decoded_path": decoded_path, "created_at": "2026-06-06 20:00:00"}],
    )
    monkeypatch.setattr(fanxiu_mail_packet_sync, "load_fanxiu_mail_envelope_titles", lambda _export_root=None: {})
    monkeypatch.setattr(fanxiu_mail_packet_sync, "_fanxiu_config_name", lambda _root, _config, value: "玄神灵液" if int(value) == 20001 else "")
    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_format_fanxiu_reward_item",
        lambda reward, _root: f"道具{reward.get('code')} x{reward.get('amount')}",
    )
    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_load_mail_item_name_index",
        lambda **_kwargs: {"37": {"name": "玄神灵液", "quality": "类型未知", "type": "聚灵收益", "source": "bag_analysis"}},
    )

    result = fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session)

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-lingmai")).first()
    assert result["inserted"] == 1
    assert row is not None
    assert row.payload["mail_rewards"] == [
        {"item_id": "20001", "item_name": "玄神灵液", "amount": 301913, "text": "道具20001 x301913", "type": 1, "class": "RewardResult"},
        {
            "item_id": "37",
            "item_name": "玄神灵液",
            "amount": 174828,
            "text": "道具37 x174828",
            "quality": "类型未知",
            "item_type": "聚灵收益",
            "name_source": "bag_analysis",
            "type": 0,
            "class": "RewardItem",
        },
    ]
    assert row.payload["mail_rewards_summary"] == "玄神灵液 x301913，玄神灵液 x174828"
    assert row.payload["mail_content_text"] == "本次聚灵已结束，以下是道友本次聚灵的收益："


def test_mail_packet_sync_resolves_reward_items_from_static_item_rows(monkeypatch, tmp_path):
    session = _session()
    export_root = tmp_path / "exports"
    item_rows = export_root / "parsed_configs" / "Item"
    quality_rows = export_root / "parsed_configs" / "Quality"
    item_rows.mkdir(parents=True)
    quality_rows.mkdir(parents=True)
    item_rows.joinpath("rows.json").write_text(
        """
[
  {
    "id": 147,
    "name_plain": "灵祖魂息",
    "quality": 5,
    "type": 5,
    "subType": 31,
    "icon": "icon_item_0147",
    "describe_plain": "灵祖挑战中得到的蕴含灵祖精魂的稀有之物"
  }
]
""",
        encoding="utf-8",
    )
    quality_rows.joinpath("rows.json").write_text(
        """
[
  {"id": 5, "name_plain": "金"}
]
""",
        encoding="utf-8",
    )
    decoded_path = tmp_path / "decoded.json"
    decoded_path.write_text(
        """
{
  "frames": [
    {
      "name": "SM_NewMail",
      "parsed": {
        "mailVo": {
          "id": "mail-lingzu",
          "type": 4904,
          "title": "灵祖挑战个人奖励补发",
          "content": "奖励补发",
          "createTime": 1780704000000,
          "rewardGetted": false,
          "rewards": {
            "items": [
              {"_class": "RewardItem", "type": 0, "code": 147, "amount": 3940}
            ]
          }
        }
      }
    }
  ]
}
""",
        encoding="utf-8",
    )
    fanxiu_mail_packet_sync._load_mail_item_name_index_cached.cache_clear()
    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_iter_fanxiu_tcp_decoded_sources",
        lambda _data_dir=None: [{"decoded_path": decoded_path, "created_at": "2026-06-06 20:00:00"}],
    )
    monkeypatch.setattr(fanxiu_mail_packet_sync, "load_fanxiu_mail_envelope_titles", lambda _export_root=None: {})

    result = fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session, export_root=export_root)

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-lingzu")).first()
    assert result["inserted"] == 1
    assert result["item_name_source_counts"]["item_rows"] == 1
    assert row is not None
    assert row.payload["mail_rewards"][0]["item_name"] == "灵祖魂息"
    assert row.payload["mail_rewards"][0]["name_source"] == "item_rows"
    assert row.payload["mail_rewards"][0]["icon"] == "icon_item_0147"
    assert row.payload["mail_rewards"][0]["amount"] == 3940
    assert row.payload["mail_rewards_summary"] == "灵祖魂息 x3940"


def test_mail_packet_sync_uses_attachment_summary_when_body_missing(monkeypatch, tmp_path):
    session = _session()
    decoded_path = tmp_path / "decoded.json"
    decoded_path.write_text(
        """
{
  "frames": [
    {
      "name": "SM_NewMail",
      "parsed": {
        "mailVo": {
          "id": "mail-attachment-only",
          "type": 10101,
          "title": "仙财福礼",
          "content": "",
          "createTime": 1780704000000,
          "rewardGetted": false,
          "rewards": {
            "items": [
              {"_class": "RewardItem", "type": 0, "code": 1001, "amount": 1688}
            ]
          },
          "i18nParams": []
        }
      }
    }
  ]
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_iter_fanxiu_tcp_decoded_sources",
        lambda _data_dir=None: [{"decoded_path": decoded_path, "created_at": "2026-06-06 20:00:00"}],
    )
    monkeypatch.setattr(fanxiu_mail_packet_sync, "load_fanxiu_mail_envelope_titles", lambda _export_root=None: {})
    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_load_mail_item_name_index",
        lambda **_kwargs: {"1001": {"name": "灵石", "icon": "icon_item_0112", "source": "item_catalog"}},
    )

    result = fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session)

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-attachment-only")).first()
    assert result["inserted"] == 1
    assert row is not None
    assert row.payload["mail_rewards_summary"] == "灵石 x1688"
    assert row.payload["mail_content_text"] == "附件：灵石 x1688"


def test_mail_packet_sync_marks_empty_body_and_empty_attachment_notice(monkeypatch, tmp_path):
    session = _session()
    decoded_path = tmp_path / "decoded.json"
    decoded_path.write_text(
        """
{
  "frames": [
    {
      "name": "SM_NewMail",
      "parsed": {
        "mailVo": {
          "id": "mail-empty-notice",
          "type": 7017,
          "title": "秘境封魔杀报名提醒",
          "content": "",
          "createTime": 1780704000000,
          "rewardGetted": false,
          "rewards": [],
          "i18nParams": []
        }
      }
    }
  ]
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_iter_fanxiu_tcp_decoded_sources",
        lambda _data_dir=None: [{"decoded_path": decoded_path, "created_at": "2026-06-06 20:00:00"}],
    )
    monkeypatch.setattr(fanxiu_mail_packet_sync, "load_fanxiu_mail_envelope_titles", lambda _export_root=None: {})

    result = fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session)

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-empty-notice")).first()
    assert result["inserted"] == 1
    assert row is not None
    assert row.payload["mail_content_text"] == "抓包未携带正文或附件；邮件类型：秘境封魔杀报名提醒"


def test_mail_packet_sync_keeps_i18n_params_when_content_template_missing(monkeypatch, tmp_path):
    session = _session()
    decoded_path = tmp_path / "decoded.json"
    decoded_path.write_text(
        """
{
  "frames": [
    {
      "name": "SM_NewMail",
      "parsed": {
        "mailVo": {
          "id": "mail-lingmai-i18n",
          "type": 2201,
          "title": "灵脉收益",
          "content": "",
          "createTime": 1780704000000,
          "i18nParams": {
            "items": [
              {"_class": "I18nParam2Name", "value": "仙煌神脉", "_super": {"key": "NAME"}},
              {"_class": "I18nParam2Num", "value": -33, "_super": {"key": null}},
              {"_type_id": -6513, "_unparsed_at": 71}
            ]
          }
        }
      }
    }
  ]
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_iter_fanxiu_tcp_decoded_sources",
        lambda _data_dir=None: [{"decoded_path": decoded_path, "created_at": "2026-06-06 20:00:00"}],
    )
    monkeypatch.setattr(fanxiu_mail_packet_sync, "load_fanxiu_mail_envelope_titles", lambda _export_root=None: {})

    fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session)

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-lingmai-i18n")).first()
    assert row is not None
    assert row.payload["mail_content_text"] == "所在灵脉：仙煌神脉"


def test_mail_packet_sync_renders_system_message_content_template(monkeypatch, tmp_path):
    session = _session()
    export_root = tmp_path / "exports"
    system_message_dir = export_root / "parsed_configs" / "SystemMessage"
    system_message_dir.mkdir(parents=True)
    system_message_dir.joinpath("rows.json").write_text(
        json.dumps(
            [
                {
                    "id": 13613,
                    "text_plain": "恭喜道友在丹道问鼎中获得第$NUM$名，这是给道友的奖励，望再接再厉再创佳绩！",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    decoded_path = tmp_path / "decoded.json"
    decoded_path.write_text(
        """
{
  "frames": [
    {
      "name": "SM_NewMail",
      "parsed": {
        "mailVo": {
          "id": "mail-post-template",
          "type": 1014,
          "title": "丹道问鼎奖励",
          "content": "",
          "createTime": 1780704000000,
          "i18nParams": {
            "items": [
              {"_class": "I18nParam2Num", "value": 5.0, "_super": {"key": "NUM"}}
            ]
          }
        }
      }
    }
  ]
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_iter_fanxiu_tcp_decoded_sources",
        lambda _data_dir=None: [{"decoded_path": decoded_path, "created_at": "2026-06-06 20:00:00"}],
    )
    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "load_fanxiu_mail_envelope_titles",
        lambda _export_root=None: {1014: {"title_plain": "丹道问鼎奖励", "contentId": 13613}},
    )

    fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session, export_root=export_root)

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-post-template")).first()
    assert row is not None
    assert row.payload["mail_content_text"] == "恭喜道友在丹道问鼎中获得第5名，这是给道友的奖励，望再接再厉再创佳绩！"


def test_mail_packet_sync_cleans_control_chars_in_i18n_param_keys(monkeypatch, tmp_path):
    session = _session()
    decoded_path = tmp_path / "decoded.json"
    decoded_path.write_text(
        """
{
  "frames": [
    {
      "name": "SM_NewMail",
      "parsed": {
        "mailVo": {
          "id": "mail-control-key",
          "type": 1708,
          "title": "社团大比资格",
          "content": "",
          "createTime": 1780704000000,
          "i18nParams": {
            "items": [
              {"_class": "I18nParam2Num", "value": 32.0, "_super": {"key": "\\u0006N"}},
              {"_class": "I18nParam2Num", "value": -43.0, "_super": {"key": null}}
            ]
          }
        }
      }
    }
  ]
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_iter_fanxiu_tcp_decoded_sources",
        lambda _data_dir=None: [{"decoded_path": decoded_path, "created_at": "2026-06-06 20:00:00"}],
    )
    monkeypatch.setattr(fanxiu_mail_packet_sync, "load_fanxiu_mail_envelope_titles", lambda _export_root=None: {})

    fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session)

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-control-key")).first()
    assert row is not None
    assert row.payload["mail_content_text"] == "参数：数值=32；未命名=-43"


def test_mail_packet_sync_renders_known_i18n_content_fallback(monkeypatch, tmp_path):
    session = _session()
    decoded_path = tmp_path / "decoded.json"
    decoded_path.write_text(
        """
{
  "frames": [
    {
      "name": "SM_NewMail",
      "parsed": {
        "mailVo": {
          "id": "mail-auto-practice",
          "type": 4903,
          "title": "修炼值自动领取通知",
          "content": "",
          "createTime": 1780704000000,
          "i18nParams": {
            "items": [
              {"_class": "I18nParam2Name", "value": "试炼手册", "_super": {"key": "NAME"}},
              {"_class": "I18nParam2Num", "value": 360.0, "_super": {"key": "NUM"}}
            ]
          }
        }
      }
    }
  ]
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_iter_fanxiu_tcp_decoded_sources",
        lambda _data_dir=None: [{"decoded_path": decoded_path, "created_at": "2026-06-06 20:00:00"}],
    )
    monkeypatch.setattr(fanxiu_mail_packet_sync, "load_fanxiu_mail_envelope_titles", lambda _export_root=None: {})

    fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session)

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-auto-practice")).first()
    assert row is not None
    assert row.payload["mail_content_text"] == "自动领取：试炼手册 x360"


def test_merge_duplicate_mail_records_removes_weak_shadow_for_packet_record():
    session = _session()
    upsert_fanxiu_mail_fact(
        session,
        title="灵祖挑战个人奖励补发",
        mail_id="24082878061626472",
        mail_type="9702",
        create_time_text="2026年06月05日05:00",
        source="packet",
        status="seen",
    )
    session.add(
        FanxiuMailRecord(
            mail_key="weak:shadow",
            title="灵祖挑战个人奖励补发",
            normalized_title="灵祖挑战个人奖励补发",
            create_time_text="2026年06月05日05:00",
            source="legacy_ocr",
            status="seen",
            seen_count=1,
        )
    )
    session.commit()

    result = merge_duplicate_fanxiu_mail_records(session)
    session.commit()

    rows = session.exec(select(FanxiuMailRecord)).all()
    assert result["deleted"] == 1
    assert len(rows) == 1
    assert rows[0].source == "packet"
    assert rows[0].seen_count == 2


def test_mail_records_endpoint_can_filter_packet_source(monkeypatch):
    session = _session()
    upsert_fanxiu_mail_fact(
        session,
        title="分红发放",
        mail_id="packet-1",
        mail_type="1101",
        create_time_text="2026年06月05日13:07",
        source="packet",
        status="seen",
    )
    session.commit()
    monkeypatch.setattr(fanxiu_api, "ensure_fanxiu_write_permission", lambda *_args, **_kwargs: None)

    response = fanxiu_api.list_fanxiu_mail_records(
        limit=2000,
        offset=0,
        status="",
        action_policy="",
        source="packet",
        current_user=object(),
        session=session,
    )

    assert response.count == 1
    assert response.records[0]["source"] == "packet"


def test_mail_records_endpoint_defaults_to_packet_source_and_sorts_by_mail_time(monkeypatch):
    session = _session()
    packet, _ = upsert_fanxiu_mail_fact(
        session,
        title="分红发放",
        mail_id="packet-1",
        mail_type="1101",
        create_time_text="2026年06月05日13:07",
        source="packet",
        status="seen",
    )
    newer_packet, _ = upsert_fanxiu_mail_fact(
        session,
        title="今日邮件",
        mail_id="packet-2",
        mail_type="1101",
        create_time_text="2026年06月06日20:00",
        source="packet",
        status="seen",
    )
    packet.last_seen_at = 2000
    packet.updated_at = 2000
    newer_packet.last_seen_at = 1000
    newer_packet.updated_at = 1000
    session.add(packet)
    session.add(newer_packet)
    session.commit()
    monkeypatch.setattr(fanxiu_api, "ensure_fanxiu_write_permission", lambda *_args, **_kwargs: None)

    response = fanxiu_api.list_fanxiu_mail_records(
        limit=2000,
        offset=0,
        status="",
        action_policy="",
        current_user=object(),
        session=session,
    )

    assert response.count == 2
    assert response.total == 2
    assert [record["mail_id"] for record in response.records] == ["packet-2", "packet-1"]
    assert response.records[0]["create_time_text"] == "2026年06月06日20:00"


def test_mail_records_endpoint_returns_offset_page_and_total(monkeypatch):
    session = _session()
    for index in range(3):
        upsert_fanxiu_mail_fact(
            session,
            title=f"邮件{index}",
            mail_id=f"packet-{index}",
            mail_type="1101",
            create_time_text=f"2026年06月0{index + 1}日13:07",
            source="packet",
            status="seen",
        )
    session.commit()
    monkeypatch.setattr(fanxiu_api, "ensure_fanxiu_write_permission", lambda *_args, **_kwargs: None)

    response = fanxiu_api.list_fanxiu_mail_records(
        limit=1,
        offset=1,
        status="",
        action_policy="",
        source="packet",
        current_user=object(),
        session=session,
    )

    assert response.count == 1
    assert response.total == 3
    assert response.offset == 1
    assert response.limit == 1
    assert response.records[0]["mail_id"] == "packet-1"


def test_mail_record_verifier_requires_linkable_named_icon_rewards():
    assert _missing_reward_field_names(
        {"item_id": "37", "item_name": "玄神灵液", "icon": "icon4_item_9310"}
    ) == []
    assert _missing_reward_field_names(
        {"item_id": "", "item_name": "玄神灵液", "icon": "icon4_item_9310"}
    ) == ["item_id"]
    assert _missing_reward_field_names(
        {"item_id": "999", "item_name": "未知道具 #999", "icon": "icon_item_0999"}
    ) == ["item_name"]
    assert _missing_reward_field_names(
        {"item_id": "37", "item_name": "玄神灵液"}
    ) == ["icon"]
