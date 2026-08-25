import os
import json
import time
from pathlib import Path

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from backend.core.fanxiu.history_museum.packet_capture.tcp_flow import _patch_fanxiu_schema_long_list, _trim_value
from backend.core.fanxiu.history_museum.packet_capture.tcp_flow import _resolve_fanxiu_message_text_assets
from backend.core.fanxiu.history_museum.packet_capture import tcp_flow as fanxiu_tcp_flow
from backend.core.fanxiu.mail.store import (
    align_fanxiu_mail_records_claimable_between_times,
    mark_fanxiu_mail_action,
    merge_duplicate_fanxiu_mail_records,
    parse_fanxiu_mail_time_text_ms,
    upsert_fanxiu_mail_fact,
)
from backend.core.fanxiu.mail.policy import (
    fanxiu_mail_action_policy_for_record,
    fanxiu_mail_action_policy_for_rewards,
    fanxiu_mail_desired_status_for_rewards,
    fanxiu_mail_prayer_target,
    fanxiu_mail_prayer_values_by_category,
    fanxiu_mail_reward_prayer_value,
    fanxiu_mail_rewards_unresolved,
    fanxiu_mail_title_force_claim_allowed,
    fanxiu_mail_title_is_always_claim,
    fanxiu_mail_visible_group_action_policy,
)
from backend.core.fanxiu.history_museum.packet_capture import mail_sync as fanxiu_mail_packet_sync
from backend.core.fanxiu.history_museum.packet_capture import insight_worker as fanxiu_packet_insight_worker
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


def test_iter_decoded_sources_prefers_recent_capture_time_over_meta_mtime(tmp_path):
    root = tmp_path / "fanxiu" / "tcp-flow"
    root.mkdir(parents=True)
    old_pcap = tmp_path / "old.pcap"
    new_pcap = tmp_path / "new.pcap"
    old_pcap.write_bytes(b"old")
    new_pcap.write_bytes(b"new")
    now = time.time()
    os.utime(old_pcap, (now - 3600, now - 3600))
    os.utime(new_pcap, (now - 60, now - 60))

    old_record = root / "old-record"
    old_record.mkdir()
    (old_record / "decoded.json").write_text(json.dumps({"frames": []}), encoding="utf-8")
    (old_record / "meta.json").write_text(
        json.dumps(
            {
                "record_id": "old-record",
                "decoded_path": str(old_record / "decoded.json"),
                "source_pcap": str(old_pcap),
                "stored_pcap": str(old_record / "old.pcap"),
                "stream": 0,
                "capture_sha256": "old",
                "pcap_name": "old.pcap",
                "created_at": "2026-07-01 10:00:00",
                "pcap_modified_at": "2026-07-01 09:00:00",
            }
        ),
        encoding="utf-8",
    )

    new_record = root / "new-record"
    new_record.mkdir()
    (new_record / "decoded.json").write_text(json.dumps({"frames": []}), encoding="utf-8")
    (new_record / "meta.json").write_text(
        json.dumps(
            {
                "record_id": "new-record",
                "decoded_path": str(new_record / "decoded.json"),
                "source_pcap": str(new_pcap),
                "stored_pcap": str(new_record / "new.pcap"),
                "stream": 0,
                "capture_sha256": "new",
                "pcap_name": "new.pcap",
                "created_at": "2026-06-01 10:00:00",
                "pcap_modified_at": "2026-07-01 09:59:00",
            }
        ),
        encoding="utf-8",
    )
    os.utime(old_record / "meta.json", (now, now))
    os.utime(new_record / "meta.json", (now - 600, now - 600))

    sources = fanxiu_tcp_flow._iter_fanxiu_tcp_decoded_sources(tmp_path)

    assert [item["record_id"] for item in sources[:2]] == ["new-record", "old-record"]


