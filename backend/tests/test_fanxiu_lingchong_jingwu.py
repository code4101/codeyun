import json
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity import lingchong_jingwu
from backend.models import FanxiuExchangeActivity, FanxiuPacketBusinessRecord, FanxiuPacketDecodedRecord


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _write_rows(root: Path, table: str, rows: list[dict]) -> None:
    path = root / "parsed_configs" / table / "rows.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def _activity_rows() -> list[dict]:
    return [
        {
            "id": 8042901,
            "name_plain": "灵宠竞武",
            "crossGroup": 8,
            "follow": [42905, 42906],
            "baseId": 42900,
        },
        {"id": 42905, "name_plain": "个人", "baseId": 42901},
        {"id": 42906, "name_plain": "位面", "baseId": 42902},
    ]


def test_resolve_lingchong_jingwu_references_uses_official_activity_graph(
    tmp_path: Path,
) -> None:
    _write_rows(tmp_path, "Activity", _activity_rows())

    result = lingchong_jingwu.resolve_lingchong_jingwu_references(
        export_root=tmp_path
    )

    assert result.activity_type == "lingchong-jingwu"
    assert result.official_name == "灵宠竞武"
    assert result.user_alias == "灵武竞宠"
    assert result.cross_count == 8
    assert result.personal_rank_activity_id == 42905
    assert result.plane_rank_activity_id == 42906


def test_resolve_lingchong_jingwu_references_rejects_wrong_cross_count(
    tmp_path: Path,
) -> None:
    rows = _activity_rows()
    rows[0]["crossGroup"] = 4
    _write_rows(tmp_path, "Activity", rows)

    with pytest.raises(ValueError, match="不是 8 跨"):
        lingchong_jingwu.resolve_lingchong_jingwu_references(
            export_root=tmp_path
        )


def test_task_milestones_require_exact_observed_task_ids(tmp_path: Path) -> None:
    _write_rows(
        tmp_path,
        "ActiveTask",
        [
            {
                "id": 804290160,
                "activityId": 8042901,
                "sort": 10,
                "name_plain": "提升资质十",
                "finishCondition": ["PetTalent|2000"],
                "reward": ["Item|9070095_2_7", "Item|9020001_5"],
            },
            {
                "id": 804290165,
                "activityId": 8042901,
                "sort": 1,
                "name_plain": "提升资质一",
                "finishCondition": ["PetTalent|50"],
                "reward": ["Item|3020129_1_7"],
            },
            # A different retained ladder must not be selected implicitly.
            {
                "id": 804290101,
                "activityId": 8042901,
                "sort": 1,
                "name_plain": "旧梯度",
                "finishCondition": ["PetTalent|50"],
                "reward": [],
            },
        ],
    )
    observed = [
        {
            "taskId": 804290160,
            "status": 3,
            "progressList": {
                "items": [{"progress": 1200, "target": 2000, "finish": False}]
            },
        },
        {
            "taskId": 804290165,
            "status": 3,
            "progressList": {
                "items": [{"progress": 50, "target": 50, "finish": True}]
            },
        },
    ]

    result = lingchong_jingwu.load_lingchong_jingwu_task_milestones(
        observed, export_root=tmp_path
    )

    assert [row.task_id for row in result] == [804290165, 804290160]
    assert result[0].finished is True
    assert result[1].progress == 1200
    assert result[1].talent_pill_count == 2


def test_task_milestones_fail_closed_without_runtime_tasks(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="拒绝从多套静态梯度猜测"):
        lingchong_jingwu.load_lingchong_jingwu_task_milestones(
            [], export_root=tmp_path
        )


def test_task_milestones_reject_runtime_config_target_mismatch(
    tmp_path: Path,
) -> None:
    _write_rows(
        tmp_path,
        "ActiveTask",
        [
            {
                "id": 804290160,
                "activityId": 8042901,
                "sort": 10,
                "finishCondition": ["PetTalent|2000"],
            }
        ],
    )

    with pytest.raises(ValueError, match="Runtime/配置目标不一致"):
        lingchong_jingwu.load_lingchong_jingwu_task_milestones(
            [
                {
                    "taskId": 804290160,
                    "progressList": {"items": [{"progress": 0, "target": 3000}]},
                }
            ],
            export_root=tmp_path,
        )


