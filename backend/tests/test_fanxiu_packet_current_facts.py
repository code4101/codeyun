from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.packet.current_facts import (
    get_latest_fanxiu_faze_show_facts,
    get_latest_fanxiu_lundao_scene_seat_facts,
    normalize_fanxiu_lundao_scene_seat_facts,
)
from backend.core.fanxiu.packet.decoded_store import (
    decoded_record_rows_from_decode_result,
    upsert_fanxiu_packet_decoded_records,
)
from backend.core.fanxiu.packet.tcp_flow import FANXIU_TCP_DECODE_SCHEMA_VERSION, _trim_value


def _scene_record(*, declared_count: int = 2, truncated: int = 0) -> dict:
    seats = {
        "_count": declared_count,
        "_type_id": 59553,
        "_type": "SeatVO",
        "items": [
            {
                "_class": "SeatVO",
                "id": 12847,
                "preOwnerRoleId": 11,
                "seatOwner": {
                    "_class": "SeatOwnerVO",
                    "roleId": 22,
                    "name": "六六鹤",
                    "level": 230,
                    "model": 1001,
                    "avatar": 205,
                    "headFrame": 52,
                    "sex": 1,
                    "faze": 10010,
                    "titleId": 18000063,
                    "titleName": "",
                    "allianceId": 1001,
                    "hangPoint": {"values": {"_count": 22, "items": [{"key": 0, "value": 1045}]}},
                    "sitDownTime": 100,
                    "protectEndTime": 200,
                    "leftListenTime": 300,
                    "battleScore": 1234.5,
                    "serverId": 22060,
                    "ganwuStartTime": 400,
                },
            },
            {"_class": "SeatVO", "id": 12848, "preOwnerRoleId": 0, "seatOwner": None},
        ],
    }
    if truncated:
        seats["_truncated_items"] = truncated
    return {
        "packet_id": "packet-1",
        "record_id": "record-1",
        "pcap_name": "capture.pcap",
        "capture_sha256": "abc",
        "stream": 0,
        "direction": "s2c",
        "frame_index": 3,
        "offset": 12,
        "sn": 7,
        "pro_id": 59504,
        "name": "SM_SyncLundaoSceneInfo",
        "captured_at": "2026-07-18 15:00:00",
        "decode_error": "",
        "payload": {
            "pro_id": 59504,
            "name": "SM_SyncLundaoSceneInfo",
            "parsed": {
                "_class": "SM_SyncLundaoSceneInfo",
                "npcId": 10057,
                "themeId": 24,
                "seats": seats,
                "_super": {"_class": "ClientResult", "code": 0},
            },
            "parsed_bytes": 2181,
            "remain": 0,
        },
        "evidence": {"decoded_path": "decoded.json", "stored_pcap": "capture.pcap"},
    }


def test_normalize_lundao_scene_reports_truncated_completeness_and_safe_fields() -> None:
    result = normalize_fanxiu_lundao_scene_seat_facts(_scene_record(declared_count=8, truncated=6))

    assert result["ok"] is True
    assert result["declared_count"] == 8
    assert result["decoded_count"] == 2
    assert result["truncated_count"] == 6
    assert result["complete"] is False
    assert result["seat_type_id"] == 59553
    assert result["seat_type"] == "SeatVO"
    assert result["seats"][0]["owner"]["name"] == "六六鹤"
    assert result["seats"][0]["owner"]["battle_score"] == 1234.5
    assert "hang_point" not in result["seats"][0]["owner"]
    assert result["seats"][1]["owner"] is None
    assert result["evidence"]["packet_id"] == "packet-1"


def test_normalize_current_lundao_room_packet_reads_nested_room_vo() -> None:
    record = _scene_record()
    record["pro_id"] = 59518
    record["name"] = "SM_SeatsNoInScene"
    record["payload"]["pro_id"] = 59518
    record["payload"]["name"] = "SM_SeatsNoInScene"
    parsed = record["payload"]["parsed"]
    parsed.clear()
    parsed.update({
        "_class": "SM_SeatsNoInScene",
        "roomId": 15,
        "roomVO": {"id": 15, "npcId": 10111, "themeId": 2},
        "seats": _scene_record()["payload"]["parsed"]["seats"],
    })

    result = normalize_fanxiu_lundao_scene_seat_facts(record)

    assert result["room_id"] == 15
    assert result["npc_id"] == 10111
    assert result["theme_id"] == 2
    assert result["protocol"] == "SM_SeatsNoInScene"
    assert result["pro_id"] == 59518


def test_decoder_keeps_full_lundao_seat_roster_but_still_trims_nested_details() -> None:
    trimmed = _trim_value(
        {
            "seats": {
                "_type": "SeatVO",
                "items": [
                    {"id": index, "seatOwner": {"hangPoint": {"items": list(range(12))}}}
                    for index in range(15)
                ],
            }
        }
    )

    assert FANXIU_TCP_DECODE_SCHEMA_VERSION >= 2
    assert len(trimmed["seats"]["items"]) == 15
    assert "_truncated_items" not in trimmed["seats"]
    assert len(trimmed["seats"]["items"][0]["seatOwner"]["hangPoint"]["items"]) == 8
    assert trimmed["seats"]["items"][0]["seatOwner"]["hangPoint"]["_truncated_items"] == 4


