from datetime import datetime

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.history_museum.packet_capture import current_facts as current_facts_module
from backend.core.fanxiu.history_museum.packet_capture.current_facts import (
    get_latest_fanxiu_faze_show_facts,
    get_latest_fanxiu_lingmai_daily_status,
    get_latest_fanxiu_lingmai_scene_seat_facts,
    get_latest_fanxiu_lingmai_self_seat_facts,
    get_latest_fanxiu_lundao_scene_seat_facts,
    get_latest_fanxiu_lundao_kick_transition_facts,
    get_latest_fanxiu_lundao_status_facts,
    get_latest_fanxiu_xianyuan_duel_facts,
    normalize_fanxiu_lingmai_scene_seat_facts,
    normalize_fanxiu_lingmai_daily_status,
    normalize_fanxiu_lingmai_self_seat_facts,
    normalize_fanxiu_lundao_scene_seat_facts,
    normalize_fanxiu_xianyuan_duel_record,
)
from backend.core.fanxiu.history_museum.packet_capture.decoded_store import (
    decoded_record_rows_from_decode_result,
    upsert_fanxiu_packet_decoded_records,
)
from backend.core.fanxiu.history_museum.packet_capture.tcp_flow import FANXIU_TCP_DECODE_SCHEMA_VERSION, _trim_value


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

    assert FANXIU_TCP_DECODE_SCHEMA_VERSION >= 3
    assert len(trimmed["seats"]["items"]) == 15
    assert "_truncated_items" not in trimmed["seats"]
    assert len(trimmed["seats"]["items"][0]["seatOwner"]["hangPoint"]["items"]) == 8
    assert trimmed["seats"]["items"][0]["seatOwner"]["hangPoint"]["_truncated_items"] == 4


def test_decoder_keeps_full_lingmai_seat_roster() -> None:
    trimmed = _trim_value(
        {
            "seats": {
                "_type": "VeinsSeatVO",
                "items": [{"id": index, "seatOwner": {"name": f"玩家{index}"}} for index in range(10)],
            }
        }
    )

    assert len(trimmed["seats"]["items"]) == 10
    assert "_truncated_items" not in trimmed["seats"]


def test_decoder_keeps_complete_reward_and_cost_result_lists() -> None:
    trimmed = _trim_value(
        {
            "rewards": {
                "_type": "RewardResult",
                "items": [{"type": 0, "code": index, "content": {"items": [{"id": index}]}} for index in range(12)],
            },
            "costs": {
                "_type": "CostResult",
                "items": [{"type": 0, "code": index, "content": {"items": [{"id": index}]}} for index in range(13)],
            },
        }
    )

    assert FANXIU_TCP_DECODE_SCHEMA_VERSION >= 5
    assert len(trimmed["rewards"]["items"]) == 12
    assert len(trimmed["costs"]["items"]) == 13
    assert "_truncated_items" not in trimmed["rewards"]
    assert "_truncated_items" not in trimmed["costs"]


def test_decoder_keeps_complete_activity_rank_lists() -> None:
    assert FANXIU_TCP_DECODE_SCHEMA_VERSION >= 7
    for vo_type in ("ActivityRankPersonalVO", "ActivityRankCrossServerVO", "ActivityRankTeamVO"):
        trimmed = _trim_value(
            {
                "rankVOS": {
                    "_type": vo_type,
                    "items": [
                        {"rank": rank, "name": f"玩家{rank}", "score": 1000 - rank}
                        for rank in range(1, 142)
                    ],
                }
            }
        )

        assert len(trimmed["rankVOS"]["items"]) == 141
        assert trimmed["rankVOS"]["items"][-1]["rank"] == 141
        assert "_truncated_items" not in trimmed["rankVOS"]


def test_decoder_keeps_complete_quest_membership() -> None:
    trimmed = _trim_value(
        {
            "entryVOs": {
                "_type": "QuestEntryVO",
                "_count": 14,
                "items": [{"taskId": 804290151 + index} for index in range(14)],
            }
        }
    )

    assert FANXIU_TCP_DECODE_SCHEMA_VERSION >= 8
    assert len(trimmed["entryVOs"]["items"]) == 14
    assert "_truncated_items" not in trimmed["entryVOs"]