def test_mail_packet_sync_uses_pcap_modified_at_for_last_seen_capture(monkeypatch):
    session = _session()
    decoded_path = Path("dummy-decoded.json")
    source = {
        "decoded_path": str(decoded_path),
        "record_id": "mail-record",
        "pcap_name": "mailbox.pcap",
        "created_at": "2026-07-01 10:30:00",
        "pcap_modified_at": "2026-07-01 10:05:00",
    }
    payload = {
        "frames": [
            {
                "name": "SM_NewMail",
                "parsed": {
                    "mailVos": {
                        "items": [
                            {
                                "id": "m1",
                                "type": "1101",
                                "title": "测试邮件",
                                "createTime": 1782867900000,
                                "rewardGetted": False,
                            }
                        ]
                    }
                },
            }
        ]
    }
    monkeypatch.setattr(fanxiu_mail_packet_sync, "_iter_fanxiu_tcp_decoded_sources", lambda _data_dir=None: [source])
    monkeypatch.setattr(fanxiu_mail_packet_sync, "_load_json_file", lambda _path: payload)
    monkeypatch.setattr(fanxiu_mail_packet_sync, "load_fanxiu_mail_envelope_titles", lambda _export_root=None: {})

    result = fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session)
    session.commit()
    row = session.exec(select(FanxiuMailRecord)).one()

    assert result["ok"] is True
    assert row.last_seen_capture_at == "2026-07-01 10:05:00"


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
        action_policy="claim",
        payload={"mail_rewards": []},
    )
    session.commit()

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-missing")).one()
    assert created is True
    assert created_again is False
    assert record.id == again.id
    assert row.status == "missing_from_list"
    assert row.action_policy == "claim"


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


def test_visible_adjacency_alignment_does_not_change_user_status():
    session = _session()
    for mail_id, title, time_text, status in [
        ("newer", "上边界", "2026年06月21日22:15", "留存"),
        ("protected-middle", "中间留存", "2026年06月20日23:59", "留存"),
        ("locked-middle", "中间锁定", "2026年06月19日23:59", "锁定"),
        ("seen-middle", "中间旧状态", "2026年06月18日23:59", "seen"),
        ("older", "下边界", "2026年06月17日23:58", "留存"),
        ("claimed", "已完成", "2026年06月19日12:00", "claimed"),
    ]:
        upsert_fanxiu_mail_fact(
            session,
            title=title,
            mail_id=mail_id,
            create_time_text=time_text,
            source="packet",
            status=status,
            locked=status == "锁定",
            payload={"mail_rewards": [{"item_name": "洗灵奇石"}]},
        )
    boundary_with_seconds, _ = upsert_fanxiu_mail_fact(
        session,
        title="下边界带秒",
        mail_id="older-with-seconds",
        create_time_text="2026年06月17日23:58",
        source="packet",
        status="锁定",
        locked=True,
    )
    boundary_with_seconds.create_time_ms = parse_fanxiu_mail_time_text_ms("2026年06月17日23:58") + 30_000
    session.add(boundary_with_seconds)
    session.commit()

    result = align_fanxiu_mail_records_claimable_between_times(
        session,
        newer_time_text="2026年06月21日22:15",
        older_time_text="2026年06月17日23:58",
        source="pytest",
    )
    session.commit()

    rows = {row.mail_id: row for row in session.exec(select(FanxiuMailRecord)).all()}
    assert result["matched"] == 1
    assert result["updated"] == 0
    assert rows["seen-middle"].status == "seen"
    assert rows["seen-middle"].locked is False
    assert rows["seen-middle"].action_policy == ""
    assert "visible_adjacency_claimable_alignment" not in (rows["seen-middle"].evidence or {})
    assert rows["protected-middle"].status == "留存"
    assert rows["protected-middle"].locked is False
    assert rows["protected-middle"].action_policy == ""
    assert rows["locked-middle"].status == "锁定"
    assert rows["locked-middle"].locked is True
    assert rows["locked-middle"].action_policy == ""
    assert rows["newer"].status == "留存"
    assert rows["older"].status == "留存"
    assert rows["older-with-seconds"].status == "锁定"
    assert rows["claimed"].status == "claimed"


