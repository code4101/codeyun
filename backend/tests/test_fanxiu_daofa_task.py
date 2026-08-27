from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from datetime import datetime

from backend.core.fanxiu.data_annotation.arena_schedule import (
    DAOFA_TASK_ID,
    daofa_scheduler_in_window,
    next_daofa_trigger_at,
)
from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.tasks.daofa import (
    DaofaTaskMixin,
    daofa_force_finish_at,
    daofa_runtime_facts_advanced,
    daofa_no_target_retry_at,
    daofa_settlement_at,
    resolve_daofa_remaining_times,
    select_daofa_target,
    should_force_finish_daofa,
)


class _StopEvent:
    def is_set(self) -> bool:
        return False


class _View:
    def __init__(self, scene_id: int) -> None:
        self.id = scene_id


class _Runtime:
    def __init__(self, scene_id: int, landings: list[int]) -> None:
        self.scene_id = scene_id
        self.landings = list(landings)
        self.actions: list[tuple[object, ...]] = []
        self.attrs: dict[str, object] = {}

    @contextmanager
    def expect_views(self, *scene_ids: int):
        self.attrs["business_view_claims"] = [tuple(scene_ids)]
        try:
            yield self
        finally:
            self.attrs.pop("business_view_claims", None)

    def current_scene(self, _scene_ids, *, update: bool = False):
        assert update is True
        return self.scene_id, 100.0, "frame"

    def click_frame_point(self, scene_id: int, x: float, y: float) -> None:
        self.actions.append(("click_point", scene_id, x, y))

    def wait_scene(self, *scene_ids: int, timeout: float, label: str):
        self.actions.append(("wait_scene", scene_ids, timeout, label))
        landed = self.landings.pop(0)
        assert landed in scene_ids
        self.scene_id = landed
        if False:
            yield None
        return _View(landed)

    def click_shape_center(self, scene_id: int, shape: str) -> None:
        self.actions.append(("click_shape", scene_id, shape))

    def ocr_text(self, *, update: bool = False) -> str:
        assert update is True
        return "挑战成功，排名上升"


class _Runner(DaofaTaskMixin):
    def __init__(self, runtime: _Runtime) -> None:
        self.runtime = runtime

    def _fanxiu_runtime(self, *_args, **_kwargs):
        return self.runtime

    def _frame_size(self, image):
        return float(image["width"]), float(image["height"])


class _ManagedRunner(_Runner):
    def __init__(self, runtime: _Runtime) -> None:
        super().__init__(runtime)
        self.left = False
        self.logs: list[tuple[str, str]] = []

    def _execute_daily_daofa_task(self, *_args, **_kwargs):
        if False:
            yield None
        raise RuntimeError("challenge failed")

    def _leave_daofa_to_world(self, runtime):
        assert runtime is self.runtime
        self.left = True
        if False:
            yield None

    def _log(self, kind: str, message: str) -> None:
        self.logs.append((kind, message))