def test_normalize_lingmai_daily_status_treats_explicit_zero_as_complete() -> None:
    record = _scene_record()
    record["pro_id"] = 93513
    record["name"] = "SM_SyncUnionVeinsRoleInfo"
    record["payload"]["pro_id"] = 93513
    record["payload"]["name"] = "SM_SyncUnionVeinsRoleInfo"
    record["payload"]["parsed"] = {
        "_class": "SM_SyncUnionVeinsRoleInfo",
        "roomId": 0,
        "seatId": 0,
        "leftListenTime": 0,
        "sitDownTime": 0,
    }

    result = normalize_fanxiu_lingmai_daily_status(record)

    assert result["ok"] is True
    assert result["available"] is True
    assert result["completed"] is True
    assert result["remaining_milliseconds"] == 0
    assert result["remaining_seconds"] == 0
    assert result["protocol"] == "SM_SyncUnionVeinsRoleInfo"


def test_decoder_keeps_full_union_lingmai_seat_roster() -> None:
    trimmed = _trim_value(
        {
            "seats": {
                "_type": "UnionVeinsSeatVO",
                "items": [{"id": index, "seatOwner": {"name": f"玩家{index}"}} for index in range(10)],
            }
        }
    )

    assert len(trimmed["seats"]["items"]) == 10
    assert "_truncated_items" not in trimmed["seats"]


def test_normalize_lingmai_scene_exposes_room_capacity_and_protection() -> None:
    record = _scene_record(declared_count=7)
    record["pro_id"] = 87216
    record["name"] = "SM_VeinsSeatsNoInScene"
    record["payload"]["pro_id"] = 87216
    record["payload"]["name"] = "SM_VeinsSeatsNoInScene"
    seats = record["payload"]["parsed"]["seats"]
    seats["_type"] = "VeinsSeatVO"
    seats["_type_id"] = 87253
    record["payload"]["parsed"] = {
        "_class": "SM_VeinsSeatsNoInScene",
        "roomId": 10,
        "roomVO": {"id": 10, "left": 3, "npcId": 10109, "themeId": 1},
        "seats": seats,
    }

    result = normalize_fanxiu_lingmai_scene_seat_facts(record)

    assert result["room_id"] == 10
    assert result["available_count"] == 3
    assert result["capacity"] == 10
    assert result["seat_type"] == "VeinsSeatVO"
    assert result["seats"][0]["owner"]["protect_end_time"] == 200


def test_normalize_union_lingmai_scene_uses_current_protocol_family() -> None:
    record = _scene_record(declared_count=9, truncated=1)
    record["pro_id"] = 93517
    record["name"] = "SM_UnionVeinsSeatsNoInScene"
    record["payload"]["pro_id"] = 93517
    record["payload"]["name"] = "SM_UnionVeinsSeatsNoInScene"
    seats = record["payload"]["parsed"]["seats"]
    seats["_type"] = "UnionVeinsSeatVO"
    seats["_type_id"] = 93553
    seats["items"][0]["seatOwner"]["teamUid"] = 24077380502945993
    seats["items"][0]["seatOwner"]["teamName"] = "玉清道宗"
    record["payload"]["parsed"] = {
        "_class": "SM_UnionVeinsSeatsNoInScene",
        "roomId": 17,
        "roomVO": {"id": 17, "left": 1, "npcId": 10109, "themeId": 1},
        "seats": seats,
    }

    result = normalize_fanxiu_lingmai_scene_seat_facts(record)

    assert result["protocol"] == "SM_UnionVeinsSeatsNoInScene"
    assert result["pro_id"] == 93517
    assert result["room_id"] == 17
    assert result["available_count"] == 1
    assert result["seats"][0]["owner"]["team_uid"] == 24077380502945993
    assert result["seats"][0]["owner"]["team_name"] == "玉清道宗"


def test_get_latest_lingmai_scene_returns_explicit_no_fact_result() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        result = get_latest_fanxiu_lingmai_scene_seat_facts(session)

    assert result["available"] is False
    assert result["protocol"] == "SM_VeinsSeatsNoInScene"
    assert result["pro_id"] == 87216
    assert result["available_count"] is None
    assert result["complete"] is False


