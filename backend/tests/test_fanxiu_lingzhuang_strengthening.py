from datetime import date

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.exchange_event import upsert_exchange_activity_snapshot
from backend.core.fanxiu.activity import lingzhuang_strengthening


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
            "captured_at": "",
            "source_kind": "activity_instance",
        },
    )


def _runtime_snapshot(*, cross_count: int = 16) -> dict:
    rows = []
    for part_index, (part, initial_id, initial_name, dongxuan_id, dongxuan_name) in enumerate(
        lingzhuang_strengthening._PARTS,
        1,
    ):
        rows.append(
            {
                "part": part,
                "initial": {
                    "material_id": initial_id,
                    "material_name": initial_name,
                    "material_count": part_index * 100,
                    "equipment_level": 170 + part_index,
                    "equipment_raw_level": (170 + part_index) * 9,
                    "equipped": True,
                },
                "dongxuan": {
                    "material_id": dongxuan_id,
                    "material_name": dongxuan_name,
                    "material_count": part_index * 100,
                    "equipment_level": part_index,
                    "equipment_raw_level": part_index * 9,
                    "equipped": part_index <= 4,
                },
            }
        )
    return {
        "game_task_activity_id": cross_count * 1_000_000 + 44_301,
        "captured_at": "2026-08-03T19:30:00+08:00",
        "materials_captured_at": "2026-08-03T19:30:00+08:00",
        "equipment_captured_at": "2026-08-03T19:30:00+08:00",
        "task_progress_captured_at": "2026-08-03T19:30:00+08:00",
        "source_kind": "read_only_runtime_memory",
        "complete": True,
        "rows": rows,
        "equipment_tasks": [
            {
                "task_id": 1604430150 + index,
                "order": index,
                "name": f"装备强化{index}",
                "progress": min(300, target),
                "target": target,
                "finished": target <= 200,
            }
            for index, target in enumerate(
                (100, 200, 400, 600, 800, 1000, 1400, 2000, 3000, 4000, 6000, 8000, 10000, 12000),
                1,
            )
        ],
        "score_round": 1,
        "score_total_rounds": 4,
        "score_tasks": [
            {
                "task_id": 614430100 + index,
                "order": index,
                "name": f"装备强化{index}",
                "progress": 75_000,
                "target": target,
                "finished": False,
            }
            for index, target in enumerate(
                (517_500, 1_150_000, 1_897_500, 2_760_000, 3_737_500, 4_830_000, 6_152_500, 7_705_000, 9_487_500, 11_500_000),
                1,
            )
        ],
        "evidence": {"pid": 123},
    }


def test_empty_strengthening_snapshot_keeps_static_material_reference() -> None:
    with _session() as session:
        snapshot = lingzhuang_strengthening.load_lingzhuang_strengthening_snapshot(session)

    assert snapshot.complete is False
    assert len(snapshot.rows) == 10
    assert snapshot.rows[1].initial.material_name == "气铠玄铁石"
    assert snapshot.rows[1].dongxuan.material_name == "气铠玄铁石"
    assert snapshot.rows[1].initial.material_id == snapshot.rows[1].dongxuan.material_id
    assert snapshot.rows[1].initial.material_count is None


def test_completed_equipment_task_prefix_is_reconstructed_from_live_suffix() -> None:
    suffix = [
        {
            "task_id": 1604430150 + order,
            "progress": 4515,
            "target": target,
            "finished": False,
        }
        for order, target in enumerate(lingzhuang_strengthening._EQUIPMENT_TASK_TARGETS, 1)
        if target >= 6000
    ]

    rows = lingzhuang_strengthening._reconstruct_equipment_task_rows(suffix)

    assert len(rows) == 14
    assert rows[9]["target"] == 4000
    assert rows[9]["progress"] == 4515
    assert rows[9]["finished"] is True
    assert rows[10]["target"] == 6000
    assert rows[10]["finished"] is False


def test_equipment_only_preliminary_task_group_is_complete() -> None:
    tasks_complete, equipment_only_phase = (
        lingzhuang_strengthening._task_progress_complete(
            raw_task_total=17,
            equipment_task_count=14,
            score_task_count=0,
        )
    )

    assert tasks_complete is True
    assert equipment_only_phase is True


def test_equipment_only_group_ignores_unrelated_quest_rows() -> None:
    tasks_complete, equipment_only_phase = (
        lingzhuang_strengthening._task_progress_complete(
            raw_task_total=57,
            equipment_task_count=14,
            score_task_count=0,
        )
    )

    assert tasks_complete is True
    assert equipment_only_phase is True


