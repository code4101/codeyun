import json
from datetime import date

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.exchange_event import (
    list_exchange_rankings,
    replace_exchange_rankings,
    upsert_exchange_activity_snapshot,
)
from backend.core.fanxiu.activity import resource_ranking
from backend.models import FanxiuPacketBusinessRecord


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _activity(session: Session) -> str:
    return upsert_exchange_activity_snapshot(
        session,
        {
            "activity_type": "lingzhuang-huadao",
            "cross_count": 16,
            "start_date": "2026-08-03",
            "end_date": "2026-08-04",
            "game_rank_activity_id": 44307,
            "currency_name": "玄铁",
            "captured_at": "",
            "source_kind": "activity_instance",
        },
    )


def test_collect_lingzhuang_huadao_persists_instance_rankings(monkeypatch) -> None:
    monkeypatch.setattr(
        resource_ranking,
        "read_lingzhuang_huadao_snapshot",
        lambda: {
            "ok": True,
            "complete": True,
            "captured_at": "2026-08-03T18:30:00+08:00",
            "rank_list_size": 111,
            "loaded_rank_count": 100,
            "plane_rank_list_size": 16,
            "plane_loaded_rank_count": 16,
            "rankings": [
                {
                    "rank": 1,
                    "score": 1_454_000,
                    "role_key": "role-1",
                    "name": "榜首",
                    "server_id": 22055,
                    "server_name": "鸾凤和鸣",
                    "club_name": "永昼",
                    "is_self": False,
                    "is_reward_guard": True,
                    "reward_rank_start": 1,
                    "reward_rank_end": 1,
                    "talent_pill_count": 160,
                    "has_player": True,
                    "is_last_player": False,
                },
                {
                    "rank": 108,
                    "score": 448,
                    "role_key": "self",
                    "name": "止清ღ羊驼",
                    "server_id": 22077,
                    "server_name": "岁序更替",
                    "club_name": "凌霄道宗",
                    "is_self": True,
                    "is_reward_guard": False,
                    "reward_rank_start": None,
                    "reward_rank_end": None,
                    "talent_pill_count": None,
                    "has_player": True,
                    "is_last_player": False,
                },
            ],
            "plane_rankings": [
                {
                    "rank": 1,
                    "score": 1_521_000,
                    "role_key": "22055",
                    "name": "",
                    "server_id": 22055,
                    "server_name": "鸾凤和鸣",
                    "club_name": "",
                    "is_self": False,
                    "is_reward_guard": False,
                    "reward_rank_start": None,
                    "reward_rank_end": None,
                    "talent_pill_count": None,
                    "has_player": True,
                    "is_last_player": False,
                },
                {
                    "rank": 3,
                    "score": 271_000,
                    "role_key": "22077",
                    "name": "",
                    "server_id": 22077,
                    "server_name": "岁序更替",
                    "club_name": "",
                    "is_self": False,
                    "is_reward_guard": False,
                    "reward_rank_start": None,
                    "reward_rank_end": None,
                    "talent_pill_count": None,
                    "has_player": True,
                    "is_last_player": False,
                }
            ],
            "evidence": {"pid": 123},
        },
    )

    with _session() as session:
        activity_id = _activity(session)
        detail = resource_ranking.collect_and_store_lingzhuang_huadao_activity(
            session,
            activity_id=activity_id,
            today=date(2026, 8, 3),
        )
        # A current activity is refreshed repeatedly. Replacing the same
        # rank keys a second time must not violate the unique constraint.
        detail = resource_ranking.collect_and_store_lingzhuang_huadao_activity(
            session,
            activity_id=activity_id,
            today=date(2026, 8, 3),
        )
        personal = list_exchange_rankings(
            session,
            activity_type="lingzhuang-huadao",
            activity_id=activity_id,
            ranking_scope="personal",
            page_size=100,
        )
        plane = list_exchange_rankings(
            session,
            activity_type="lingzhuang-huadao",
            activity_id=activity_id,
            ranking_scope="plane",
            page_size=100,
        )

        assert detail is not None
        assert detail.label == "16跨,2026/8/3-8/4"
        assert detail.captured_at == "2026-08-03T18:30:00+08:00"
        assert next(row for row in personal.items if row.rank == 1).talent_pill_count == 160
        assert not any(row.is_last_player for row in personal.items)
        assert len([row for row in personal.items if row.is_reward_guard]) == 12
        assert [
            (row.reward_rank_start, row.reward_rank_end)
            for row in personal.items
            if row.reward_rank_start is not None
        ] == [
            (1, 1),
            (2, 2),
            (3, 4),
            (5, 8),
            (9, 16),
            (17, 32),
            (33, 64),
            (65, 128),
            (65, 128),
            (129, 256),
            (257, 512),
            (513, 1000),
            (1001, 2000),
        ]
        assert [
            row.has_player
            for row in personal.items
            if (row.reward_rank_end or 0) > 128
        ] == [False, False, False, False]
        assert sum(row.is_self for row in personal.items) == 1
        assert next(row for row in personal.items if row.is_self).reward_rank_end == 128
        assert [row.server_name for row in plane.items] == ["鸾凤和鸣", "岁序更替"]
        assert next(row for row in plane.items if row.server_name == "岁序更替").is_self