def _run(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def _ctx() -> dict[str, object]:
    return {
        "asset_tree_path": Path("asset-tree.json"),
        "images": {376: {"width": 900, "height": 1600}},
    }


def test_daofa_round_handles_optional_confirmation() -> None:
    runtime = _Runtime(376, [377, 378, 376])
    result = _run(
        _Runner(runtime)._run_daofa_challenge_round(
            _ctx(),
            _StopEvent(),
            challenge_point=(231.0, 1024.0),
        )
    )

    assert result == {
        "status": "success",
        "prompt_seen": True,
        "result_text": "挑战成功，排名上升",
        "final_scene": 376,
    }
    assert runtime.actions[:3] == [
        ("click_point", 376, 231.0, 1024.0),
        ("wait_scene", (377, 378), 600.0, "道法争锋：等待挑战确认或挑战结果"),
        ("click_shape", 377, "确认"),
    ]
    assert runtime.actions[-2:] == [
        ("click_shape", 378, "继续"),
        ("wait_scene", (376,), 45.0, "道法争锋：结果页继续并返回挑战页"),
    ]
    assert "business_view_claims" not in runtime.attrs


def test_daofa_round_accepts_direct_result_when_login_prompt_is_suppressed() -> None:
    runtime = _Runtime(376, [378, 376])
    result = _run(
        _Runner(runtime)._run_daofa_challenge_round(
            _ctx(),
            _StopEvent(),
            challenge_point=(231.0, 1024.0),
        )
    )

    assert result["prompt_seen"] is False
    assert ("click_shape", 377, "确认") not in runtime.actions
    assert runtime.actions[1][0:2] == ("wait_scene", (377, 378))


@pytest.mark.parametrize("scene_id", [377, 378])
def test_daofa_round_can_resume_inside_closure(scene_id: int) -> None:
    landings = [378, 376] if scene_id == 377 else [376]
    runtime = _Runtime(scene_id, landings)
    result = _run(_Runner(runtime)._run_daofa_challenge_round(_ctx(), _StopEvent()))

    assert result["final_scene"] == 376
    assert not any(action[0] == "click_point" for action in runtime.actions)


def test_daofa_round_rejects_missing_or_out_of_bounds_target() -> None:
    with pytest.raises(RuntimeError, match="必须提供目标挑战按钮落点"):
        _run(_Runner(_Runtime(376, []))._run_daofa_challenge_round(_ctx(), _StopEvent()))

    with pytest.raises(ValueError, match="挑战落点越界"):
        _run(
            _Runner(_Runtime(376, []))._run_daofa_challenge_round(
                _ctx(),
                _StopEvent(),
                challenge_point=(901.0, 1024.0),
            )
        )


def test_daofa_managed_task_runs_business_cleanup_before_reraising() -> None:
    runner = _ManagedRunner(_Runtime(376, []))

    with pytest.raises(RuntimeError, match="challenge failed"):
        _run(runner._execute_daily_daofa_task_managed(_ctx(), _StopEvent(), {}))

    assert runner.left is True


def test_daofa_completed_attempts_checkpoint_before_best_effort_cleanup() -> None:
    runner = _ManagedRunner(_Runtime(376, []))
    events: list[str] = []

    def set_next(_payload):
        events.append("checkpoint")
        return "2026-08-12 23:00:00"

    def leave(_runtime):
        events.append("leave")
        if False:
            yield None
        raise RuntimeError("#376 缺少返回路径")

    runner._set_daofa_next_trigger = set_next
    runner._leave_daofa_to_world = leave

    result = _run(
        runner._finish_completed_daofa(
            runner.runtime,
            {},
            completed_rounds=5,
            completion_evidence="剩余挑战次数为 0",
        )
    )

    assert events == ["checkpoint", "leave"]
    assert result["result"] == "success"
    assert result["current_scene"] == 376
    assert "道法争锋已完成" in result["message"]
    assert "离场未完成" in result["message"]


def test_daofa_job_wrapper_does_not_repeat_business_owned_cleanup() -> None:
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daily_daofa")
    assert definition is not None

    class Runner:
        @staticmethod
        def _execute_daily_daofa_task_managed(_ctx, _stop_event, _payload):
            if False:
                yield None
            return {"result": "success", "current_scene": 376}

        @staticmethod
        def _fanxiu_runtime(*_args, **_kwargs):
            raise AssertionError("道法 Job 包装器不得重复执行 goto_view(34)")

    result = _run(definition.handler(Runner(), {}, {}, _StopEvent()))

    assert result == {"result": "success", "current_scene": 376}


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 7, 30, 10, 0, 0), True),
        (datetime(2026, 7, 30, 23, 59, 59), True),
        (datetime(2026, 7, 31, 9, 22, 0), False),
        (datetime(2026, 8, 2, 10, 0, 0), True),
        (datetime(2026, 8, 2, 21, 59, 59), True),
        (datetime(2026, 8, 2, 22, 0, 0), False),
        (datetime(2026, 8, 2, 9, 22, 0), False),
    ],
)
def test_daofa_single_job_uses_the_current_day_window(
    now: datetime,
    expected: bool,
) -> None:
    assert daofa_scheduler_in_window(now) is expected


def test_daofa_single_job_dynamically_uses_sunday_trigger() -> None:
    assert next_daofa_trigger_at(datetime(2026, 8, 1, 23, 59, 59)) == datetime(
        2026, 8, 2, 18, 30, 0
    )


def test_daofa_window_and_strategy_trigger_are_independent() -> None:
    monday_open = datetime(2026, 8, 3, 10, 0, 0)
    sunday_closed = datetime(2026, 8, 2, 22, 0, 0)

    assert daofa_scheduler_in_window(monday_open) is True
    assert next_daofa_trigger_at(monday_open) == datetime(2026, 8, 3, 23, 0, 0)
    assert daofa_scheduler_in_window(sunday_closed) is False
    assert next_daofa_trigger_at(sunday_closed) == datetime(2026, 8, 3, 23, 0, 0)