def test_collect_strengthening_snapshot_persists_complete_runtime_fact(monkeypatch) -> None:
    monkeypatch.setattr(
        lingzhuang_strengthening,
        "read_lingzhuang_strengthening_runtime_snapshot",
        _runtime_snapshot,
    )
    with _session() as session:
        activity_id = _activity(session)
        collected = lingzhuang_strengthening.collect_and_store_lingzhuang_strengthening_snapshot(
            session,
            activity_id=activity_id,
            today=date(2026, 8, 3),
        )
        loaded = lingzhuang_strengthening.load_lingzhuang_strengthening_snapshot(session)

    assert collected.captured_at == "2026-08-03T19:30:00+08:00"
    assert loaded.complete is True
    assert loaded.rows[1].initial.material_count == 200
    assert loaded.rows[1].initial.equipment_level == 172
    assert loaded.rows[4].dongxuan.equipped is False
    assert loaded.activity_id == activity_id
    assert loaded.game_task_activity_id == 16044301
    assert loaded.equipment_tasks[2].progress == 300
    assert loaded.equipment_tasks[2].target == 400
    assert [
        (row.target, row.talent_pill_count)
        for row in loaded.equipment_tasks
        if row.talent_pill_count > 0
    ] == [(1_000, 1), (2_000, 1), (4_000, 2), (8_000, 2), (12_000, 4)]
    assert loaded.equipment_current == 300
    assert loaded.score_round == 1
    assert loaded.score_current == 75_000
    assert [(row.round, row.target) for row in loaded.score_rounds] == [
        (1, 11_500_000),
        (2, 11_870_500),
        (3, 12_240_000),
        (4, 12_614_900),
    ]
    assert loaded.score_tasks[0].progress == 75_000
    assert loaded.score_tasks[0].target == 517_500


def test_collect_strengthening_snapshot_rejects_inactive_activity(monkeypatch) -> None:
    monkeypatch.setattr(
        lingzhuang_strengthening,
        "read_lingzhuang_strengthening_runtime_snapshot",
        _runtime_snapshot,
    )
    with _session() as session:
        activity_id = _activity(session)
        try:
            lingzhuang_strengthening.collect_and_store_lingzhuang_strengthening_snapshot(
                session,
                activity_id=activity_id,
                today=date(2026, 8, 5),
            )
        except ValueError as exc:
            assert "不在有效日期内" in str(exc)
        else:
            raise AssertionError("inactive activity refresh must fail")


def test_partial_refresh_preserves_failed_categories_and_complete_timestamp(monkeypatch) -> None:
    snapshots = [_runtime_snapshot()]
    partial = _runtime_snapshot()
    partial["captured_at"] = "2026-08-03T20:00:00+08:00"
    partial["materials_captured_at"] = "2026-08-03T20:00:00+08:00"
    partial["equipment_captured_at"] = ""
    partial["task_progress_captured_at"] = ""
    partial["complete"] = False
    partial["equipment_tasks"] = []
    partial["score_round"] = None
    partial["score_tasks"] = []
    partial["rows"][0]["initial"]["material_count"] = 999
    snapshots.append(partial)
    monkeypatch.setattr(
        lingzhuang_strengthening,
        "read_lingzhuang_strengthening_runtime_snapshot",
        lambda **_: snapshots.pop(0),
    )

    with _session() as session:
        activity_id = _activity(session)
        first = lingzhuang_strengthening.collect_and_store_lingzhuang_strengthening_snapshot(
            session,
            activity_id=activity_id,
            today=date(2026, 8, 3),
        )
        second = lingzhuang_strengthening.collect_and_store_lingzhuang_strengthening_snapshot(
            session,
            activity_id=activity_id,
            today=date(2026, 8, 3),
        )

    assert second.complete is False
    assert second.captured_at == first.captured_at
    assert second.materials_captured_at == "2026-08-03T20:00:00+08:00"
    assert second.rows[0].initial.material_count == 999
    assert second.equipment_captured_at == first.equipment_captured_at
    assert second.rows[0].initial.equipment_level == first.rows[0].initial.equipment_level
    assert second.task_progress_captured_at == first.task_progress_captured_at
    assert second.equipment_tasks == first.equipment_tasks
    assert second.score_tasks == first.score_tasks
