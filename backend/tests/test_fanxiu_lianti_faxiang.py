import json
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity import lianti_faxiang
from backend.core.fanxiu.activity.exchange_activity_registry import (
    collect_registered_resource_ranking_resources,
    get_exchange_activity_spec,
    load_registered_resource_ranking_resources,
    load_registered_resource_ranking_tasks,
)
from backend.models import FanxiuExchangeActivity, FanxiuPacketDecodedRecord


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _write_rows(root: Path, table: str, rows: list[dict]) -> None:
    path = root / "parsed_configs" / table / "rows.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def _active_tasks() -> list[dict]:
    return [
        {
            "id": 104301151,
            "activityId": 1043011,
            "sort": 1,
            "name_plain": "修炼体魄一",
            "finishCondition": ["PhysicalFightScore|5000"],
            "corner_plain": "必拿",
            "reward": ["Item|3020003_1_7"],
        },
        {
            "id": 104301152,
            "activityId": 1043011,
            "sort": 2,
            "name_plain": "修炼体魄二",
            "finishCondition": ["PhysicalFightScore|10000"],
            "corner_plain": "必拿",
            "reward": ["Item|3020003_1_7"],
        },
        # Retained old ladder: must never be selected without QuestEntryVO membership.
        {
            "id": 104301101,
            "activityId": 1043011,
            "sort": 1,
            "name_plain": "旧梯度",
            "finishCondition": ["PhysicalFightScore|5000"],
            "reward": [],
        },
    ]


def test_registry_declares_same_server_lianti_resource_ranking() -> None:
    spec = get_exchange_activity_spec("lianti-faxiang")

    assert spec.label == "炼体法相"
    assert spec.page.page_kind == "resource-ranking"
    assert spec.page.ranking_scopes == ("personal",)
    assert spec.shop is None
    assert spec.rank_scopes[0].activity_id.fixed_id == 1043011


def test_task_projection_requires_exact_quest_entry_ids(tmp_path: Path) -> None:
    _write_rows(tmp_path, "ActiveTask", _active_tasks())
    observed = [
        {
            "taskId": 104301152,
            "status": 3,
            "progressList": {"items": [{"progress": 7000, "target": 10000}]},
        },
        {
            "taskId": 104301151,
            "status": 4,
            "progressList": {
                "items": [{"progress": 5000, "target": 5000, "finish": True}]
            },
        },
    ]

    result = lianti_faxiang._task_milestones(observed, export_root=tmp_path)

    assert [row["task_id"] for row in result] == [104301151, 104301152]
    assert result[0]["must_get"] is True
    assert result[0]["finished"] is True
    assert 104301101 not in [row["task_id"] for row in result]


def test_task_projection_fails_closed_without_quest_membership(tmp_path: Path) -> None:
    _write_rows(tmp_path, "ActiveTask", _active_tasks())

    with pytest.raises(ValueError, match="拒绝从多套静态梯度猜测"):
        lianti_faxiang._task_milestones([], export_root=tmp_path)


def test_task_projection_rejects_runtime_config_target_mismatch(tmp_path: Path) -> None:
    _write_rows(tmp_path, "ActiveTask", _active_tasks())

    with pytest.raises(ValueError, match="Runtime/配置目标不一致"):
        lianti_faxiang._task_milestones(
            [
                {
                    "taskId": 104301151,
                    "progressList": {"items": [{"progress": 0, "target": 10000}]},
                }
            ],
            export_root=tmp_path,
        )


def test_generic_task_api_loader_uses_current_quest_entry_membership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_rows(tmp_path, "ActiveTask", _active_tasks())
    monkeypatch.setattr(
        lianti_faxiang,
        "resolve_fanxiu_export_root",
        lambda _root=None: tmp_path,
    )
    with _session() as session:
        activity_id = "lianti-faxiang-1-2026-08-14-2026-08-14"
        session.add(
            FanxiuExchangeActivity(
                id=activity_id,
                instance_key=activity_id,
                activity_type="lianti-faxiang",
                cross_count=1,
                start_date="2026-08-14",
                end_date="2026-08-14",
            )
        )
        session.add(
            FanxiuPacketDecodedRecord(
                packet_id="quest-create-lianti",
                name="SM_CreateQuestInfo",
                captured_at="2026-08-14T10:01:00+08:00",
                frame_index=1,
                payload={
                    "parsed": {
                        "entryVOs": {
                            "_count": 2,
                            "items": [
                                {
                                    "taskId": 104301151,
                                    "status": 3,
                                    "progressList": {
                                        "items": [{"progress": 0, "target": 5000}]
                                    },
                                },
                                {
                                    "taskId": 104301152,
                                    "status": 3,
                                    "progressList": {
                                        "items": [{"progress": 0, "target": 10000}]
                                    },
                                },
                            ],
                        }
                    }
                },
            )
        )
        session.commit()

        with pytest.raises(ValueError, match="Runtime.*禁止使用抓包 raw JSON"):
            load_registered_resource_ranking_tasks(
                session,
                activity_type="lianti-faxiang",
                activity_id=activity_id,
            )


def test_generic_resource_snapshot_counts_score_and_breakthrough_separately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_rows(
        tmp_path,
        "Item",
        [
            {"id": 5030001, "name_plain": "淬体精魄", "quality": 4},
            {"id": 5030002, "name_plain": "龙髓精魄", "quality": 5},
        ],
    )
    monkeypatch.setattr(
        lianti_faxiang,
        "read_backpack_item_counts",
        lambda *_args, **_kwargs: (
            {5030001: 321, 5030002: 7},
            {"manager": "BackpackMgr"},
        ),
    )
    snapshot = lianti_faxiang.collect_lianti_faxiang_resource_snapshot(
        activity_id="lianti-current",
        export_root=tmp_path,
    )

    assert snapshot.primary_resource_count == 321
    assert snapshot.breakthrough_resource_count == 7
    assert snapshot.maximum_score_gain == 32100
    assert snapshot.evidence["breakthrough_resource_not_counted_as_score"] is True


def test_generic_resource_registry_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.exchange_event.is_exchange_activity_active",
        lambda _activity: True,
    )
    _write_rows(
        tmp_path,
        "Item",
        [
            {"id": 5030001, "name_plain": "淬体精魄", "quality": 4},
            {"id": 5030002, "name_plain": "龙髓精魄", "quality": 5},
        ],
    )
    monkeypatch.setattr(lianti_faxiang, "resolve_fanxiu_export_root", lambda _=None: tmp_path)
    monkeypatch.setattr(
        lianti_faxiang,
        "read_backpack_item_counts",
        lambda *_args, **_kwargs: ({5030001: 200, 5030002: 3}, {"read_only": True}),
    )
    with _session() as session:
        activity_id = "lianti-faxiang-1-2026-08-14-2026-08-14"
        session.add(
            FanxiuExchangeActivity(
                id=activity_id,
                instance_key=activity_id,
                activity_type="lianti-faxiang",
                cross_count=1,
                start_date="2026-08-14",
                end_date="2026-08-14",
            )
        )
        session.commit()
        collected = collect_registered_resource_ranking_resources(
            session, activity_type="lianti-faxiang", activity_id=activity_id
        )
        loaded = load_registered_resource_ranking_resources(
            session, activity_type="lianti-faxiang", activity_id=activity_id
        )

    assert collected.primary_resource_count == 200
    assert loaded == collected
