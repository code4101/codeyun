from __future__ import annotations

from backend.core.fanxiu.instrumentation import activity_rank_runtime, resource_ranking


class FakeReader:
    @staticmethod
    def fields(value):
        return value

    @staticmethod
    def dictionary_fields(value):
        return value

    @staticmethod
    def list_items(value):
        return value, len(value)


def test_lingzhuang_huadao_rank_data_reads_runtime_rows(monkeypatch):
    rank_rows = [
        {
            "rank": rank,
            "score": 1_500_000 - rank * 1_000,
            "name": f"玩家{rank}",
            "serverId": 22055,
            "clubName": "示例宗门",
        }
        for rank in (1, 2, 4, 8, 16, 32)
    ]
    monkeypatch.setattr(
        activity_rank_runtime,
        "manager_index_fields",
        lambda *_args, **_kwargs: {
            "inst": {
                "Model": {
                    "ActivityrankData": {
                        "V_RankDataDic": {
                            44307: {
                                "selfRankVO": {
                                    "rank": -1,
                                    "score": 0,
                                    "name": "止清ღ羊驼",
                                    "serverId": 22077,
                                },
                                "rankVOS": rank_rows,
                                "rankListSize": 108,
                            }
                        }
                    }
                }
            }
        },
    )

    result = resource_ranking._rank_data(
        FakeReader(),
        0x2000,
        44307,
        reward_tiers=[
            {"rank_start": 1, "rank_end": 1, "rewards": ["Item|9070095_160"]},
            {"rank_start": 2, "rank_end": 2, "rewards": ["Item|9070095_160"]},
            {"rank_start": 3, "rank_end": 4, "rewards": ["Item|9070095_120"]},
            {"rank_start": 5, "rank_end": 8, "rewards": ["Item|9070095_80"]},
            {"rank_start": 9, "rank_end": 16, "rewards": ["Item|9070095_60"]},
            {"rank_start": 17, "rank_end": 32, "rewards": ["Item|9070095_40"]},
            {"rank_start": 33, "rank_end": 64, "rewards": ["Item|9070095_30"]},
            {"rank_start": 65, "rank_end": 128, "rewards": ["Item|9070095_20"]},
        ],
    )

    assert result["rank_list_size"] == 108
    assert result["loaded_rank_count"] == 6
    assert result["self_ranking"]["rank"] == -1
    assert result["self_ranking"]["server_name"] == "岁序更替"
    assert [row["rank"] for row in result["rankings"]] == [
        1,
        2,
        4,
        8,
        16,
        32,
        64,
        -1,
    ]
    assert result["reward_guard_ranks"] == [1, 2, 4, 8, 16, 32, 64]
    assert result["rankings"][0]["name"] == "玩家1"
    assert result["rankings"][0]["is_reward_guard"] is True
    assert [row["talent_pill_count"] for row in result["rankings"][:7]] == [
        160,
        160,
        120,
        80,
        60,
        40,
        30,
    ]
    assert result["rankings"][0]["score_per_talent_pill"] == 1499000 / 160
    assert result["rankings"][-2]["has_player"] is False
    assert result["rankings"][-2]["reward_rank_start"] == 33
    assert not any(row["is_last_player"] for row in result["rankings"])
    assert result["rankings"][-1]["is_self"] is True
    assert result["rankings"][-1]["name"] == "止清ღ羊驼"


def test_lingzhuang_huadao_plane_rank_data_keeps_all_planes(monkeypatch):
    monkeypatch.setattr(
        activity_rank_runtime,
        "manager_index_fields",
        lambda *_args, **_kwargs: {
            "inst": {
                "Model": {
                    "ActivityrankData": {
                        "V_RankDataDic": {
                            44308: {
                                "selfRankVO": {
                                    "rank": 3,
                                    "score": 271039,
                                    "key": "22077",
                                    "serverId": 22077,
                                },
                                "rankVOS": [
                                    {
                                        "rank": 1,
                                        "score": 1519825,
                                        "key": "22055",
                                    },
                                    {
                                        "rank": 2,
                                        "score": 294875,
                                        "key": "22068",
                                        "serverId": 22068,
                                    },
                                    {
                                        "rank": 3,
                                        "score": 271039,
                                        "key": "22077",
                                        "serverId": 22077,
                                    },
                                ],
                                "rankListSize": 16,
                            }
                        }
                    }
                }
            }
        },
    )

    result = resource_ranking._rank_data(
        FakeReader(),
        0x2000,
        44308,
        key_points_only=False,
    )

    assert result["rank_list_size"] == 16
    assert [row["rank"] for row in result["rankings"]] == [1, 2, 3]
    assert [row["server_name"] for row in result["rankings"]] == [
        "鸾凤和鸣",
        "自由飞翔",
        "岁序更替",
    ]
    assert result["rankings"][-1]["is_self"] is True