def test_daofa_admission_discards_post_settlement_sunday_cycle(monkeypatch) -> None:
    from backend.core.fanxiu.data_annotation.tasks import daofa as daofa_module

    monkeypatch.setattr(daofa_module, "_now", lambda: datetime(2026, 8, 2, 23, 24, 0))
    writes: list[tuple[str, str]] = []

    class Runner(DaofaTaskMixin):
        def _persist_admission_decision(self, payload, decision):
            normalized = dict(decision)
            next_time = normalized.pop("next_time")
            writes.append((str(payload["__scheduler_task_id"]), next_time))
            return normalized

    decision = Runner().daily_daofa_admission(
        {"__scheduler_task_id": DAOFA_TASK_ID}
    )

    assert decision["result"] == "success"
    assert "next_time" not in decision
    assert writes == [(DAOFA_TASK_ID, "2026-08-03 23:00:00")]
    assert decision["current_scene"] is None
    assert decision["scheduler_incident"] == {
        "kind": "window_expired",
        "cycle_kind": "weekly",
        "window": "周日 10:00-22:00",
        "reason": "本周窗口已结束，禁止跨周补跑",
    }


def test_daofa_visible_ranks_reuses_current_stable_frame() -> None:
    class Runtime:
        def ocr_fragments(self, *, update: bool = False):
            assert update is False
            return [{"text": "第 27 名", "x": 100, "y": 200, "w": 80, "h": 30}]

    ranks = DaofaTaskMixin()._daofa_visible_ranks(
        Runtime(),
        {"x": 0, "y": 0, "w": 900, "h": 1600},
    )
    assert ranks == [(27, 140.0, 215.0)]


def test_daofa_admission_discards_stale_weekday_run_without_game_side_effects(
    monkeypatch,
) -> None:
    from backend.core.fanxiu.data_annotation.tasks import daofa as daofa_module

    monkeypatch.setattr(daofa_module, "_now", lambda: datetime(2026, 7, 31, 9, 22, 0))
    writes: list[tuple[str, str]] = []

    class Runner(DaofaTaskMixin):
        def _persist_admission_decision(self, payload, decision):
            normalized = dict(decision)
            next_time = normalized.pop("next_time")
            writes.append((str(payload["__scheduler_task_id"]), next_time))
            return normalized

    decision = Runner().daily_daofa_admission(
        {"__scheduler_task_id": DAOFA_TASK_ID}
    )

    assert decision == {
        "result": "success",
        "message": "道法争锋：当前不在 周一至周六 10:00-24:00 窗口，当前周期尚未开放，等待当日 10:00，未执行游戏操作",
        "current_scene": None,
        "scheduler_incident": {
            "kind": "window_expired",
            "cycle_kind": "daily",
            "window": "周一至周六 10:00-24:00",
            "reason": "当前周期尚未开放，等待当日 10:00",
        },
    }
    assert writes == [(DAOFA_TASK_ID, "2026-07-31 23:00:00")]


def test_daofa_retry_cannot_escape_the_current_window() -> None:
    writes: list[tuple[str, str]] = []

    class Runner(DaofaTaskMixin):
        def _persist_scheduler_task_next_time(self, task_id: str, next_time: str) -> None:
            writes.append((task_id, next_time))

    result = Runner()._set_daofa_retry(
        {"__scheduler_task_id": DAOFA_TASK_ID},
        seconds=60,
        now=datetime(2026, 8, 1, 23, 59, 30),
    )

    assert result == "2026-08-02 18:30:00"
    assert writes == [(DAOFA_TASK_ID, result)]


def test_select_daofa_target_uses_group_order_and_rank(tmp_path: Path) -> None:
    from backend.core.fanxiu.catalog.server_relations import save_fanxiu_server_relations

    save_fanxiu_server_relations(
        {
            "groups": [
                {
                    "key": "friendly",
                    "children": [
                        {"key": "same_server", "servers": [{"server_id": 22077, "server_order": 53, "server_name": "same"}]},
                        {"key": "alliance", "servers": [{"server_id": 22055, "server_order": 55, "server_name": "alliance"}]},
                        {"key": "ally", "servers": [{"server_id": 22064, "server_order": 64, "server_name": "ally"}]},
                    ],
                }
            ]
        },
        tmp_path,
    )
    facts = {
        "rank": 55,
        "targets": [
            {"rank": 40, "name": "same", "server_id": 22077, "power": 1, "is_npc": False},
            {"rank": 41, "name": "alliance", "server_id": 22055, "power": 1, "is_npc": False},
            {"rank": 42, "name": "ally", "server_id": 22064, "power": 1, "is_npc": False},
            {"rank": 48, "name": "npc", "server_id": 22077, "power": 0, "is_npc": True},
            {"rank": 49, "name": "other", "server_id": 22049, "power": 1, "is_npc": False},
        ],
    }

    selected = select_daofa_target(facts, battle_score=10, data_dir=tmp_path)
    assert selected is not None
    assert selected["name"] == "npc"

    facts["targets"] = facts["targets"][:3]
    assert select_daofa_target(facts, battle_score=10, data_dir=tmp_path)["name"] == "ally"