def test_resource_snapshot_preserves_pet_type_specific_aptitude_gain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_rows(
        tmp_path,
        "Item",
        [
            {
                "id": 8022001,
                "name_plain": "珍品饲灵丸",
                "quality": 4,
                "effectValue": "1:4,2:4,3:2",
            },
            {
                "id": 8022002,
                "name_plain": "绝品饲灵丸",
                "quality": 5,
                "effectValue": "1:20,2:20,3:10",
            },
            # Similar names without a calculable effect are not guessed in.
            {"id": 1570002, "name_plain": "灵兽饲灵丸", "quality": 3},
        ],
    )
    monkeypatch.setattr(
        lingchong_jingwu,
        "read_backpack_item_counts",
        lambda item_ids, **_: (
            {item_id: {8022001: 11, 8022002: 3}.get(item_id, 0) for item_id in item_ids},
            {"read_only": True, "discovery": "loaded_global"},
        ),
    )

    snapshot = lingchong_jingwu.collect_lingchong_jingwu_resource_snapshot(
        activity_id="lingchong-jingwu-8-2026-08-12",
        export_root=tmp_path,
    )

    assert snapshot.complete is True
    assert snapshot.total_count == 14
    assert [row.item_id for row in snapshot.items] == [8022001, 8022002]
    assert snapshot.items[0].minimum_aptitude_gain == 2
    assert snapshot.items[0].maximum_aptitude_gain == 4
    assert snapshot.evidence["score_requires_pet_type"] is True


def _rank_fact(*, scope: str, declared: int = 2) -> dict:
    return {
        "rank_vo_type": (
            "ActivityRankPersonalVO"
            if scope == "personal"
            else "ActivityRankCrossServerVO"
        ),
        "rank_list_size": declared,
        "rank": 2,
        "items": [
            {"rank": 1, "id": 101, "key": "101", "name": "甲", "score": 90},
            {"rank": 2, "id": 102, "key": "102", "name": "乙", "score": 80},
        ],
    }


@pytest.mark.parametrize("scope", ["personal", "plane"])
def test_rank_projection_keeps_complete_real_rows(scope: str) -> None:
    rows = lingchong_jingwu.project_lingchong_jingwu_rank_rows(
        _rank_fact(scope=scope), scope=scope
    )

    assert len(rows) == 2
    assert rows[1]["is_self"] is True
    assert rows[1]["is_last_player"] is True
    assert rows[0]["raw_data"]["scope_complete"] is True
    if scope == "plane":
        assert rows[0]["server_id"] == 101


def test_rank_projection_rejects_partial_or_wrong_vo() -> None:
    partial = _rank_fact(scope="personal", declared=3)
    with pytest.raises(ValueError, match="不完整"):
        lingchong_jingwu.project_lingchong_jingwu_rank_rows(
            partial, scope="personal"
        )

    wrong_vo = _rank_fact(scope="plane")
    with pytest.raises(ValueError, match="VO 类型不匹配"):
        lingchong_jingwu.project_lingchong_jingwu_rank_rows(
            wrong_vo, scope="personal"
        )


def test_activity_payload_binds_worldline_identity_and_both_rank_scopes(
    tmp_path: Path,
) -> None:
    _write_rows(tmp_path, "Activity", _activity_rows())
    references = lingchong_jingwu.resolve_lingchong_jingwu_references(
        export_root=tmp_path
    )

    payload = lingchong_jingwu.build_lingchong_jingwu_activity_payload(
        {
            "activityId": 8042901,
            "name": "灵宠竞武",
            "serverCount": 8,
            "startTime": 1786482005000,
            "endTime": 1786629600000,
        },
        references=references,
        captured_at="2026-08-12T05:00:00+08:00",
    )

    assert payload["activity_type"] == "lingchong-jingwu"
    assert payload["start_date"] == "2026-08-12"
    assert payload["end_date"] == "2026-08-13"
    assert payload["evidence"]["rank_scope_activity_ids"] == {
        "personal": 42905,
        "plane": 42906,
    }