def test_normalize_lingmai_self_seat_exposes_current_role() -> None:
    record = _scene_record(declared_count=1)
    record["pro_id"] = 87227
    record["name"] = "SM_VeinsSelfSeat"
    record["pcap_name"] = "fanxiu_runtime_20260719_190938.pcap"
    record["payload"]["pro_id"] = 87227
    record["payload"]["name"] = "SM_VeinsSelfSeat"
    record["payload"]["parsed"] = {
        "_class": "SM_VeinsSelfSeat",
        "seatVO": {
            "id": 3798,
            "preOwnerRoleId": 11,
            "seatOwner": {"roleId": 42, "name": "自己", "battleScore": 1234.5},
        },
    }

    result = normalize_fanxiu_lingmai_self_seat_facts(record)

    assert result["available"] is True
    assert result["seated"] is True
    assert result["seat"]["seat_id"] == 3798
    assert result["seat"]["owner"]["role_id"] == 42
    assert result["evidence"]["pcap_name"] == "fanxiu_runtime_20260719_190938.pcap"


def test_get_latest_lingmai_self_seat_returns_explicit_no_fact_result() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        result = get_latest_fanxiu_lingmai_self_seat_facts(session)

    assert result["available"] is False
    assert result["seated"] is False
    assert result["protocol"] == "SM_VeinsSelfSeat"
    assert result["pro_id"] == 87227


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


def test_lundao_status_uses_frame_order_when_strength_updates_in_same_capture() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    frames = [
        {
            "direction": "s2c",
            "frame_index": 27,
            "offset": 100,
            "sn": 1,
            "pro_id": 59502,
            "name": "SM_RoomList",
            "parsed": {
                "_class": "SM_RoomList",
                "strength": 0,
                "roomId": 14,
                "seatId": 12831,
                "leftListenTime": 18900000,
                "sitDownTime": 123,
                "rooms": {"items": [{"id": 15, "left": 0}, {"id": 14, "left": 5}]},
            },
        },
        {
            "direction": "s2c",
            "frame_index": 46,
            "offset": 200,
            "sn": 2,
            "pro_id": 59508,
            "name": "SM_UpdateLundaoStrength",
            "parsed": {"_class": "SM_UpdateLundaoStrength", "strength": 1},
        },
    ]
    with Session(engine) as session:
        rows = decoded_record_rows_from_decode_result(
            {
                "record_id": "lundao-status",
                "created_at": "2026-07-20 16:25:00",
                "pcap_name": "fanxiu_runtime_20260720_161846.pcap",
                "capture_sha256": "status-sha",
                "stream": 0,
                "frames": frames,
            }
        )
        upsert_fanxiu_packet_decoded_records(session, rows)
        result = get_latest_fanxiu_lundao_status_facts(session)

    assert result["strength"] == 1
    assert result["room_id"] == 14
    assert result["seat_id"] == 12831
    assert result["seated"] is True
    assert result["room_available_counts"] == {"15": 0, "14": 5}
    assert result["evidence"]["strength"]["frame_index"] == 1


def test_lundao_room_list_without_seat_id_still_reports_seated() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        rows = decoded_record_rows_from_decode_result(
            {
                "record_id": "lundao-room-only",
                "created_at": "2026-07-20 17:53:37",
                "pcap_name": "fanxiu_runtime_20260720_174840.pcap",
                "capture_sha256": "room-only-sha",
                "stream": 0,
                "frames": [
                    {
                        "direction": "s2c",
                        "frame_index": 115,
                        "offset": 2208,
                        "sn": 0,
                        "pro_id": 59502,
                        "name": "SM_RoomList",
                        "parsed": {
                            "_class": "SM_RoomList",
                            "strength": 1,
                            "roomId": 14,
                            "leftListenTime": 18900000,
                            "sitDownTime": 1784535539198,
                            "rooms": {"items": [{"id": 15, "left": 0}, {"id": 14, "left": 0}]},
                        },
                    }
                ],
            }
        )
        upsert_fanxiu_packet_decoded_records(session, rows)
        result = get_latest_fanxiu_lundao_status_facts(session)

    assert result["room_id"] == 14
    assert result["seat_id"] is None
    assert result["seated"] is True