def test_collect_lingzhuang_huadao_preserves_snapshot_when_runtime_is_incomplete(
    monkeypatch,
) -> None:
    with _session() as session:
        activity_id = _activity(session)
        replace_exchange_rankings(
            session,
            activity_type="lingzhuang-huadao",
            activity_id=activity_id,
            captured_at="2026-08-03T18:00:00+08:00",
            rows=[
                {
                    "ranking_scope": "plane",
                    "rank": 1,
                    "score": 1_521_000,
                    "role_key": "22055",
                    "server_name": "鸾凤和鸣",
                }
            ],
        )
        monkeypatch.setattr(
            resource_ranking,
            "read_lingzhuang_huadao_snapshot",
            lambda: {
                "ok": True,
                "complete": True,
                "captured_at": "2026-08-03T18:40:00+08:00",
                "rank_list_size": 116,
                "loaded_rank_count": 0,
                "rankings": [],
                "plane_rank_list_size": 16,
                "plane_loaded_rank_count": 0,
                "plane_rankings": [],
            },
        )

        try:
            resource_ranking.collect_and_store_lingzhuang_huadao_activity(
                session,
                activity_id=activity_id,
                today=date(2026, 8, 3),
            )
        except ValueError as exc:
            assert "保留上次快照" in str(exc)
        else:
            raise AssertionError("incomplete runtime data must not replace the snapshot")

        plane = list_exchange_rankings(
            session,
            activity_type="lingzhuang-huadao",
            activity_id=activity_id,
            ranking_scope="plane",
            page_size=100,
        )
        assert plane.last_captured_at == "2026-08-03T18:00:00+08:00"
        assert [row.server_name for row in plane.items] == ["鸾凤和鸣"]


def test_collect_lingzhuang_huadao_rejects_inactive_instance() -> None:
    with _session() as session:
        activity_id = _activity(session)
        try:
            resource_ranking.collect_and_store_lingzhuang_huadao_activity(
                session,
                activity_id=activity_id,
                today=date(2026, 8, 5),
            )
        except ValueError as exc:
            assert "不在有效日期内" in str(exc)
        else:
            raise AssertionError("inactive activity refresh must fail")


