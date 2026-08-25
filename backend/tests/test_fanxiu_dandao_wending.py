from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from backend.core.fanxiu.activity import dandao_wending
from backend.core.fanxiu.activity.dandao_wending import (
    collect_and_store_dandao_wending_activity,
    ensure_dandao_wending_activity,
    load_dandao_wending_tasks,
    load_dandao_observed_task_milestones,
    resolve_dandao_live_task_ids,
    resolve_dandao_static_plan,
)
from backend.core.fanxiu.activity.exchange_activity_registry import (
    get_exchange_activity_spec,
    materialize_registered_exchange_activity,
)
from backend.core.fanxiu.activity.exchange_event import (
    list_exchange_rankings,
    upsert_exchange_activity_snapshot,
)
from backend.models import FanxiuExchangeActivity, FanxiuExchangeRanking


def _write_table(root: Path, table: str, rows: list[dict]) -> None:
    path = root / "parsed_configs" / table / "rows.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def _activity_rows(*, reversed_follow: bool = False) -> list[dict]:
    follow = [43104, 43103] if reversed_follow else [43103, 43104]
    return [
        {
            "id": 1043111,
            "sameActGroup": 12,
            "baseId": 43110,
            "subType": 31,
            "rewardGroup": 1043101,
            "jump": "OpenWin|ActivityRankMainView",
        },
        {
            "id": 4043101,
            "sameActGroup": 12,
            "baseId": 43100,
            "crossGroup": 4,
            "follow": follow,
            "jump": "OpenWin|ActivityRankServerMainView",
        },
        {
            "id": 43103,
            "sameActGroup": 12,
            "crossGroup": 4,
            "subType": 10311,
            "rewardGroup": 43103,
        },
        {
            "id": 43104,
            "sameActGroup": 12,
            "crossGroup": 4,
            "subType": 10312,
            "rewardGroup": 43104,
        },
    ]


def _write_static_tables(root: Path, *, reversed_follow: bool = False) -> None:
    _write_table(root, "Activity", _activity_rows(reversed_follow=reversed_follow))
    _write_table(
        root,
        "ActivityList",
        [
            {"id": 31, "subtype": 1},
            {"id": 10311, "subtype": 1},
            {"id": 10312, "subtype": 4},
        ],
    )


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _current_schedule() -> dict:
    return {
        "source_kind": "worldline_activity_runtime_memory",
        "captured_at": "2026-08-19T06:49:29+08:00",
        "source_evidence": {"pid": 2626},
        "occurrences": [
            {
                "activity_id": 1043111,
                "name": "丹道问鼎",
                "identity_complete": True,
                "start_at": "2026-08-19T05:00:05+08:00",
                "start_date": "2026-08-19",
                "end_date": "2026-08-19",
                "state": 2,
            }
        ],
    }


def test_resolve_preliminary_uses_direct_rank_activity_id(tmp_path: Path) -> None:
    _write_static_tables(tmp_path)

    plan = resolve_dandao_static_plan(1043111, export_root=tmp_path)

    assert plan.phase == "preliminary"
    assert plan.page_view == "ActivityRankMainView"
    assert plan.task_activity_id == 1043111
    assert plan.metric == "MedicalExp"
    assert [(row.scope, row.rank_activity_id, row.reward_group) for row in plan.rank_requests] == [
        ("personal", 1043111, 1043101)
    ]


def test_resolve_cross_classifies_follow_by_subtype_not_position(tmp_path: Path) -> None:
    _write_static_tables(tmp_path, reversed_follow=True)

    plan = resolve_dandao_static_plan(4043101, export_root=tmp_path)

    assert plan.phase == "cross"
    assert plan.page_view == "ActivityRankServerMainView"
    assert plan.task_activity_id == 4043101
    assert [row.scope for row in plan.rank_requests] == ["personal", "plane"]
    assert [row.rank_activity_id for row in plan.rank_requests] == [43103, 43104]
    assert [row.subject for row in plan.rank_requests] == ["role", "server"]


def test_resolve_cross_rejects_missing_comparative_board(tmp_path: Path) -> None:
    rows = _activity_rows()
    rows[1]["follow"] = [43103]
    _write_table(tmp_path, "Activity", rows)
    _write_table(
        tmp_path,
        "ActivityList",
        [{"id": 31, "subtype": 1}, {"id": 10311, "subtype": 1}],
    )

    with pytest.raises(ValueError, match="必须同时声明个人榜和位面榜"):
        resolve_dandao_static_plan(4043101, export_root=tmp_path)