def test_observed_tasks_use_current_server_membership_not_all_static_ladders(
    tmp_path: Path,
) -> None:
    _write_rows(
        tmp_path,
        "ActiveTask",
        [
            {
                "id": 804290160,
                "activityId": 8042901,
                "sort": 1,
                "name_plain": "本期一",
                "finishCondition": ["PetTalent|50"],
                "reward": ["Item|9070095_1_7"],
            },
            {
                "id": 804290161,
                "activityId": 8042901,
                "sort": 2,
                "name_plain": "本期二",
                "finishCondition": ["PetTalent|100"],
                "reward": ["Item|9070095_2_7"],
            },
            {
                "id": 804290101,
                "activityId": 8042901,
                "sort": 1,
                "name_plain": "历史梯度",
                "finishCondition": ["PetTalent|50"],
                "reward": [],
            },
        ],
    )
    with _session() as session:
        session.add(
            FanxiuPacketDecodedRecord(
                packet_id="quest-create",
                frame_index=1,
                name="SM_CreateQuestInfo",
                captured_at="2026-08-12T18:07:08+08:00",
                payload={
                    "parsed": {
                        "entryVOs": {
                            "_count": 2,
                            "items": [
                                {
                                    "taskId": 804290160,
                                    "status": 3,
                                    "progressList": {"items": [{"progress": 50, "target": 50, "finish": True}]},
                                },
                                {
                                    "taskId": 804290161,
                                    "status": 1,
                                    "progressList": {"items": [{"progress": 60, "target": 100, "finish": False}]},
                                },
                            ]
                        }
                    }
                },
            )
        )
        session.commit()

        with pytest.raises(ValueError, match="Runtime.*禁止使用抓包 raw JSON"):
            lingchong_jingwu.load_lingchong_jingwu_observed_tasks(
                session, export_root=tmp_path
            )


def test_observed_tasks_fail_closed_when_decoder_trimmed_membership(
    tmp_path: Path,
) -> None:
    _write_rows(
        tmp_path,
        "ActiveTask",
        [
            {
                "id": 804290160,
                "activityId": 8042901,
                "sort": 10,
                "name_plain": "提升资质十",
                "finishCondition": ["PetTalent|2000"],
                "reward": [],
            }
        ],
    )
    with _session() as session:
        session.add(
            FanxiuPacketDecodedRecord(
                packet_id="trimmed-quest-create",
                name="SM_CreateQuestInfo",
                captured_at="2026-08-12T18:07:08+08:00",
                payload={
                    "parsed": {
                        "entryVOs": {
                            "_count": 14,
                            "_truncated_items": 13,
                            "items": [
                                {
                                    "taskId": 804290160,
                                    "progressList": {"items": [{"progress": 0, "target": 2000}]},
                                }
                            ],
                        }
                    }
                },
            )
        )
        session.commit()
        with pytest.raises(ValueError, match="Runtime.*禁止使用抓包 raw JSON"):
            lingchong_jingwu.load_lingchong_jingwu_observed_tasks(
                session, export_root=tmp_path
            )


def test_observed_tasks_reject_newer_fact_from_another_period(tmp_path: Path) -> None:
    _write_rows(
        tmp_path,
        "ActiveTask",
        [{
            "id": 804290160,
            "activityId": 8042901,
            "sort": 1,
            "name_plain": "本期任务",
            "finishCondition": ["PetTalent|50"],
            "reward": [],
        }],
    )
    with _session() as session:
        for packet_id, captured_at, progress in (
            ("current-period", "2026-08-12T18:00:00+08:00", 50),
            ("next-period", "2026-08-20T18:00:00+08:00", 0),
        ):
            session.add(FanxiuPacketDecodedRecord(
                packet_id=packet_id,
                name="SM_CreateQuestInfo",
                captured_at=captured_at,
                payload={"parsed": {"entryVOs": {"_count": 1, "items": [{
                    "taskId": 804290160,
                    "status": 3,
                    "progressList": {"items": [{"progress": progress, "target": 50, "finish": progress == 50}]},
                }]}}},
            ))
        session.commit()

        with pytest.raises(ValueError, match="Runtime.*禁止使用抓包 raw JSON"):
            lingchong_jingwu.load_lingchong_jingwu_observed_tasks(
                session,
                export_root=tmp_path,
                start_date="2026-08-12",
                end_date="2026-08-13",
            )