def test_get_latest_lundao_scene_reads_latest_decoded_record_without_catch_up() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    old = _scene_record()
    old["packet_id"] = "old"
    old["pcap_name"] = "fanxiu_runtime_20260718_140000.pcap"
    old["captured_at"] = "2026-07-18 14:00:00"
    newest = _scene_record()
    newest["packet_id"] = "new"
    newest["pcap_name"] = "fanxiu_runtime_20260718_150000.pcap"
    newest["captured_at"] = "2026-07-18 15:00:00"
    newest["payload"]["parsed"]["themeId"] = 25

    with Session(engine) as session:
        rows = []
        for record in (old, newest):
            rows.extend(
                decoded_record_rows_from_decode_result(
                    {
                        "record_id": record["record_id"],
                        "created_at": record["captured_at"],
                        "pcap_name": record["pcap_name"],
                        "capture_sha256": record["capture_sha256"],
                        "stream": record["stream"],
                        "frames": [record["payload"] | {"direction": "s2c", "offset": record["offset"], "sn": record["sn"]}],
                    }
                )
            )
        # The generated packet ids need to differ because both fixture records
        # otherwise point at the same record/offset/sn tuple.
        rows[0]["packet_id"] = "old"
        rows[1]["packet_id"] = "new"
        upsert_fanxiu_packet_decoded_records(session, rows)
        result = get_latest_fanxiu_lundao_scene_seat_facts(session)

    assert result["available"] is True
    assert result["captured_at"] == "2026-07-18 15:00:00"
    assert result["theme_id"] == 25
    assert result["complete"] is True


def test_get_latest_lundao_scene_prefers_capture_name_over_backlog_insert_time() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    old_capture = _scene_record()
    old_capture["pcap_name"] = "fanxiu_runtime_20260717_230000.pcap"
    old_capture["captured_at"] = "2026-07-18 16:00:00"
    old_capture["payload"]["parsed"]["themeId"] = 17
    new_capture = _scene_record()
    new_capture["pcap_name"] = "fanxiu_runtime_20260718_150000.pcap"
    new_capture["captured_at"] = "2026-07-18 15:00:00"
    new_capture["payload"]["parsed"]["themeId"] = 18

    with Session(engine) as session:
        rows = []
        for record in (old_capture, new_capture):
            rows.extend(
                decoded_record_rows_from_decode_result(
                    {
                        "record_id": record["record_id"],
                        "created_at": record["captured_at"],
                        "pcap_name": record["pcap_name"],
                        "capture_sha256": record["capture_sha256"],
                        "stream": record["stream"],
                        "frames": [record["payload"] | {"direction": "s2c", "offset": record["offset"], "sn": record["sn"]}],
                    }
                )
            )
        rows[0]["packet_id"] = "old-capture"
        rows[1]["packet_id"] = "new-capture"
        upsert_fanxiu_packet_decoded_records(session, rows)
        result = get_latest_fanxiu_lundao_scene_seat_facts(session)

    assert result["theme_id"] == 18
    assert result["evidence"]["pcap_name"] == "fanxiu_runtime_20260718_150000.pcap"


def test_get_latest_lundao_scene_returns_explicit_no_fact_result() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        result = get_latest_fanxiu_lundao_scene_seat_facts(session)

    assert result == {
        "ok": True,
        "available": False,
        "protocol": "SM_SeatsNoInScene",
        "pro_id": 59518,
        "reason": "no_decoded_record",
        "declared_count": None,
        "decoded_count": 0,
        "truncated_count": 0,
        "complete": False,
        "seats": [],
        "evidence": {},
    }


def test_get_latest_faze_show_prefers_latest_capture_across_both_protocols() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    decode_result = {
        "record_id": "faze-record",
        "created_at": "2026-07-18 15:00:00",
        "pcap_name": "fanxiu_runtime_20260718_150000.pcap",
        "capture_sha256": "faze-sha",
        "stream": 0,
        "frames": [
            {
                "direction": "s2c",
                "offset": 10,
                "sn": 1,
                "pro_id": 34003,
                "name": "SM_ShowFazePanel",
                "parsed": {"_class": "SM_ShowFazePanel", "showId": 10010},
            },
            {
                "direction": "s2c",
                "offset": 20,
                "sn": 2,
                "pro_id": 34001,
                "name": "SM_FazeShow",
                "parsed": {"_class": "SM_FazeShow", "fazeResId": 10020},
            },
        ],
    }
    with Session(engine) as session:
        rows = decoded_record_rows_from_decode_result(decode_result)
        rows[0]["pcap_name"] = "fanxiu_runtime_20260718_140000.pcap"
        rows[1]["pcap_name"] = "fanxiu_runtime_20260718_150000.pcap"
        upsert_fanxiu_packet_decoded_records(session, rows)
        result = get_latest_fanxiu_faze_show_facts(session)

    assert result["available"] is True
    assert result["protocol"] == "SM_FazeShow"
    assert result["faze_id"] == 10020