def test_visible_adjacency_alignment_is_open_interval_and_descending_only():
    session = _session()
    upsert_fanxiu_mail_fact(
        session,
        title="边界同刻",
        mail_id="same-time",
        create_time_text="2026年06月21日22:15",
        source="packet",
        status="锁定",
    )
    session.commit()

    same_time = align_fanxiu_mail_records_claimable_between_times(
        session,
        newer_time_text="2026年06月21日22:15",
        older_time_text="2026年06月21日22:15",
        source="pytest",
    )
    reversed_time = align_fanxiu_mail_records_claimable_between_times(
        session,
        newer_time_text="2026年06月17日23:58",
        older_time_text="2026年06月21日22:15",
        source="pytest",
    )

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "same-time")).one()
    assert same_time["ok"] is False
    assert reversed_time["ok"] is False
    assert row.status == "锁定"


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
    assert rows["mail-a"].action_policy == "claim"
    assert rows["mail-b"].action_policy == "claim"
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

    assert result["claim_policy_count"] == 2
    assert result["delete_policy_count"] == 0


def test_mail_policy_claims_known_safe_rewards():
    assert fanxiu_mail_action_policy_for_rewards([
        {"item_id": "1001", "item_name": "灵石", "item_type": "货币"},
        {"item_id": "37", "item_name": "玄神灵液", "item_type": "类型未知"},
    ]) == "claim"


def test_mail_policy_title_override_uses_explicit_whitelist():
    assert fanxiu_mail_title_is_always_claim("宗门灵泉活动收益")
    assert fanxiu_mail_title_is_always_claim("魔狱封阵奖励")
    assert fanxiu_mail_title_is_always_claim("跨服魔狱封阵奖励补发")
    assert not fanxiu_mail_title_is_always_claim("香车馈赠")
    assert not fanxiu_mail_title_is_always_claim("道场关闭")
    assert not fanxiu_mail_title_is_always_claim("宗门镇邪活动奖励")
    assert not fanxiu_mail_title_is_always_claim("宗门灵泉活动预告")


def test_mail_policy_title_whitelist_never_overrides_faze_or_unknown_rewards():
    assert fanxiu_mail_title_force_claim_allowed(
        "魔狱封阵奖励",
        [{"item_name": "灵石", "item_type": "货币", "amount": 100}],
    )
    assert not fanxiu_mail_title_force_claim_allowed(
        "魔狱封阵奖励",
        [{"item_name": "魔道法则", "item_type": "法则", "amount": 1}],
    )
    assert not fanxiu_mail_title_force_claim_allowed(
        "魔狱封阵奖励",
        [{"item_name": "未知道具 #10080012", "amount": 1}],
    )
    assert not fanxiu_mail_title_force_claim_allowed("魔狱封阵奖励", [])


def test_mail_policy_holds_faze_but_claims_low_value_prayer_resources():
    assert fanxiu_mail_action_policy_for_rewards([
        {"item_id": "10080012", "item_name": "魔道法则", "item_type": "法则"},
    ]) == ""
    assert fanxiu_mail_action_policy_for_rewards([
        {"item_id": "5030001", "item_name": "淬体精魄", "item_type": "丹药"},
    ], prayer_category="炼丹") == "claim"
    assert fanxiu_mail_action_policy_for_rewards([
        {"item_id": "19070082", "item_name": "炼丹灵草匣", "item_type": "礼包宝匣"},
    ], prayer_category="淬体") == "claim"
    assert fanxiu_mail_action_policy_for_rewards([
        {"item_id": "7020014", "item_name": "瑶池玉莲", "item_type": "NPC礼物"},
    ], prayer_category="淬体") == "claim"


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


def test_mail_visible_group_holds_ambiguous_same_title_time_candidates():
    safe_record = FanxiuMailRecord(
        mail_key="id:safe",
        title="联盟天地弈局奖励",
        normalized_title="联盟天地弈局奖励",
        create_time_text="2026年06月07日23:59",
        source="packet",
        payload={"mail_rewards": [{"item_id": "1001", "item_name": "灵石", "item_type": "货币"}]},
    )
    protected_record = FanxiuMailRecord(
        mail_key="id:protected",
        title="联盟天地弈局奖励",
        normalized_title="联盟天地弈局奖励",
        create_time_text="2026年06月07日23:59",
        source="packet",
        payload={"mail_rewards": [{"item_id": "5030001", "item_name": "淬体精魄", "item_type": "丹药"}]},
    )
    assert fanxiu_mail_visible_group_action_policy([safe_record, protected_record]) == ""