def test_resource_snapshot_store_round_trip(tmp_path: Path) -> None:
    snapshot = lingchong_jingwu.LingchongJingwuResourceSnapshot(
        activity_id="lingchong-jingwu-8-2026-08-12-2026-08-13",
        captured_at="2026-08-12T22:00:00+08:00",
        items=[
            lingchong_jingwu.LingchongJingwuResourceItem(
                item_id=8022001,
                name="珍品饲灵丸",
                quality=4,
                count=12,
                aptitude_gain_by_pet_type={1: 4, 3: 2},
                minimum_aptitude_gain=2,
                maximum_aptitude_gain=4,
            )
        ],
        total_count=12,
        evidence={"read_only": True},
    )
    with _session() as session:
        lingchong_jingwu.store_lingchong_jingwu_resource_snapshot(session, snapshot)
        loaded = lingchong_jingwu.load_lingchong_jingwu_resource_snapshot(
            session, activity_id=snapshot.activity_id
        )

    assert loaded == snapshot


def test_ensure_activity_preserves_newer_rank_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    references = lingchong_jingwu.LingchongJingwuReferences(
        parent_activity_id=8042901,
        cross_count=8,
        personal_rank_activity_id=42905,
        plane_rank_activity_id=42906,
    )
    monkeypatch.setattr(
        lingchong_jingwu,
        "resolve_lingchong_jingwu_references",
        lambda: references,
    )
    worldline = {
        "activityId": 8042901,
        "name": "灵宠竞武",
        "serverCount": 8,
        "startTime": 1786482005000,
        "endTime": 1786629600000,
    }
    activity_id = "lingchong-jingwu-8-2026-08-12-2026-08-13"
    rank_evidence = {
        "rank_scope_completeness": {
            "personal": {"declared": 47, "loaded": 47},
            "plane": {"declared": 8, "loaded": 8},
        }
    }
    with _session() as session:
        session.add(
            FanxiuExchangeActivity(
                id=activity_id,
                instance_key=activity_id,
                activity_type="lingchong-jingwu",
                cross_count=8,
                start_date="2026-08-12",
                end_date="2026-08-13",
                captured_at="2026-08-13T00:27:11+08:00",
                source_kind="standard_runtime_facts",
                evidence=rank_evidence,
            )
        )
        session.add(
            FanxiuPacketBusinessRecord(
                domain="worldline_activity",
                record_key="worldline:8042901",
                entity_id="8042901",
                captured_at="2026-08-12T23:28:48+08:00",
                payload={"item": worldline},
                evidence={"packet": "worldline"},
            )
        )
        session.commit()

        selected_id = lingchong_jingwu.ensure_lingchong_jingwu_activity(session)
        activity = session.get(FanxiuExchangeActivity, selected_id)

    assert selected_id == activity_id
    assert activity is not None
    assert activity.captured_at == "2026-08-13T00:27:11+08:00"
    assert activity.evidence["rank_scope_completeness"] == rank_evidence["rank_scope_completeness"]
    assert activity.evidence["worldline_fact"] == {"packet": "worldline"}


def test_rank_fact_must_belong_to_selected_activity_period() -> None:
    activity = FanxiuExchangeActivity(
        instance_key="lingchong-period-test",
        activity_type="lingchong-jingwu",
        cross_count=8,
        start_date="2026-08-12",
        end_date="2026-08-13",
    )

    lingchong_jingwu._require_fact_in_activity_period(
        activity,
        {"captured_at": "2026-08-13T00:27:11+08:00"},
        label="个人榜",
    )
    with pytest.raises(ValueError, match="不属于所选活动周期"):
        lingchong_jingwu._require_fact_in_activity_period(
            activity,
            {"captured_at": "2026-08-20T00:27:11+08:00"},
            label="个人榜",
        )