def test_lundao_kick_transition_detects_latest_seated_to_unseated_event() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    frames = [
        {
            "direction": "s2c",
            "offset": 10,
            "sn": 1,
            "pro_id": 59514,
            "name": "SM_SyncLundaoRoleInfo",
            "parsed": {"roomId": 14, "seatId": 12831, "leftListenTime": 1000},
        },
        {
            "direction": "s2c",
            "offset": 20,
            "sn": 2,
            "pro_id": 59514,
            "name": "SM_SyncLundaoRoleInfo",
            "parsed": {"roomId": 0, "seatId": 0, "leftListenTime": 1000},
        },
    ]
    with Session(engine) as session:
        rows = decoded_record_rows_from_decode_result(
                {
                    "record_id": "kick-transition",
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "pcap_name": "fanxiu_runtime_20260720_190000.pcap",
                "capture_sha256": "kick-sha",
                "stream": 0,
                "frames": frames,
            }
        )
        upsert_fanxiu_packet_decoded_records(session, rows)
        result = get_latest_fanxiu_lundao_kick_transition_facts(session, since_seconds=2 * 86400)

    assert result["kicked"] is True
    assert result["event_at"] == "2026-07-20 19:00:00"
    assert result["evidence"]["frame_index"] == 1


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






def _xianyuan_target(target_id: int, name: str, score: int, power: int, server: int) -> dict:
    return {
        "id": target_id,
        "player": True,
        "willScore": 100,
        "rankVO": {"name": name, "score": score, "rank": 1, "server": server, "power": power},
        "teamVO": {"power": power},
    }


def _xianyuan_record(
    *,
    protocol: str,
    pro_id: int,
    pcap_name: str,
    frame_index: int,
    targets: list[dict],
    self_power: int | None = None,
    remaining_challenges: int | None = None,
    remaining_refreshes: int | None = None,
) -> dict:
    parsed: dict = {"targets": {"_count": len(targets), "items": targets}}
    if protocol == "SM_PartnerArenaPlayInfo":
        parsed["joinerVO"] = {
            "teams": {"_count": 1, "items": [{"power": self_power}]},
            "remainChallengeTimes": remaining_challenges,
            "remainRefreshTimes": remaining_refreshes,
            "current": 3000,
            "rank": 50,
        }
    elif protocol == "SM_PartnerArenaChallenge":
        parsed.update(
            {
                "remainChallengeTimes": remaining_challenges,
                "current": 3100,
                "victory": True,
                "recordVO": {"attacker": {"power": self_power}},
            }
        )
    elif protocol == "SM_PartnerArenaRefresh":
        parsed["remainRefreshTimes"] = remaining_refreshes
    return {
        "packet_id": f"{pcap_name}|{frame_index}",
        "record_id": pcap_name,
        "pcap_name": pcap_name,
        "frame_index": frame_index,
        "offset": frame_index * 10,
        "sn": frame_index,
        "pro_id": pro_id,
        "name": protocol,
        "captured_at": "2026-07-24 23:00:00",
        "decode_error": "",
        "payload": {
            "pro_id": pro_id,
            "name": protocol,
            "parsed": parsed,
            "parsed_bytes": 100,
            "remain": 0,
        },
    }


def test_normalize_xianyuan_duel_play_info_exposes_team_power_and_three_targets() -> None:
    targets = [
        _xianyuan_target(1, "甲", 3200, 100, 22001),
        _xianyuan_target(2, "乙", 3100, 200, 22002),
        _xianyuan_target(3, "丙", 3000, 300, 22003),
    ]

    result = normalize_fanxiu_xianyuan_duel_record(
        _xianyuan_record(
            protocol="SM_PartnerArenaPlayInfo",
            pro_id=90102,
            pcap_name="fanxiu_runtime_20260724_230000.pcap",
            frame_index=10,
            targets=targets,
            self_power=999,
            remaining_challenges=5,
            remaining_refreshes=1,
        )
    )

    assert result["ok"] is True
    assert result["targets_complete"] is True
    assert result["self_power"] == 999
    assert result["remaining_challenges"] == 5
    assert result["remaining_refreshes"] == 1
    assert [item["name"] for item in result["targets"]] == ["甲", "乙", "丙"]


