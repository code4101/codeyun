from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select, text

from backend.core.fanxiu.history_museum.packet_capture.player_profile_store import upsert_fanxiu_player_profile_rows
from backend.core.fanxiu.player_profiles import (
    ingest_fanxiu_player_battle_observations,
    list_daily_fanxiu_player_profile_records,
    list_daily_fanxiu_player_xianlv_team_records,
    list_latest_fanxiu_player_profile_records,
    list_latest_fanxiu_player_xianlv_team_records,
)
from backend.models import FanxiuPlayerProfileRecord
from backend.migrations.manager import (
    v68_add_fanxiu_player_profile_records,
    v107_add_fanxiu_player_profile_battle_observations,
)


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
        "battle_score": 37000000000000000000000.0,
        "battle_score_text": "3.7万京",
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
    assert result == {"created": 0, "updated": 0, "skipped_invalid": 1, "skipped_duplicate": 0}
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
    assert result == {"created": 1, "updated": 0, "skipped_invalid": 0, "skipped_duplicate": 0}
    assert len(rows) == 1
    assert rows[0].name == "止清"


def test_player_profile_store_accepts_real_battle_score_only_source():
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
    assert result == {"created": 1, "updated": 0, "skipped_invalid": 0, "skipped_duplicate": 0}
    assert len(rows) == 1
    assert rows[0].battle_score == 37000000000000000000000.0
    assert rows[0].attack_value is None


def test_latest_player_profile_records_keep_older_roles_after_dense_recent_samples():
    session = _session()
    for index in range(6000):
        session.add(
            FanxiuPlayerProfileRecord(
                packet_id=f"fanxiu_runtime_dense|{index}",
                role_id="1",
                role_id_text="1",
                name="密集采样",
                battle_score=1000 + index,
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
            battle_score=2,
            attack_value=2,
            attack_text="2万京",
            captured_at="2026-06-21 10:00:00",
            created_at=1,
        )
    )
    session.commit()

    rows = list_latest_fanxiu_player_profile_records(session, limit=10)

    assert [row["name"] for row in rows] == ["密集采样", "较早角色"]


def test_source_neutral_battle_observation_requires_reliable_identity_time_and_key():
    session = _session()
    valid = {
        "observation_id": "runtime:seat:42:20260819T180000",
        "source_kind": "dongtian_guarder_runtime",
        "role_id": "42",
        "name": "友军",
        "battle_score": 888,
        "observed_at": "2026-08-19T18:00:00+08:00",
    }

    result = ingest_fanxiu_player_battle_observations(
        session,
        [valid, {**valid, "observation_id": "", "role_id": ""}],
    )

    assert result == {"created": 1, "updated": 0, "skipped_invalid": 1, "skipped_duplicate": 0}
    record = session.exec(select(FanxiuPlayerProfileRecord)).one()
    assert record.attack_value is None
    assert record.source_kind == "dongtian_guarder_runtime"


def test_daily_representative_uses_highest_battle_then_newest_without_attack_bias():
    session = _session()
    rows = [
        {
            "observation_id": "a",
            "source_kind": "runtime",
            "role_id": "42",
            "battle_score": 100,
            "observed_at": "2026-08-19 20:00:00",
        },
        {
            "observation_id": "b",
            "source_kind": "runtime",
            "role_id": "42",
            "battle_score": 200,
            "observed_at": "2026-08-19 18:00:00",
        },
        {
            "observation_id": "c",
            "source_kind": "runtime",
            "role_id": "42",
            "battle_score": 200,
            "attack_value": 9,
            "observed_at": "2026-08-19 17:00:00",
        },
        {
            "observation_id": "d",
            "source_kind": "runtime",
            "role_id": "42",
            "battle_score": 50,
            "observed_at": "2026-08-20 01:00:00",
        },
    ]
    ingest_fanxiu_player_battle_observations(session, rows)

    daily = list_daily_fanxiu_player_profile_records(session)
    latest = list_latest_fanxiu_player_profile_records(session)

    assert [row["observation_id"] for row in daily] == ["d", "b"]
    assert [row["observation_id"] for row in latest] == ["d"]


def test_latest_profiles_default_to_stable_battle_score_descending_order():
    session = _session()
    ingest_fanxiu_player_battle_observations(
        session,
        [
            {
                "observation_id": "low-new",
                "source_kind": "runtime",
                "role_id": "1",
                "battle_score": 100,
                "attack_value": 9999,
                "observed_at": "2026-08-19 20:00:00",
            },
            {
                "observation_id": "high-old",
                "source_kind": "runtime",
                "role_id": "2",
                "battle_score": 300,
                "observed_at": "2026-08-19 10:00:00",
            },
            {
                "observation_id": "mid",
                "source_kind": "runtime",
                "role_id": "3",
                "battle_score": 200,
                "observed_at": "2026-08-19 15:00:00",
            },
        ],
    )

    rows = list_latest_fanxiu_player_profile_records(session)

    assert [row["battle_score"] for row in rows] == [300, 200, 100]