def test_force_finish_uses_the_weakest_target_regardless_of_rank_or_self_power() -> None:
    facts = {
        "rank": 26,
        "targets": [
            {"rank": 21, "name": "ahead", "server_id": 1, "power": 5, "is_npc": False},
            {"rank": 27, "name": "behind", "server_id": 2, "power": 2, "is_npc": False},
        ],
    }
    selected = select_daofa_target(facts, battle_score=1, force_finish=True)
    assert selected is not None and selected["name"] == "behind"


def test_force_finish_uses_the_weakest_instrumented_target() -> None:
    facts = {
        "rank": None,
        "targets": [
            {"rank": 21, "name": "too-strong", "server_id": 1, "power": 20, "is_npc": False},
            {"rank": 27, "name": "behind", "server_id": 2, "power": 2, "is_npc": False},
            {"rank": 28, "name": "behind-stronger", "server_id": 3, "power": 4, "is_npc": False},
        ],
    }
    selected = select_daofa_target(
        facts,
        battle_score=None,
        force_finish=True,
    )
    assert selected is not None and selected["name"] == "behind"


def test_force_finish_window_uses_sunday_settlement() -> None:
    assert daofa_settlement_at(datetime(2026, 7, 18, 20, 0)) == datetime(2026, 7, 19, 0, 0)
    assert daofa_settlement_at(datetime(2026, 7, 19, 20, 0)) == datetime(2026, 7, 19, 22, 0)
    assert daofa_force_finish_at(datetime(2026, 7, 18, 20, 0)) == datetime(2026, 7, 18, 23, 30)
    assert daofa_force_finish_at(datetime(2026, 7, 19, 20, 0)) == datetime(2026, 7, 19, 21, 30)
    assert should_force_finish_daofa(datetime(2026, 7, 18, 23, 29, 59)) is False
    assert should_force_finish_daofa(datetime(2026, 7, 18, 23, 30)) is True
    assert should_force_finish_daofa(datetime(2026, 7, 18, 23, 35)) is True
    assert should_force_finish_daofa(datetime(2026, 7, 19, 21, 29, 59)) is False
    assert should_force_finish_daofa(datetime(2026, 7, 19, 21, 30)) is True
    assert should_force_finish_daofa(datetime(2026, 7, 19, 21, 35)) is True


def test_no_target_retry_defaults_to_one_hour_and_is_business_configurable() -> None:
    now = datetime(2026, 7, 26, 18, 52, 0)

    assert daofa_no_target_retry_at(now) == datetime(2026, 7, 26, 19, 52, 0)
    assert daofa_no_target_retry_at(
        now,
        {"retry_seconds": 60, "no_target_retry_seconds": 1800},
    ) == datetime(2026, 7, 26, 19, 22, 0)


def test_no_target_retry_is_capped_at_the_force_finish_time() -> None:
    assert daofa_no_target_retry_at(datetime(2026, 7, 18, 23, 0)) == datetime(
        2026, 7, 18, 23, 30
    )
    assert daofa_no_target_retry_at(datetime(2026, 7, 19, 21, 0)) == datetime(
        2026, 7, 19, 21, 30
    )


def test_daofa_runtime_facts_must_be_complete_and_advance_after_round() -> None:
    previous = {
        "rank": 26,
        "remain_times": 2,
        "targets": [{"rank": 21}],
    }

    assert daofa_runtime_facts_advanced(
        previous,
        {
            "available": True,
            "complete": True,
            "rank": 21,
            "remain_times": 1,
            "targets": [{"rank": 16}],
        },
    )
    assert daofa_runtime_facts_advanced(
        previous,
        {
            "available": True,
            "complete": True,
            "rank": 21,
            "remain_times": 0,
            "targets": [],
        },
    )
    assert not daofa_runtime_facts_advanced(
        previous,
        {
            "available": True,
            "complete": True,
            **previous,
        },
    )
    assert not daofa_runtime_facts_advanced(
        previous,
        {
            "available": False,
            "complete": False,
            "rank": 21,
            "remain_times": 1,
            "targets": [{"rank": 16}],
        },
    )


def test_instrumented_remaining_times_override_stale_ocr_after_bulk_consumption() -> None:
    assert resolve_daofa_remaining_times(
        {"available": True, "remain_times": 0},
        ocr_remaining=1,
    ) == 0
    assert resolve_daofa_remaining_times(
        {"available": False, "remain_times": None},
        ocr_remaining=1,
    ) == 1