def test_tasks_join_only_runtime_declared_ids_from_multiple_static_ladders(
    tmp_path: Path,
) -> None:
    _write_table(
        tmp_path,
        "ActiveTask",
        [
            {
                "id": 104311101,
                "activityId": 1043111,
                "sort": 1,
                "name_plain": "低档一",
                "finishCondition": ["MedicalExp|1000"],
                "reward": ["Item|1_1"],
            },
            {
                "id": 104311151,
                "activityId": 1043111,
                "sort": 1,
                "name_plain": "本期一",
                "finishCondition": ["MedicalExp|1000"],
                "reward": ["Item|2_1"],
            },
            {
                "id": 104311152,
                "activityId": 1043111,
                "sort": 2,
                "name_plain": "本期二",
                "finishCondition": ["MedicalExp|2000"],
                "reward": ["Item|2_2"],
            },
        ],
    )
    observed = [
        {
            "taskId": 104311152,
            "status": 1,
            "progressList": [{"progress": 1800, "target": 2000, "finish": False}],
        },
        {
            "taskId": 104311151,
            "status": 2,
            "progressList": [{"progress": 1000, "target": 1000, "finish": True}],
        },
    ]

    milestones = load_dandao_observed_task_milestones(
        observed,
        task_activity_id=1043111,
        export_root=tmp_path,
    )

    assert [row.task_id for row in milestones] == [104311151, 104311152]
    assert [row.progress for row in milestones] == [1000, 1800]
    assert [row.finished for row in milestones] == [True, False]
    assert milestones[0].rewards == ("Item|2_1",)


def test_tasks_fail_closed_without_runtime_ids_or_on_target_mismatch(
    tmp_path: Path,
) -> None:
    _write_table(
        tmp_path,
        "ActiveTask",
        [
            {
                "id": 104311151,
                "activityId": 1043111,
                "sort": 1,
                "finishCondition": ["MedicalExp|1000"],
                "reward": [],
            }
        ],
    )

    with pytest.raises(ValueError, match="拒绝从多套静态梯度猜测"):
        load_dandao_observed_task_milestones(
            [], task_activity_id=1043111, export_root=tmp_path
        )
    with pytest.raises(ValueError, match="Runtime/配置目标不一致"):
        load_dandao_observed_task_milestones(
            [
                {
                    "taskId": 104311151,
                    "progressList": [{"progress": 1, "target": 999}],
                }
            ],
            task_activity_id=1043111,
            export_root=tmp_path,
        )


def test_registry_get_materializes_current_dandao_occurrence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_static_tables(tmp_path)
    monkeypatch.setattr(dandao_wending, "resolve_fanxiu_export_root", lambda _=None: tmp_path)
    monkeypatch.setattr(
        dandao_wending,
        "load_worldline_activity_schedule_snapshot",
        _current_schedule,
    )

    spec = get_exchange_activity_spec("dandao-wending")
    assert spec.label == "丹道问鼎"
    assert spec.page.ranking_scopes == ("personal", "plane")
    with _session() as session:
        activity_id = materialize_registered_exchange_activity(
            session, activity_type="dandao-wending"
        )
        assert activity_id == "dandao-wending-1-2026-08-19-2026-08-19"
        assert ensure_dandao_wending_activity(session) == activity_id


def test_collect_uses_current_runtime_rank_and_exposes_fourteen_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_static_tables(tmp_path)
    _write_table(
        tmp_path,
        "ActiveTask",
        [
            {
                "id": 104311150 + index,
                "activityId": 1043111,
                "sort": index,
                "name_plain": f"丹道任务{index}",
                "finishCondition": [f"MedicalExp|{index * 1000}"],
                "corner_plain": "必拿" if index <= 3 else "",
                "reward": [f"Item|1_{index}"],
            }
            for index in range(1, 15)
        ],
    )
    monkeypatch.setattr(dandao_wending, "resolve_fanxiu_export_root", lambda _=None: tmp_path)
    monkeypatch.setattr(
        dandao_wending,
        "load_worldline_activity_schedule_snapshot",
        _current_schedule,
    )
    monkeypatch.setattr(
        dandao_wending,
        "read_activity_rank_runtime_snapshot",
        lambda _activity_id: {
            "ok": True,
            "complete": True,
            "captured_at": "2026-08-19T06:55:00+08:00",
            "rank_list_size": 2,
            "loaded_rank_count": 2,
            "declared_rank_count": 2,
            "self_ranking": {
                "rank": 0,
                "score": 0,
                "role_key": "self",
                "name": "本人",
            },
            "rankings": [
                {"rank": 1, "score": 447832, "role_key": "one", "name": "无尘"},
                {"rank": 2, "score": 123456, "role_key": "two", "name": "第二名"},
            ],
            "evidence": {"process_start_ticks": 4748},
        },
    )

    with _session() as session:
        activity_id = ensure_dandao_wending_activity(session)
        collect_and_store_dandao_wending_activity(
            session,
            activity_id=activity_id,
            today=date(2026, 8, 19),
        )
        assert ensure_dandao_wending_activity(session) == activity_id
        persisted = session.get(FanxiuExchangeActivity, activity_id)
        assert persisted is not None
        assert persisted.captured_at == "2026-08-19T06:55:00+08:00"
        assert persisted.source_kind == "read_only_runtime_memory"
        page = list_exchange_rankings(
            session,
            activity_type="dandao-wending",
            activity_id=activity_id,
            ranking_scope="personal",
            page=1,
            page_size=100,
        )
        tasks = load_dandao_wending_tasks(session, activity_id=activity_id)

    assert [row.name for row in page.entries] == ["无尘", "第二名"]
    assert page.self_entry is None
    assert page.last_captured_at.startswith("2026-08-19")
    assert len(tasks["items"]) == 14
    assert tasks["complete"] is False