def test_mail_policy_keeps_unknown_rewards_without_locking_or_claiming():
    rewards = [{"item_id": "999999", "item_name": "未知道具 #999999"}]

    assert fanxiu_mail_desired_status_for_rewards(rewards) == "留存"
    assert fanxiu_mail_action_policy_for_rewards(rewards) == ""


def test_mail_policy_exposes_prayer_values_for_downstream_flows():
    assert fanxiu_mail_reward_prayer_value({"item_name": "淬体精魄"}) == 10
    assert fanxiu_mail_reward_prayer_value({"item_name": "神品灵草匣"}) == 500
    assert fanxiu_mail_reward_prayer_value({"item_name": "炼丹灵草宝匣"}) == 100
    assert fanxiu_mail_reward_prayer_value({"item_name": "洗灵奇石"}) == 10
    assert fanxiu_mail_reward_prayer_value({"item_name": "造化青莲"}) == 0
    assert fanxiu_mail_reward_prayer_value({"item_name": "灵石"}) is None


def test_mail_policy_calculates_prayer_value_vector_with_amounts():
    rewards = [
        {"item_name": "炼丹灵草匣", "amount": 2},
        {"item_name": "淬体精魄", "amount": 42},
        {"item_name": "珍品饲灵丸", "amount": 2},
        {"item_name": "洗灵奇石", "amount": 20},
        {"item_name": "瑶池玉莲", "amount": 2},
        {"item_name": "灵石", "amount": 9999},
    ]

    assert fanxiu_mail_prayer_values_by_category(rewards) == {
        "炼丹": 100,
        "淬体": 420,
        "灵兽": 200,
        "洗灵": 200,
        "仙花": 200,
    }
    assert fanxiu_mail_prayer_target(rewards) == ("淬体", 420)


def test_mail_policy_claims_only_on_target_prayer_week_above_threshold():
    rewards = [
        {"item_name": "淬体精魄", "amount": 30},
        {"item_name": "珍品饲灵丸", "amount": 4},
    ]

    assert fanxiu_mail_prayer_target(rewards) == ("灵兽", 400)
    assert fanxiu_mail_action_policy_for_rewards(rewards, prayer_category="淬体") == ""
    assert fanxiu_mail_action_policy_for_rewards(rewards, prayer_category="灵兽") == "claim"


def test_mail_policy_uses_prayer_tie_break_priority():
    rewards = [
        {"item_name": "炼丹灵草匣", "amount": 6},
        {"item_name": "淬体精魄", "amount": 30},
        {"item_name": "洗灵奇石", "amount": 30},
        {"item_name": "瑶池玉莲", "amount": 3},
    ]

    assert fanxiu_mail_prayer_target(rewards) == ("仙花", 300)
    assert fanxiu_mail_action_policy_for_rewards(rewards, prayer_category="洗灵") == ""
    assert fanxiu_mail_action_policy_for_rewards(rewards, prayer_category="仙花") == "claim"


def test_mail_policy_claims_prayer_mail_at_or_below_value_threshold():
    assert fanxiu_mail_action_policy_for_rewards(
        [{"item_name": "珍品饲灵丸", "amount": 2}],
        prayer_category="炼丹",
    ) == "claim"
    assert fanxiu_mail_action_policy_for_rewards(
        [{"item_name": "淬体精魄", "amount": 21}],
        prayer_category="炼丹",
    ) == ""


def test_mail_policy_claims_domain_blessing_points_after_item_resolution():
    rewards = [{
        "item_id": "7433",
        "item_name": "领域福令积分",
        "item_type": "类型未知",
    }]

    assert fanxiu_mail_desired_status_for_rewards(rewards) == "可领"
    assert fanxiu_mail_action_policy_for_rewards(rewards) == "claim"


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