def test_collect_yaochi_flower_festival_persists_visible_ranking(monkeypatch) -> None:
    reward_tiers = [
        {"rank_start": 1, "rank_end": 1, "rewards": ["Item|9070095_10"]},
        {"rank_start": 2, "rank_end": 2, "rewards": ["Item|9070095_10"]},
        {"rank_start": 3, "rank_end": 3, "rewards": ["Item|9070095_7"]},
        {"rank_start": 4, "rank_end": 8, "rewards": ["Item|9070095_5"]},
    ]
    monkeypatch.setattr(
        resource_ranking,
        "load_activity_rank_reward_tiers",
        lambda **_: reward_tiers,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.rank_reward.load_activity_rank_reward_tiers",
        lambda **_: reward_tiers,
    )
    monkeypatch.setattr(
        resource_ranking,
        "read_activity_rank_fact",
        lambda _session, _rank_activity_id: {
            "rank": -1,
            "score": 0,
            "role_key": "self",
            "name": "止清ღ羊驼",
            "server_id": 22077,
            "server_name": "岁序更替",
            "club_name": "凌霄道宗",
            "rank_list_size": 4,
            "items": [
                {
                    "rank": 1,
                    "score": 103_635,
                    "key": "role-1",
                    "name": "凌霄，锋哥",
                    "server_id": 22077,
                    "server_name": "岁序更替",
                    "club_name": "凌霄道宗",
                },
                {
                    "rank": 4,
                    "score": 11,
                    "key": "role-4",
                    "name": "凌霄༅青风”",
                    "server_id": 22077,
                    "server_name": "岁序更替",
                    "club_name": "凌霄道宗",
                },
                {
                    "rank": 5,
                    "score": 67_168,
                    "key": "stale-other-list-row",
                    "name": "不应写入",
                    "server_id": 22077,
                    "server_name": "岁序更替",
                    "club_name": "凌霄阁",
                },
            ],
            "captured_at": "2026-08-05 19:00:25",
            "protocol": "SM_ActivityRankSync",
            "evidence": {"packet_id": "packet-1"},
        },
    )

    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(
            session,
            {
                "activity_type": "yaochi-flower-festival",
                "cross_count": 1,
                "start_date": "2026-08-05",
                "end_date": "2026-08-05",
                "game_rank_activity_id": 1042811,
                "currency_name": "仙花友好度",
                "source_kind": "worldline_activity",
                "evidence": {"server_day": 473, "world_level": 220},
            },
        )
        detail = resource_ranking.collect_and_store_yaochi_flower_festival_activity(
            session,
            activity_id=activity_id,
            today=date(2026, 8, 5),
        )
        ranking = list_exchange_rankings(
            session,
            activity_type="yaochi-flower-festival",
            activity_id=activity_id,
            ranking_scope="personal",
            page_size=100,
        )

        assert detail is not None
        assert detail.label == "1跨,2026/8/5"
        assert [(row.rank, row.name) for row in ranking.items] == [
            (1, "凌霄，锋哥"),
            (2, ""),
            (3, ""),
            (4, "凌霄༅青风”"),
            (8, ""),
            (-1, "止清ღ羊驼"),
        ]
        assert ranking.items[0].is_reward_guard
        assert ranking.items[0].talent_pill_count == 10
        assert ranking.items[3].is_last_player
        assert ranking.items[4].reward_rank_start == 4
        assert ranking.items[4].reward_rank_end == 8
        assert ranking.items[-1].is_self


def test_load_yaochi_flower_task_milestones_keeps_full_ladder(tmp_path) -> None:
    task_dir = tmp_path / "parsed_configs" / "ActiveTask"
    task_dir.mkdir(parents=True)
    targets = [
        500,
        1000,
        2000,
        3000,
        4000,
        5000,
        7000,
        10000,
        15000,
        20000,
        30000,
        40000,
        50000,
        60000,
    ]
    talent_pills = {5000: 1, 10000: 1, 20000: 2, 40000: 2, 60000: 4}
    rows = [
        {
            "id": 104281150 + order,
            "activityId": 1042811,
            "name_plain": f"赠送仙缘{order}",
            "finishCondition": [f"NpcFlower|{target}"],
            "reward": (
                [f"Item|9070095_{talent_pills[target]}_7"]
                if target in talent_pills
                else ["Item|9020001_1"]
            ),
            "sort": order,
            "corner_plain": "必拿" if order <= 3 else "",
        }
        for order, target in enumerate(targets, start=1)
    ]
    rows.extend(
        {
            "id": 104281100 + order,
            "activityId": 1042811,
            "name_plain": f"旧梯度{order}",
            "finishCondition": [f"NpcFlower|{target}"],
            "reward": (
                [f"Item|9070095_{talent_pills[target]}_7"]
                if target in talent_pills
                else ["Item|9020001_1"]
            ),
            "sort": order,
        }
        for order, target in enumerate(
            [
                500,
                1000,
                2000,
                4000,
                7000,
                10000,
                15000,
                20000,
                30000,
                40000,
                50000,
                60000,
            ],
            start=1,
        )
    )
    (task_dir / "rows.json").write_text(json.dumps(rows), encoding="utf-8")

    milestones = resource_ranking.load_yaochi_flower_task_milestones(
        rank_activity_id=1042811,
        export_root=tmp_path,
    )

    assert [row["target"] for row in milestones] == targets
    assert [
        (row["target"], row["talent_pill_count"])
        for row in milestones
        if row["talent_pill_count"] > 0
    ] == [(5000, 1), (10000, 1), (20000, 2), (40000, 2), (60000, 4)]
    assert sum(row["talent_pill_count"] for row in milestones) == 10