def test_xianlv_team_observations_are_independent_and_do_not_expose_team_slot():
    session = _session()
    result = ingest_fanxiu_player_battle_observations(
        session,
        [
            {
                "observation_id": "body",
                "source_kind": "player_runtime",
                "role_id": "42",
                "battle_score": 1000,
                "observed_at": "2026-08-19 10:00:00",
            },
            {
                "observation_id": "team-low-known",
                "source_kind": "dongtian_guarder_runtime",
                "role_id": "42",
                "xianlv_team_fight_score_max": 500,
                "xianlv_team_slot": 1,
                "observed_at": "2026-08-19 11:00:00",
            },
            {
                "observation_id": "team-high-unknown",
                "source_kind": "dongtian_guarder_runtime",
                "role_id": "42",
                "xianlv_team_fight_score_max": 600,
                "observed_at": "2026-08-19 12:00:00",
            },
        ],
    )

    assert result["created"] == 3
    body_latest = list_latest_fanxiu_player_profile_records(session)[0]
    assert body_latest["battle_score"] == 1000
    assert body_latest["observed_at"] == "2026-08-19 10:00:00"
    team_daily = list_daily_fanxiu_player_xianlv_team_records(session)
    team_latest = list_latest_fanxiu_player_xianlv_team_records(session)
    assert [row["observation_id"] for row in team_daily] == ["team-high-unknown"]
    assert team_daily[0]["xianlv_team_observed_at"] == "2026-08-19 12:00:00"
    assert team_latest[0]["xianlv_team_fight_score_max"] == 600
    assert "xianlv_team_slot" not in team_latest[0]


def test_same_observation_higher_xianlv_score_atomically_replaces_internal_evidence():
    session = _session()
    base = {
        "observation_id": "same-event",
        "source_kind": "dongtian_guarder_runtime",
        "role_id": "42",
        "observed_at": "2026-08-19 12:00:00",
    }
    ingest_fanxiu_player_battle_observations(
        session,
        [{**base, "xianlv_team_fight_score_max": 500, "xianlv_team_slot": 1}],
    )
    result = ingest_fanxiu_player_battle_observations(
        session,
        [{**base, "xianlv_team_fight_score_max": 600}],
    )

    record = session.exec(select(FanxiuPlayerProfileRecord)).one()
    assert result["updated"] == 1
    assert record.xianlv_team_fight_score_max == 600
    assert record.evidence["xianlv_team"]["score"] == 600
    assert record.evidence["xianlv_team"]["team_slot"] is None


def test_xianlv_daily_equal_score_uses_newest_observation_regardless_of_team_slot_evidence():
    session = _session()
    ingest_fanxiu_player_battle_observations(
        session,
        [
            {
                "observation_id": "unknown-newer",
                "source_kind": "dongtian_guarder_runtime",
                "role_id": "42",
                "xianlv_team_fight_score_max": 600,
                "observed_at": "2026-08-19 12:00:00",
            },
            {
                "observation_id": "known-older",
                "source_kind": "dongtian_guarder_runtime",
                "role_id": "42",
                "xianlv_team_fight_score_max": 600,
                "xianlv_team_slot": 2,
                "observed_at": "2026-08-19 11:00:00",
            },
        ],
    )

    daily = list_daily_fanxiu_player_xianlv_team_records(session)

    assert [row["observation_id"] for row in daily] == ["unknown-newer"]
    assert "xianlv_team_slot" not in daily[0]


def test_observation_day_uses_asia_shanghai_for_timezone_aware_timestamps():
    session = _session()
    ingest_fanxiu_player_battle_observations(
        session,
        [
            {
                "observation_id": "utc-near-midnight",
                "source_kind": "player_runtime",
                "role_id": "42",
                "battle_score": 600,
                "observed_at": "2026-08-18T16:30:00Z",
            },
        ],
    )

    row = list_daily_fanxiu_player_profile_records(session)[0]
    assert row["observed_date"] == "2026-08-19"


def test_v107_upgrades_historical_player_profile_table_without_rebuild():
    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as session:
        v68_add_fanxiu_player_profile_records(session)
        v107_add_fanxiu_player_profile_battle_observations(session)
        columns = {row[1] for row in session.exec(text("PRAGMA table_info(fanxiuplayerprofilerecord)")).all()}
        indexes = {row[1] for row in session.exec(text("PRAGMA index_list(fanxiuplayerprofilerecord)")).all()}

    assert {
        "xianlv_team_fight_score_max",
        "xianlv_team_fight_score_text",
        "xianlv_team_observed_at",
    } <= columns
    # The already-issued v107 migration may leave this compatibility-only column
    # in existing databases; the ORM, API, and atlas no longer consume it.
    assert "xianlv_team_slot" in columns
    assert "ix_fanxiuplayerprofilerecord_role_date_battle" in indexes
    assert "ix_fanxiuplayerprofilerecord_role_xianlv_time_score" in indexes
