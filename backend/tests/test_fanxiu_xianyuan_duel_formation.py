from __future__ import annotations

from backend.core.fanxiu.data_annotation.duel_strategy import best_xianyuan_partner_order, plan_swaps
from backend.core.fanxiu.data_annotation.runner import create_behavior_tree_runtime_runner


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


def _team(partner_ids: list[int], power: int) -> dict:
    return {
        "type": 0,
        "power": power,
        "partner_ids": partner_ids,
        "members": [{"slot": index, "partner_id": value} for index, value in enumerate(partner_ids, 1)],
        "formation_complete": True,
    }


def test_formation_skips_all_visual_work_at_two_times_power(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    logs: list[str] = []
    monkeypatch.setattr(runner, "_log", lambda _kind, message: logs.append(message))
    monkeypatch.setitem(
        runner._optimize_daily_xianyuan_duel_formation.__func__.__globals__,
        "read_xianyuan_duel_runtime_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("2x power shortcut must not load detailed formations")
        ),
    )

    class NoVisualRuntime:
        def __getattr__(self, name):
            raise AssertionError(f"2x power shortcut must not call runtime.{name}")

    payload = {
        "__xianyuan_duel_facts": {"self_power": 200, "self_team": _team([16, 23, 9, 2, 1], 200)},
        "__xianyuan_duel_target": {
            "name": "目标",
            "team_power": 100,
            "team": _team([28, 2, 1, 30, 9], 100),
        },
    }

    _drain(runner._optimize_daily_xianyuan_duel_formation(NoVisualRuntime(), payload))

    assert any("达到 2 倍" in message and "跳过阵容调整" in message for message in logs)


def test_formation_hydrates_details_only_below_power_shortcut(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    monkeypatch.setattr(runner, "_log", lambda *_args: None)
    my_ids = [16, 23, 9, 2, 1]
    enemy_ids = [28, 2, 1, 30, 9]
    summary_target = {
        "target_id": 42,
        "name": "目标",
        "team_power": 100,
        "team": {"formation_complete": False},
    }
    detailed_target = {
        **summary_target,
        "team": _team(enemy_ids, 100),
    }
    reads: list[bool] = []
    monkeypatch.setitem(
        runner._optimize_daily_xianyuan_duel_formation.__func__.__globals__,
        "read_xianyuan_duel_runtime_snapshot",
        lambda *, include_formations: (
            reads.append(include_formations)
            or {
                "available": True,
                "complete": True,
                "self_power": 150,
                "self_team": _team(my_ids, 150),
                "targets": [detailed_target],
            }
        ),
    )
    dragged: list[tuple[str, str]] = []

    class StructuredRuntime:
        def current_scene(self, _preferred, *, update=False):
            return 309, 100.0, "frame"

        def drag_shape_to_shape(self, _scene, start, end, **_kwargs):
            dragged.append((start, end))

        def wait_action_settle(self, _seconds):
            if False:
                yield None

    _drain(
        runner._optimize_daily_xianyuan_duel_formation(
            StructuredRuntime(),
            {
                "formation_drag_settle_seconds": 0,
                "__xianyuan_duel_facts": {
                    "self_power": 150,
                    "self_team": {"formation_complete": False},
                },
                "__xianyuan_duel_target": summary_target,
            },
        )
    )

    assert reads == [True]
    assert dragged


def test_formation_drags_exact_slots_from_structured_partner_ids(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    monkeypatch.setattr(runner, "_log", lambda *_args: None)
    my_ids = [16, 23, 9, 2, 1]
    enemy_ids = [28, 2, 1, 30, 9]
    expected = plan_swaps(my_ids, best_xianyuan_partner_order(my_ids, enemy_ids)["partner_ids"])
    dragged: list[tuple[str, str]] = []

    class StructuredRuntime:
        def current_scene(self, _preferred, *, update=False):
            return 309, 100.0, "frame"

        def drag_shape_to_shape(self, _scene, start, end, **_kwargs):
            dragged.append((start, end))

        def wait_action_settle(self, _seconds):
            if False:
                yield None

    payload = {
        "formation_drag_settle_seconds": 0,
        "__xianyuan_duel_facts": {"self_power": 150, "self_team": _team(my_ids, 150)},
        "__xianyuan_duel_target": {"name": "目标", "team_power": 100, "team": _team(enemy_ids, 100)},
    }

    _drain(runner._optimize_daily_xianyuan_duel_formation(StructuredRuntime(), payload))

    assert dragged == [(f"拖拽锚点{start}", f"拖拽锚点{end}") for start, end in expected]