def test_collect_cross_server_yaochi_flower_persists_declared_plane_board(monkeypatch) -> None:
    monkeypatch.setattr(resource_ranking, "load_activity_rank_reward_tiers", lambda **_: [])
    monkeypatch.setattr(
        resource_ranking,
        "resolve_yaochi_flower_activity_references",
        lambda **_: {
            "template_activity_id": 8042801,
            "task_activity_id": 8042801,
            "personal_rank_activity_id": 42851,
            "plane_rank_activity_id": 42852,
        },
    )

    def read_rank(_session, rank_activity_id):
        if rank_activity_id == 42852:
            return {
                "rank": 5, "score": 2208665, "rank_list_size": 8,
                "items": [
                    {"rank": 1, "score": 18479137, "id": 22001, "server_name": "海浪无声"},
                    {"rank": 5, "score": 2208665, "id": 22077, "server_name": "岁序更替"},
                ],
                "captured_at": "2026-08-07 21:24:00", "protocol": "SM_ActivityRankSync", "evidence": {},
            }
        return {
            "rank": 8, "score": 773156, "role_key": "self", "name": "止清ღ羊驼",
            "server_id": 22077, "server_name": "岁序更替", "club_name": "凌霄道宗",
            "rank_list_size": 8,
            "items": [{"rank": 8, "score": 773156, "key": "self", "name": "止清ღ羊驼", "server_id": 22077, "server_name": "岁序更替"}],
            "captured_at": "2026-08-07 21:24:00", "protocol": "SM_ActivityRankSync", "evidence": {},
        }

    monkeypatch.setattr(resource_ranking, "read_activity_rank_fact", read_rank)
    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(
            session,
            {
                "activity_type": "yaochi-flower-festival", "cross_count": 8,
                "start_date": "2026-08-06", "end_date": "2026-08-07",
                "game_rank_activity_id": 42851,
                "evidence": {"server_day": 475, "world_level": 220},
            },
        )
        resource_ranking.collect_and_store_yaochi_flower_festival_activity(
            session, activity_id=activity_id, today=date(2026, 8, 7)
        )
        plane = list_exchange_rankings(
            session,
            activity_type="yaochi-flower-festival",
            activity_id=activity_id,
            ranking_scope="plane",
            page_size=100,
        )

    assert [(row.rank, row.server_name, row.is_self) for row in plane.items] == [
        (1, "海浪无声", False),
        (5, "岁序更替", True),
    ]


def test_resolve_yaochi_flower_reuses_cross_server_parent_references(tmp_path) -> None:
    activity_dir = tmp_path / "parsed_configs" / "Activity"
    activity_dir.mkdir(parents=True)
    (activity_dir / "rows.json").write_text(
        json.dumps(
            [
                {"id": 8042801, "name_plain": "瑶池花会", "crossGroup": 8, "follow": [42851, 42852]},
                {"id": 42851, "name_plain": "个人", "crossGroup": 8, "baseId": 42801},
                {"id": 42852, "name_plain": "位面", "crossGroup": 8, "baseId": 42802},
                {"id": 1042811, "name_plain": "瑶池花会"},
            ]
        ),
        encoding="utf-8",
    )

    cross_references = resource_ranking.resolve_yaochi_flower_activity_references(
        rank_activity_id=42851,
        cross_count=8,
        export_root=tmp_path,
    )
    local_references = resource_ranking.resolve_yaochi_flower_activity_references(
        rank_activity_id=1042811,
        cross_count=1,
        export_root=tmp_path,
    )

    assert cross_references == {
        "template_activity_id": 8042801,
        "task_activity_id": 8042801,
        "personal_rank_activity_id": 42851,
        "plane_rank_activity_id": 42852,
    }
    assert local_references["task_activity_id"] == 1042811
    assert local_references["plane_rank_activity_id"] is None


