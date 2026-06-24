from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from backend.core.fanxiu.packet.player_profile_store import (
    list_latest_fanxiu_player_profile_records,
    upsert_fanxiu_player_profile_rows,
)
from backend.models import FanxiuPlayerProfileRecord


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _profile_row(*, packet_id: str, record_id: str, pcap_name: str) -> dict:
    return {
        "source_kind": "sync_player",
        "role_id": "24082878061087500",
        "role_id_text": "24082878061087500",
        "name": "止清",
        "server_name": "岁序更替",
        "captured_at": "2026-06-01 10:02:30",
        "combat_attributes": [{"key": 2001, "name": "攻击", "value": 939600000000000000.0, "text": "93.9京"}],
        "evidence": {
            "packet_id": packet_id,
            "protocol": "SM_SyncPlayer",
            "decoded_at": "2026-06-01 10:02:30",
            "record_id": record_id,
            "pcap_name": pcap_name,
        },
    }


def _battle_score_only_row(*, packet_id: str, record_id: str, pcap_name: str) -> dict:
    row = _profile_row(packet_id=packet_id, record_id=record_id, pcap_name=pcap_name)
    row["combat_attributes"] = []
    row["battle_score"] = 37000000000000000000000.0
    row["battle_score_text"] = "3.7万京"
    return row


def test_player_profile_store_rejects_synthetic_fixture_source():
    session = _session()

    result = upsert_fanxiu_player_profile_rows(
        session,
        [_profile_row(packet_id="sync-player", record_id="r1", pcap_name="a.pcap")],
    )

    rows = session.exec(select(FanxiuPlayerProfileRecord)).all()
    assert result == {"created": 0, "skipped_invalid": 1, "skipped_duplicate": 0}
    assert rows == []


def test_player_profile_store_accepts_real_capture_source():
    session = _session()

    result = upsert_fanxiu_player_profile_rows(
        session,
        [
            _profile_row(
                packet_id="fanxiu_runtime_20260607_111800_abcd_stream0|s2c|1|30008|1",
                record_id="fanxiu_runtime_20260607_111800_abcd_stream0",
                pcap_name="fanxiu_runtime_20260607_111800.pcap",
            )
        ],
    )

    rows = session.exec(select(FanxiuPlayerProfileRecord)).all()
    assert result == {"created": 1, "skipped_invalid": 0, "skipped_duplicate": 0}
    assert len(rows) == 1
    assert rows[0].name == "止清"


def test_player_profile_store_rejects_real_battle_score_only_source():
    session = _session()

    result = upsert_fanxiu_player_profile_rows(
        session,
        [
            _battle_score_only_row(
                packet_id="fanxiu_runtime_20260623_181359_abcd_stream0|s2c|1|30007|1",
                record_id="fanxiu_runtime_20260623_181359_abcd_stream0",
                pcap_name="fanxiu_runtime_20260623_181359.pcap",
            )
        ],
    )

    rows = session.exec(select(FanxiuPlayerProfileRecord)).all()
    assert result == {"created": 0, "skipped_invalid": 1, "skipped_duplicate": 0}
    assert rows == []


def test_latest_player_profile_records_keep_older_roles_after_dense_recent_samples():
    session = _session()
    for index in range(6000):
        session.add(
            FanxiuPlayerProfileRecord(
                packet_id=f"fanxiu_runtime_dense|{index}",
                role_id="1",
                role_id_text="1",
                name="密集采样",
                attack_value=1000 + index,
                attack_text="1京",
                captured_at=f"2026-06-23 19:{index // 60:02d}:{index % 60:02d}",
                created_at=100000 + index,
            )
        )
    session.add(
        FanxiuPlayerProfileRecord(
            packet_id="fanxiu_runtime_older|1",
            role_id="2",
            role_id_text="2",
            name="较早角色",
            attack_value=2,
            attack_text="2万京",
            captured_at="2026-06-21 10:00:00",
            created_at=1,
        )
    )
    session.commit()

    rows = list_latest_fanxiu_player_profile_records(session, limit=10)

    assert [row["name"] for row in rows] == ["密集采样", "较早角色"]