def test_mail_packet_gap_trace_reports_decoded_window_without_mail_protocol(monkeypatch, tmp_path):
    session = _session()
    decoded_path = tmp_path / "decoded.json"
    decoded_path.write_text(
        json.dumps(
            {
                "frames": [
                    {"pro_id": 20012, "name": "SM_SyncTime", "direction": "s2c", "parsed": {}},
                    {"pro_id": 51004, "name": "SM_ActivitySync", "direction": "s2c", "parsed": {}},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    live_dir = tmp_path / "live-captures"
    live_dir.mkdir()
    pcap = live_dir / "fanxiu_runtime_20260608_050019.pcap"
    pcap.write_bytes(b"\xd4\xc3\xb2\xa1" + b"0" * 64)

    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_iter_fanxiu_tcp_decoded_sources",
        lambda _data_dir=None: [
            {
                "decoded_path": decoded_path,
                "created_at": "2026-06-08 05:01:27",
                "pcap_name": pcap.name,
                "source_kind": "record",
            }
        ],
    )
    monkeypatch.setattr(fanxiu_mail_packet_sync, "resolve_fanxiu_tcp_live_capture_dir", lambda _data_dir=None: live_dir)

    trace = fanxiu_mail_packet_sync.trace_fanxiu_mail_packet_gap(
        session,
        title="分身协助奖励",
        time_text="2026年06月08日05:00",
    )

    assert trace["diagnosis"] == "decoded_window_has_no_mail_protocol"
    assert trace["decoded_source_count"] == 1
    assert trace["mail_protocol_frames"] == 0
    assert trace["raw_pcap_count"] == 1


def test_mail_packet_gap_trace_reports_unparsed_mail_protocol(monkeypatch, tmp_path):
    session = _session()
    decoded_path = tmp_path / "decoded.json"
    decoded_path.write_text(
        json.dumps(
            {
                "frames": [
                    {"pro_id": 30404, "name": "SM_NewMail", "direction": "s2c", "parsed": None},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    live_dir = tmp_path / "live-captures"
    live_dir.mkdir()

    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_iter_fanxiu_tcp_decoded_sources",
        lambda _data_dir=None: [
            {
                "decoded_path": decoded_path,
                "created_at": "2026-06-08 05:01:27",
                "pcap_name": "capture.pcap",
                "source_kind": "record",
            }
        ],
    )
    monkeypatch.setattr(fanxiu_mail_packet_sync, "resolve_fanxiu_tcp_live_capture_dir", lambda _data_dir=None: live_dir)

    trace = fanxiu_mail_packet_sync.trace_fanxiu_mail_packet_gap(
        session,
        title="分身协助奖励",
        time_text="2026年06月08日05:00",
    )

    assert trace["diagnosis"] == "mail_protocol_unparsed_or_unmatched"
    assert trace["decoded_mail_protocol_counts"] == {"SM_NewMail": 1}
    assert trace["unparsed_mail_protocol_frames"] == 1


def test_mail_packet_gap_trace_scans_raw_action_ids(monkeypatch, tmp_path):
    session = _session()
    decoded_path = tmp_path / "decoded.json"
    mail_id = "24082878061629073"
    decoded_path.write_text(
        json.dumps(
            {
                "frames": [
                    {"pro_id": 30406, "name": "SM_ReadMail", "direction": "s2c", "parsed": {"id": int(mail_id)}},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    live_dir = tmp_path / "live-captures"
    live_dir.mkdir()
    pcap = live_dir / "fanxiu_runtime_20260608_045657.pcap"
    pcap.write_bytes(b"prefix" + fanxiu_mail_packet_sync._encode_lusuo_zigzag_varint(int(mail_id)) + b"suffix")

    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_iter_fanxiu_tcp_decoded_sources",
        lambda _data_dir=None: [
            {
                "decoded_path": decoded_path,
                "created_at": "2026-06-08 04:58:00",
                "pcap_name": pcap.name,
                "source_kind": "record",
            }
        ],
    )
    monkeypatch.setattr(fanxiu_mail_packet_sync, "resolve_fanxiu_tcp_live_capture_dir", lambda _data_dir=None: live_dir)

    trace = fanxiu_mail_packet_sync.trace_fanxiu_mail_packet_gap(
        session,
        title="分身协助奖励",
        time_text="2026年06月08日05:00",
    )

    assert trace["diagnosis"] == "decoded_window_has_mail_actions_but_no_source_fact"
    assert trace["raw_action_id_hits"][0]["name"] == pcap.name
    assert trace["raw_action_id_hits"][0]["mail_ids"] == [mail_id]
    assert trace["raw_action_id_day_hits"][0]["mail_ids"] == [mail_id]


def test_mail_packet_sync_reports_orphan_action_ids(monkeypatch, tmp_path):
    session = _session()
    decoded_path = tmp_path / "decoded.json"
    decoded_path.write_text(
        json.dumps(
            {
                "frames": [
                    {
                        "name": "SM_ReadMail",
                        "direction": "s2c",
                        "parsed": {"id": 24082878061629073},
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_iter_fanxiu_tcp_decoded_sources",
        lambda _data_dir=None: [{"decoded_path": decoded_path, "created_at": "2026-06-08 04:58:00"}],
    )
    monkeypatch.setattr(fanxiu_mail_packet_sync, "load_fanxiu_mail_envelope_titles", lambda _export_root=None: {})

    result = fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session)

    assert result["action_packets"] == 1
    assert result["orphan_action_packets"] == 1
    assert result["orphan_action_samples"][0]["mail_id"] == "24082878061629073"
    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "24082878061629073")).first()
    assert row is not None
    assert row.source == "packet_orphan_action"
    assert row.status == "seen"
    assert row.evidence["orphan_action"] is True


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


def test_packet_decoder_patch_reads_sparse_shared_type_list():
    from types import SimpleNamespace

    from pyxllib.file.packetstream import VarintBinaryReader

    class FakeSchema:
        by_name = {}
        by_id = {7: SimpleNamespace(name="Team")}

        def _read_list(self, *_args, **_kwargs):
            raise AssertionError("sparse list should be handled by the CodeYun patch")

        def _parse_class(self, reader, _info, *, depth):
            return {"value": reader.read_int(), "depth": depth}

    # 5 logical slots, 2 serialized items, shared type id 7, values 11/13.
    reader = VarintBinaryReader(bytes([10, 4, 1, 14, 22, 26]))
    patched = _patch_fanxiu_schema_long_list(FakeSchema())

    result = patched._read_list(reader, write_method="List", depth=2)

    assert result == {
        "_count": 2,
        "_declared_count": 5,
        "_type_id": 7,
        "_type": "Team",
        "_sparse": True,
        "items": [{"value": 11, "depth": 2}, {"value": 13, "depth": 2}],
    }
    assert reader.left() == 0


def test_mail_decoder_trim_keeps_full_mailvo_business_list():
    trimmed = _trim_value(
        {
            "mailVos": {"_type": "MailVo", "items": [{"id": i, "rewards": {"items": list(range(12))}} for i in range(12)]},
            "mailRewards": {"_type": "RewardItem", "items": list(range(12))},
            "other": {"items": list(range(12))},
        }
    )

    assert len(trimmed["mailVos"]["items"]) == 12
    assert "_truncated_items" not in trimmed["mailVos"]
    assert len(trimmed["mailVos"]["items"][0]["rewards"]["items"]) == 8
    assert trimmed["mailVos"]["items"][0]["rewards"]["_truncated_items"] == 4
    assert len(trimmed["mailRewards"]["items"]) == 12
    assert "_truncated_items" not in trimmed["mailRewards"]
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


def test_mail_packet_sync_renders_system_message_time_and_rewards_placeholders(monkeypatch, tmp_path):
    session = _session()
    export_root = tmp_path / "exports"
    system_message_dir = export_root / "parsed_configs" / "SystemMessage"
    system_message_dir.mkdir(parents=True)
    system_message_dir.joinpath("rows.json").write_text(
        json.dumps(
            [
                {
                    "id": 41010,
                    "text_plain": "所在灵脉：$NAME$ 本次聚灵时间：$TIME$ 本次聚灵收益：$L_REWARDS$",
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
          "id": "mail-lingmai-template",
          "type": 2201,
          "title": "灵脉收益",
          "content": "",
          "createTime": 1780704000000,
          "i18nParams": {
            "items": [
              {"_class": "I18nParam2Name", "value": "仙煌神脉", "_super": {"key": "NAME"}},
              {"_class": "I18nParam2Num", "value": 10800000.0, "_super": {"key": "NUM"}},
              {
                "_class": "I18nParam2Reward",
                "value": {"items": [{"type": 0, "code": 37, "amount": 301913}]},
                "_super": {"key": "REWARD"}
              }
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
        lambda _export_root=None: {2201: {"title_plain": "灵脉收益", "contentId": 41010}},
    )
    monkeypatch.setattr(
        fanxiu_mail_packet_sync,
        "_normalize_mail_reward_item",
        lambda reward, _export_root=None, _item_name_index=None: {
            "item_name": "玄神灵液",
            "amount": reward.get("amount"),
        },
    )

    fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session, export_root=export_root)

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-lingmai-template")).first()
    assert row is not None
    assert row.payload["mail_content_text"] == "所在灵脉：仙煌神脉 本次聚灵时间：3小时 本次聚灵收益：玄神灵液 x301913"


def test_mail_packet_sync_time_placeholder_consumes_matching_num_only(monkeypatch, tmp_path):
    session = _session()
    export_root = tmp_path / "exports"
    system_message_dir = export_root / "parsed_configs" / "SystemMessage"
    system_message_dir.mkdir(parents=True)
    system_message_dir.joinpath("rows.json").write_text(
        json.dumps(
            [
                {
                    "id": 35009,
                    "text_plain": "吸收时间:$TIME$ 次数:$NUM$ 修为:$NUM$ 灵露:$NUM$ 体力:$NUM$ 答对:$NUM$",
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
          "id": "mail-lingquan-template",
          "type": 1001014,
          "title": "宗门灵泉活动收益",
          "content": "",
          "createTime": 1780704000000,
          "i18nParams": {
            "items": [
              {"_class": "I18nParam2Num", "value": 720000.0, "_super": {"key": "NUM"}},
              {"_class": "I18nParam2Num", "value": 80.0, "_super": {"key": "NUM"}},
              {"_class": "I18nParam2Num", "value": 222000.0, "_super": {"key": "NUM"}},
              {"_class": "I18nParam2Num", "value": 1.0, "_super": {"key": "NUM"}},
              {"_class": "I18nParam2Num", "value": 80.0, "_super": {"key": "NUM"}},
              {"_class": "I18nParam2Num", "value": 0.0, "_super": {"key": "NUM"}}
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
        lambda _export_root=None: {1001014: {"title_plain": "宗门灵泉活动收益", "contentId": 35009}},
    )

    fanxiu_mail_packet_sync.sync_fanxiu_mail_packets(session, export_root=export_root)

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "mail-lingquan-template")).first()
    assert row is not None
    assert row.payload["mail_content_text"] == "吸收时间:12分钟 次数:80 修为:222000 灵露:1 体力:80 答对:0"


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


def test_orphan_action_title_does_not_overwrite_visible_backfill():
    session = _session()
    record, _ = upsert_fanxiu_mail_fact(
        session,
        title="分身协助奖励",
        mail_id="24082878061629073",
        create_time_text="2026年06月08日05:00",
        source="packet_orphan_action",
        status="seen",
        evidence={"visible_orphan_backfill": True},
    )
    session.add(record)
    session.commit()

    record, _ = upsert_fanxiu_mail_fact(
        session,
        title="未知邮件动作24082878061629073",
        mail_id="24082878061629073",
        create_time_text="",
        source="packet_orphan_action",
        status="seen",
    )
    session.add(record)
    session.commit()

    row = session.exec(select(FanxiuMailRecord).where(FanxiuMailRecord.mail_id == "24082878061629073")).first()
    assert row is not None
    assert row.title == "分身协助奖励"
    assert row.create_time_text == "2026年06月08日05:00"


def test_orphan_action_with_attachment_hint_has_no_runtime_action_policy():
    record = FanxiuMailRecord(
        mail_key="id:24082878061629073",
        mail_id="24082878061629073",
        title="分身协助奖励",
        normalized_title="分身协助奖励",
        create_time_text="2026年06月08日05:00",
        source="packet_orphan_action",
        status="seen",
        payload={
            "orphan_action_status": "seen",
            "mail_rewards_unresolved": True,
            "mail_rewards_unresolved_reason": "visible mail list proves an attachment, but decoded MailVo rewards are missing",
            "has_attachment_hint": True,
        },
        evidence={"visible_orphan_backfill": True, "has_attachment_hint": True},
    )

    assert fanxiu_mail_rewards_unresolved(record.payload)
    assert fanxiu_mail_action_policy_for_record(record) == ""
    assert fanxiu_mail_visible_group_action_policy([record]) == ""


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
        payload={"mail_content_text": "分红发放正文"},
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


def test_mail_records_endpoint_defaults_to_current_runtime_model_and_sorts_by_mail_time(monkeypatch):
    session = _session()
    packet, _ = upsert_fanxiu_mail_fact(
        session,
        title="分红发放",
        mail_id="packet-1",
        mail_type="1101",
        create_time_text="2026年06月05日13:07",
        source="runtime_memory",
        status="留存",
        payload={"mail_content_text": "分红发放正文"},
    )
    newer_packet, _ = upsert_fanxiu_mail_fact(
        session,
        title="今日邮件",
        mail_id="packet-2",
        mail_type="1101",
        create_time_text="2026年06月06日20:00",
        source="runtime_memory",
        status="留存",
        payload={"mail_rewards": [{"item_id": "1", "item_name": "灵石", "icon": "icon_charge_0001"}]},
    )
    orphan, _ = upsert_fanxiu_mail_fact(
        session,
        title="未知邮件动作packet-3",
        mail_id="packet-3",
        create_time_text="2026年06月06日05:00",
        source="runtime_memory",
        status="留存",
    )
    packet.last_seen_at = 2000
    packet.updated_at = 2000
    newer_packet.last_seen_at = 1000
    newer_packet.updated_at = 1000
    orphan.last_seen_at = 3000
    orphan.updated_at = 3000
    packet.present_in_runtime = True
    newer_packet.present_in_runtime = True
    orphan.present_in_runtime = True
    session.add(packet)
    session.add(newer_packet)
    session.add(orphan)
    session.commit()
    monkeypatch.setattr(fanxiu_api, "ensure_fanxiu_write_permission", lambda *_args, **_kwargs: None)

    response = fanxiu_api.list_fanxiu_mail_records(
        limit=2000,
        offset=0,
        status="",
        action_policy="",
        source="all",
        current_user=object(),
        session=session,
    )

    assert response.count == 3
    assert response.total == 3
    assert [record["mail_id"] for record in response.records] == ["packet-2", "packet-3", "packet-1"]
    assert response.records[0]["create_time_text"] == "2026年06月06日20:00"

    with_empty_actions = fanxiu_api.list_fanxiu_mail_records(
        limit=2000,
        offset=0,
        status="",
        action_policy="",
        source="all",
        include_empty_actions=True,
        current_user=object(),
        session=session,
    )
    assert with_empty_actions.total == 3
    assert [record["mail_id"] for record in with_empty_actions.records] == ["packet-2", "packet-3", "packet-1"]

    packet_only = fanxiu_api.list_fanxiu_mail_records(
        limit=2000,
        offset=0,
        status="",
        action_policy="",
        source="runtime_memory",
        include_empty_actions=False,
        current_user=object(),
        session=session,
    )
    assert [record["mail_id"] for record in packet_only.records] == ["packet-2", "packet-3", "packet-1"]


def test_mail_records_endpoint_hides_unresolved_orphan_attachment_evidence_by_default(monkeypatch):
    session = _session()
    orphan, _ = upsert_fanxiu_mail_fact(
        session,
        title="分身协助奖励",
        mail_id="24082878061629073",
        create_time_text="2026年06月08日05:00",
        source="packet_orphan_action",
        status="seen",
        payload={
            "mail_rewards_unresolved": True,
            "has_attachment_hint": True,
            "mail_rewards_unresolved_reason": "visible mail list proves an attachment, but decoded MailVo rewards are missing",
        },
        evidence={"visible_orphan_backfill": True, "has_attachment_hint": True},
    )
    orphan.last_seen_at = 1000
    orphan.updated_at = 1000
    session.add(orphan)
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

    assert response.count == 0
    assert response.total == 0

    response = fanxiu_api.list_fanxiu_mail_records(
        limit=2000,
        offset=0,
        status="",
        action_policy="",
        source="all",
        include_empty_actions=True,
        current_user=object(),
        session=session,
    )

    assert response.count == 1
    assert response.records[0]["source"] == "packet_orphan_action"
    assert response.records[0]["payload"]["mail_rewards_unresolved"] is True


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
            payload={"mail_content_text": f"邮件{index}正文"},
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