def test_load_yuanding_sansheng_task_milestones(tmp_path) -> None:
    task_dir = tmp_path / "parsed_configs" / "ActiveTask"
    task_dir.mkdir(parents=True)
    (task_dir / "rows.json").write_text(
        json.dumps(
            [
                {
                    "id": 1604510101,
                    "activityId": 16045101,
                    "name_plain": "缘定三生一",
                    "finishCondition": ["MarriageScore|60000"],
                    "reward": ["Item|9070095_1", "Item|9020001_2"],
                    "sort": 1,
                    "corner_plain": "必拿",
                },
                {
                    "id": 1604510102,
                    "activityId": 16045101,
                    "name_plain": "缘定三生二",
                    "finishCondition": ["MarriageScore|120000"],
                    "reward": ["Item|9020004_2"],
                    "sort": 2,
                },
            ]
        ),
        encoding="utf-8",
    )

    milestones = resource_ranking.load_yuanding_sansheng_task_milestones(
        export_root=tmp_path,
    )

    assert [(row["target"], row["talent_pill_count"]) for row in milestones] == [
        (60_000, 1),
        (120_000, 0),
    ]
    assert milestones[0]["must_get"] is True


def test_yuanding_sansheng_projects_worldline_and_collects_both_rankings(
    monkeypatch,
) -> None:
    with _session() as session:
        session.add(
            FanxiuPacketBusinessRecord(
                domain="worldline_activity",
                record_key="worldline:16045101",
                entity_id="16045101",
                captured_at="2026-08-08 19:13:31",
                payload={
                    "item": {
                        "activityId": 16045101,
                        "name": "缘定三生",
                        "startTimeText": "2026-08-08 05:00:05",
                        "endTimeText": "2026-08-09 22:00:00",
                        "crossGroup": 64,
                        "serverCount": 16,
                        "serverIds": [22057, 22058],
                    }
                },
            )
        )
        session.commit()

        def read_rank(_session, rank_activity_id):
            if rank_activity_id == 45107:
                return {
                    "rank": 1,
                    "score": 3_980_470_986,
                    "role_key": "2",
                    "name": "",
                    "rank_list_size": 2,
                    "items": [
                        {"rank": 1, "score": 3_980_470_986, "id": 2},
                        {"rank": 2, "score": 978_260_055, "id": 1},
                    ],
                    "captured_at": "2026-08-08 19:13:31",
                    "protocol": "SM_ActivityRankSync",
                }
            return {
                "rank": 8,
                "score": 254_169_750,
                "role_key": "self",
                "name": "止清羊驼",
                "server_id": 22077,
                "server_name": "岁序更替",
                "club_name": "凌霄道宗",
                "rank_list_size": 8,
                "items": [
                    {
                        "rank": 8,
                        "score": 254_169_750,
                        "key": "self",
                        "name": "止清羊驼",
                        "server_id": 22077,
                        "server_name": "岁序更替",
                    }
                ],
                "captured_at": "2026-08-08 19:13:31",
                "protocol": "SM_ActivityRankSync",
            }

        monkeypatch.setattr(resource_ranking, "read_activity_rank_fact", read_rank)
        detail = resource_ranking.collect_and_store_yuanding_sansheng_activity(
            session,
            today=date(2026, 8, 8),
        )
        personal = list_exchange_rankings(
            session,
            activity_type="yuanding-sansheng",
            activity_id=detail.id,
            ranking_scope="personal",
            page_size=100,
        )
        group = list_exchange_rankings(
            session,
            activity_type="yuanding-sansheng",
            activity_id=detail.id,
            ranking_scope="plane",
            page_size=100,
        )

    assert detail.label == "16跨,2026/8/8-8/9"
    assert detail.game_rank_activity_id == 45105
    assert detail.resource_strategy["entry"] == "仙府 → 弟子 → 联姻"
    assert [(row.rank, row.score, row.is_self) for row in personal.items] == [
        (8, 254_169_750, True),
    ]
    assert [(row.rank, row.score, row.is_self) for row in group.items] == [
        (1, 3_980_470_986, True),
        (2, 978_260_055, False),
    ]
