from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.standard_observation import (
    ActivityObservationSpec,
    collect_standard_activity_observation,
)
from backend.models import FanxiuPacketBusinessRecord


def test_standard_activity_observation_is_driven_only_by_declared_ids() -> None:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(
        engine,
        tables=[FanxiuPacketBusinessRecord.__table__],
    )
    with Session(engine) as session:
        session.add(
            FanxiuPacketBusinessRecord(
                domain="resource_state",
                record_key="currency:19",
                protocol="SM_RewardResult",
                entity_id="19",
                captured_at="2026-08-02 20:22:54",
                payload={
                    "amount": 341222,
                    "history": 351222,
                    "borrow": 10000,
                },
            )
        )
        session.add(
            FanxiuPacketBusinessRecord(
                domain="activity_rank",
                record_key="210802|0",
                protocol="SM_ActivityRankSync",
                entity_id="210802",
                captured_at="2026-08-02 20:22:52",
                payload={
                    "snapshot": {
                        "rank_vo_type": "ActivityRankCrossServerVO",
                        "rank_list_size": 2,
                        "personal_item": {
                            "rank": 2,
                            "score": 8473998,
                            "id": 22077,
                            "key": "22077",
                            "name": "",
                        },
                        "items": [
                            {"rank": 1, "score": 53384285, "id": 22088, "key": "22088", "name": ""},
                            {"rank": 2, "score": 8473998, "id": 22077, "key": "22077", "name": ""},
                        ],
                    }
                },
            )
        )
        session.add(
            FanxiuPacketBusinessRecord(
                domain="activity_rank",
                record_key="210801|0",
                protocol="SM_ActivityRankSync",
                entity_id="210801",
                captured_at="2026-08-02 20:22:51",
                payload={
                    "snapshot": {
                        "rank_list_size": 141,
                        "personal_item": {
                            "rank": 17,
                            "score": 1496728,
                            "key": "self",
                            "name": "自己",
                            "server_id": 22077,
                            "server_name": "岁序更替",
                            "club_name": "凌霄道宗",
                        },
                        "items": [
                            {
                                "rank": 1,
                                "score": 12812590,
                                "key": "first",
                                "name": "第一名",
                                "server_id": 22088,
                                "server_name": "喜笑颜开",
                            }
                        ],
                    }
                },
            )
        )
        session.commit()

        result = collect_standard_activity_observation(
            session,
            ActivityObservationSpec(rank_activity_id=210801, currency_type=19),
            reward_tiers=[
                {"rank_start": 1, "rank_end": 1},
                {"rank_start": 17, "rank_end": 32},
            ],
        )

    assert result["source"] == "standard_runtime_facts"
    assert result["score"] == 1496728
    assert result["rank"] == 17
    assert result["exchange_currency"] == 331222
    assert result["cumulative_currency"] == 351222
    personal = [row for row in result["rankings"] if row["ranking_scope"] == "personal"]
    plane = [row for row in result["rankings"] if row["ranking_scope"] == "plane"]
    assert [(row["rank"], row["is_self"]) for row in personal] == [
        (1, False),
        (17, True),
        (141, False),
    ]
    assert personal[-1]["is_last_player"] is True
    assert personal[-1]["has_player"] is False
    assert personal[1]["reward_rank_start"] == 17
    assert personal[1]["reward_rank_end"] == 32
    assert [(row["rank"], row["server_id"], row["is_self"]) for row in plane] == [
        (1, 22088, False),
        (2, 22077, True),
    ]
