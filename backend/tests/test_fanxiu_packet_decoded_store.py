from sqlmodel import Session, SQLModel, create_engine, select

from backend.core.fanxiu.packet.decoded_store import (
    decoded_record_rows_from_decode_result,
    list_fanxiu_packet_decoded_records,
    prune_fanxiu_packet_decoded_records,
    upsert_fanxiu_packet_decoded_records,
)
from backend.models import FanxiuPacketDecodedRecord


def test_decoded_record_rows_from_decode_result_keeps_plaintext_payload() -> None:
    result = {
        "record_id": "record-1",
        "created_at": "2026-06-04 22:30:00",
        "pcap_name": "capture.pcap",
        "capture_sha256": "abc",
        "stream": 2,
        "stored_decoded_path": "decoded.json",
        "frames": [
            {
                "direction": "s2c",
                "offset": 12,
                "sn": 7,
                "pro_id": 30008,
                "name": "SM_ShowOther",
                "payload_len": 9,
                "parsed": {"name": "玩家"},
            },
            {"direction": "c2s", "offset": 20, "sn": 8, "pro_id": 20011, "name": "CM_SyncTime", "time": 123},
        ],
    }

    rows = decoded_record_rows_from_decode_result(result)

    assert [row["packet_id"] for row in rows] == [
        "record-1|s2c|12|30008|7",
        "record-1|c2s|20|20011|8",
    ]
    assert rows[0]["payload"]["parsed"] == {"name": "玩家"}
    assert rows[0]["captured_at"] == "2026-06-04 22:30:00"


def test_upsert_fanxiu_packet_decoded_records_is_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    rows = decoded_record_rows_from_decode_result(
        {
            "record_id": "record-1",
            "created_at": "2026-06-04 22:30:00",
            "pcap_name": "capture.pcap",
            "capture_sha256": "abc",
            "stream": 2,
            "frames": [
                {"direction": "s2c", "offset": 12, "sn": 7, "pro_id": 30008, "name": "SM_ShowOther", "parsed": {"name": "玩家"}},
            ],
        }
    )

    with Session(engine) as session:
        first = upsert_fanxiu_packet_decoded_records(session, rows)
        second = upsert_fanxiu_packet_decoded_records(session, rows)
        records = session.exec(select(FanxiuPacketDecodedRecord)).all()

    assert first == {"created": 1, "updated": 0, "skipped_invalid": 0, "skipped_duplicate": 0}
    assert second == {"created": 0, "updated": 0, "skipped_invalid": 0, "skipped_duplicate": 1}
    assert len(records) == 1
    assert records[0].payload["parsed"] == {"name": "玩家"}


def test_list_fanxiu_packet_decoded_records_filters_latest_by_protocol() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    rows = decoded_record_rows_from_decode_result(
        {
            "record_id": "record-1",
            "created_at": "2026-06-04 22:30:00",
            "pcap_name": "capture.pcap",
            "capture_sha256": "abc",
            "stream": 2,
            "frames": [
                {"direction": "s2c", "offset": 12, "sn": 7, "pro_id": 95102, "name": "SM_XianLvMineEnterSync", "parsed": {"memberNum": 12}},
                {"direction": "s2c", "offset": 20, "sn": 8, "pro_id": 95185, "name": "SM_XianLvMineUpdateAttackFatigueValue", "parsed": {"attackFatigueValue": 100}},
            ],
        }
    )

    with Session(engine) as session:
        upsert_fanxiu_packet_decoded_records(session, rows)
        result = list_fanxiu_packet_decoded_records(session, names=["SM_XianLvMineEnterSync"], limit=10)

    assert result["count"] == 1
    assert result["records"][0]["name"] == "SM_XianLvMineEnterSync"
    assert result["records"][0]["payload"]["parsed"]["memberNum"] == 12


def test_prune_fanxiu_packet_decoded_records_deletes_expired_but_keeps_minimum() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    rows = []
    for index, captured_at in enumerate(
        [
            "2026-06-01 10:00:00",
            "2026-06-02 10:00:00",
            "2026-07-06 10:00:00",
        ]
    ):
        rows.extend(
            decoded_record_rows_from_decode_result(
                {
                    "record_id": f"record-{index}",
                    "created_at": captured_at,
                    "pcap_name": f"capture-{index}.pcap",
                    "capture_sha256": f"abc-{index}",
                    "stream": 0,
                    "frames": [
                        {"direction": "s2c", "offset": index, "sn": index, "pro_id": 95102, "name": "SM_XianLvMineEnterSync", "parsed": {"index": index}},
                    ],
                }
            )
        )

    with Session(engine) as session:
        upsert_fanxiu_packet_decoded_records(session, rows)
        result = prune_fanxiu_packet_decoded_records(
            session,
            max_age_seconds=3 * 24 * 60 * 60,
            min_keep=1,
        )
        records = session.exec(select(FanxiuPacketDecodedRecord)).all()

    assert result["deleted"] == 2
    assert len(records) == 1
    assert records[0].captured_at == "2026-07-06 10:00:00"
