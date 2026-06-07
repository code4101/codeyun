from sqlmodel import Session, SQLModel, create_engine, select

from backend.core.fanxiu_packet_decoded_store import (
    decoded_record_rows_from_decode_result,
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