def test_collect_cross_occurrence_uses_both_follow_rank_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_static_tables(tmp_path)
    monkeypatch.setattr(dandao_wending, "resolve_fanxiu_export_root", lambda _=None: tmp_path)
    requested: list[int] = []

    def read_snapshot(activity_id: int) -> dict:
        requested.append(activity_id)
        return {
            "ok": True,
            "complete": True,
            "captured_at": "2026-08-21T20:00:00+08:00",
            "rank_list_size": 1,
            "loaded_rank_count": 1,
            "self_ranking": {"rank": 1, "score": activity_id, "role_key": f"self-{activity_id}"},
            "rankings": [
                {"rank": 1, "score": activity_id, "role_key": f"row-{activity_id}", "name": str(activity_id)}
            ],
            "evidence": {"rank_activity_id": activity_id},
        }

    monkeypatch.setattr(dandao_wending, "read_activity_rank_runtime_snapshot", read_snapshot)
    with _session() as session:
        activity_id = upsert_exchange_activity_snapshot(
            session,
            {
                "activity_type": "dandao-wending",
                "cross_count": 4,
                "start_date": "2026-08-20",
                "end_date": "2026-08-21",
                "game_rank_activity_id": 43103,
                "currency_name": "炼丹熟练度",
                "captured_at": "2026-08-21T00:30:00+08:00",
                "source_kind": "runtime_schedule_reconcile",
                "evidence": {"game_activity_id": 4043101},
            },
        )
        collect_and_store_dandao_wending_activity(
            session,
            activity_id=activity_id,
            today=date(2026, 8, 21),
        )
        rankings = session.exec(
            select(FanxiuExchangeRanking).where(
                FanxiuExchangeRanking.activity_id == activity_id
            )
        ).all()

    assert requested == [43103, 43104]
    assert {(row.ranking_scope, row.name) for row in rankings} == {
        ("personal", "43103"),
        ("plane", "43104"),
    }


def test_live_task_ids_follow_questmgr_membership_not_static_variant(tmp_path: Path) -> None:
    rows = [
        {
            "id": task_id,
            "activityId": 4043101,
            "sort": order,
            "finishCondition": [f"MedicalExp|{order * 1000}"],
        }
        for order, task_id in enumerate((165, 166, 154, 155), start=1)
    ]
    rows.extend(
        {
            "id": task_id,
            "activityId": 4043101,
            "sort": order,
            "finishCondition": [f"MedicalExp|{order * 1000}"],
        }
        for order, task_id in enumerate((151, 152), start=1)
    )
    _write_table(tmp_path, "ActiveTask", rows)

    assert resolve_dandao_live_task_ids(
        4043101,
        task_entries=[{"taskId": 154}, {"taskId": 155}],
        finished_task_ids=[165, 166],
        export_root=tmp_path,
    ) == (165, 166, 154, 155)


def test_live_task_ids_reject_simultaneous_retained_variants(tmp_path: Path) -> None:
    _write_table(
        tmp_path,
        "ActiveTask",
        [
            {
                "id": task_id,
                "activityId": 4043101,
                "sort": 1,
                "finishCondition": ["MedicalExp|1000"],
            }
            for task_id in (151, 165)
        ],
    )

    with pytest.raises(ValueError, match="互斥梯度"):
        resolve_dandao_live_task_ids(
            4043101,
            task_entries=[{"taskId": 151}, {"taskId": 165}],
            finished_task_ids=[],
            export_root=tmp_path,
        )