def test_normalize_xianyuan_duel_exposes_ordered_structured_formations() -> None:
    partner_ids = [16, 23, 9, 2, 1]
    targets = [_xianyuan_target(index + 1, f"目标{index}", 3200 - index, 100, 22001) for index in range(3)]
    for index, target in enumerate(targets):
        ids = [28, 2, 1, 30, 9]
        target["teamVO"].update(
            {
                "partnerIds": {"_count": 5, "items": ids},
                "teamDetail": {
                    "_count": 5,
                    "items": [{"_super": {"partnerId": value, "fightPower": 10}} for value in ids],
                },
            }
        )
    record = _xianyuan_record(
        protocol="SM_PartnerArenaPlayInfo",
        pro_id=90102,
        pcap_name="formation.pcap",
        frame_index=1,
        targets=targets,
        self_power=999,
        remaining_challenges=1,
        remaining_refreshes=1,
    )
    self_team = record["payload"]["parsed"]["joinerVO"]["teams"]["items"][0]
    self_team.update(
        {
            "type": 0,
            "partnerIds": {"_count": 5, "items": partner_ids},
            "teamDetail": {
                "_count": 5,
                "items": [{"_super": {"partnerId": value, "fightPower": 20}} for value in partner_ids],
            },
        }
    )

    result = normalize_fanxiu_xianyuan_duel_record(record)

    assert result["self_team"]["partner_ids"] == partner_ids
    assert result["self_team"]["formation_complete"] is True
    assert result["targets"][0]["team"]["partner_ids"] == [28, 2, 1, 30, 9]
    assert result["targets"][0]["team"]["formation_complete"] is True


def test_latest_xianyuan_duel_facts_combines_latest_targets_with_joiner_fields(monkeypatch) -> None:
    first_targets = [
        _xianyuan_target(1, "甲", 3200, 100, 22001),
        _xianyuan_target(2, "乙", 3100, 200, 22002),
        _xianyuan_target(3, "丙", 3000, 300, 22003),
    ]
    refreshed_targets = [
        _xianyuan_target(4, "丁", 3300, 400, 22004),
        _xianyuan_target(5, "戊", 3250, 500, 22005),
        _xianyuan_target(6, "己", 3150, 600, 22006),
    ]
    play_info = _xianyuan_record(
        protocol="SM_PartnerArenaPlayInfo",
        pro_id=90102,
        pcap_name="fanxiu_runtime_20260724_230000.pcap",
        frame_index=10,
        targets=first_targets,
        self_power=999,
        remaining_challenges=2,
        remaining_refreshes=1,
    )
    refresh = _xianyuan_record(
        protocol="SM_PartnerArenaRefresh",
        pro_id=90116,
        pcap_name="fanxiu_runtime_20260724_230100.pcap",
        frame_index=20,
        targets=refreshed_targets,
        remaining_refreshes=0,
    )
    monkeypatch.setattr(
        current_facts_module,
        "list_fanxiu_packet_decoded_records",
        lambda *_args, **_kwargs: {"ok": True, "records": [play_info, refresh]},
    )

    result = get_latest_fanxiu_xianyuan_duel_facts(object())

    assert result["available"] is True
    assert result["protocol"] == "SM_PartnerArenaRefresh"
    assert result["self_power"] == 999
    assert result["remaining_challenges"] == 2
    assert result["remaining_refreshes"] == 0
    assert [item["name"] for item in result["targets"]] == ["丁", "戊", "己"]


def test_latest_xianyuan_duel_facts_does_not_fall_back_to_older_targets(monkeypatch) -> None:
    complete = _xianyuan_record(
        protocol="SM_PartnerArenaPlayInfo",
        pro_id=90102,
        pcap_name="fanxiu_runtime_20260724_230000.pcap",
        frame_index=10,
        targets=[
            _xianyuan_target(1, "甲", 3200, 100, 22001),
            _xianyuan_target(2, "乙", 3100, 200, 22002),
            _xianyuan_target(3, "丙", 3000, 300, 22003),
        ],
        self_power=999,
        remaining_challenges=2,
        remaining_refreshes=1,
    )
    incomplete = _xianyuan_record(
        protocol="SM_PartnerArenaRefresh",
        pro_id=90116,
        pcap_name="fanxiu_runtime_20260724_230100.pcap",
        frame_index=20,
        targets=[_xianyuan_target(4, "丁", 3300, 400, 22004)],
        remaining_refreshes=0,
    )
    monkeypatch.setattr(
        current_facts_module,
        "list_fanxiu_packet_decoded_records",
        lambda *_args, **_kwargs: {"ok": True, "records": [complete, incomplete]},
    )

    result = get_latest_fanxiu_xianyuan_duel_facts(object())

    assert result["available"] is False
    assert result["reason"] == "latest_targets_incomplete"
    assert [item["name"] for item in result["targets"]] == ["丁"]


