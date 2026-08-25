from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

import backend.core.fanxiu.data_annotation.tasks.moyu_challenge as moyu_challenge
from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks.moyu_challenge import (
    MoyuChallengeTaskMixin,
    next_moyu_challenge_time,
    select_moyu_reward,
)


def _consume(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


class _Clock(datetime):
    value = datetime(2026, 8, 7, 11, 59)

    @classmethod
    def now(cls, tz=None):
        del tz
        return cls.value


class _Runner(MoyuChallengeTaskMixin):
    def __init__(self):
        self.actions: list[str] = []
        self.next_times = []

    def _persist_scheduler_task_next_time(self, task_id, next_time):
        self.next_times.append((task_id, next_time))

    def _persist_admission_decision(self, payload, decision):
        result = dict(decision)
        next_time = result.pop("next_time")
        self._persist_scheduler_task_next_time(payload["__scheduler_task_id"], next_time)
        return result

    def _moyu_open_activity(self, runtime, payload=None):
        del runtime, payload
        self.actions.append("open")
        yield "running"

    def _moyu_try_challenge(self, runtime, payload):
        del runtime, payload
        self.actions.append("challenge")
        yield "running"
        return {
            "entered": False,
            "completed": False,
            "message": "点击挑战后未出现 #463/#464，判定已错过 Boss 窗口",
        }

    def _moyu_return_world(self, runtime):
        del runtime
        self.actions.append("world")
        yield "running"

    def _moyu_claim_reward(self, runtime, payload):
        del runtime, payload
        self.actions.append("reward")
        yield "running"
        return {
            "claimed": True,
            "already_claimed": False,
            "message": "选择第2轮第11名奖励并领取成功",
        }

    def _moyu_close_reward_and_return_world(self, runtime):
        del runtime
        self.actions.append("close")
        yield "running"


class _Runtime:
    attrs = {"payload": {}}

    def set_next_time(self, next_time):
        self.next_time = next_time


class _ChallengeRuntime:
    def __init__(self, landings):
        self.landings = iter(landings)
        self.actions = []
        self.next_times = []

    def set_next_time(self, next_time):
        self.next_times.append(next_time)

    def wait_click(self, scene_id, shape):
        self.actions.append(("click", scene_id, shape))
        if False:
            yield None

    def wait_view(self, *scene_ids, **options):
        self.actions.append(("wait", scene_ids, options))
        if False:
            yield None
        value = next(self.landings)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(id=value)

    def wait_click_then_view(self, scene_id, shape, target, **options):
        self.actions.append(("click_then_view", scene_id, shape, target, options))
        if False:
            yield None
        return SimpleNamespace(id=target)


class _ChallengeButtonRuntime(_ChallengeRuntime):
    def ocr_text_in_shapes(self, scene_id, shapes, *, padding):
        self.actions.append(("ocr_button", scene_id, shapes, padding))
        return "挑战"


class _ActivityRuntime:
    def __init__(self):
        self.actions = []
        self.next_times = []

    def set_next_time(self, next_time):
        self.next_times.append(next_time)

    def goto_view(self, scene_id):
        self.actions.append(("goto", scene_id))
        if False:
            yield None

    def wait_click_then_view(self, scene_id, shape, target, **options):
        self.actions.append(("click_then_view", scene_id, shape, target, options))
        if False:
            yield None
        return SimpleNamespace(id=target)

    def open_daily_entry(self, **options):
        self.actions.append(("open_daily_entry", options))
        if False:
            yield None
        return "open"

    def wait_view_or_ocr(self, scene_id, predicate, **options):
        self.actions.append(("wait", (scene_id,), predicate("大道外域 魔狱封阵"), options))
        if False:
            yield None
        return ("text", scene_id, 0.0)


def test_moyu_challenge_is_registered_and_seeded_twice_daily():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("moyu_challenge")
    task = next(
        item
        for item in default_data_annotation_scheduler_tasks(
            datetime(2026, 8, 7, 10, 0)
        )
        if item["id"] == "moyu-challenge"
    )

    assert definition is not None
    assert definition.label == "魔狱_挑战"
    assert definition.scheduler_supported is True
    assert task["task_type"] == "moyu_challenge"
    assert task["next_time"] == "2026-08-07 11:59:00"
    assert task["error_retry_delay_seconds"] == 60
    assert task["payload"]["activity_entry_timeout_seconds"] == 60


def test_moyu_activity_entry_wait_allows_long_flight_animation():
    runner = MoyuChallengeTaskMixin()
    runtime = _ActivityRuntime()

    _consume(
        runner._moyu_open_activity(
            runtime,
            {"activity_entry_timeout_seconds": 75},
        )
    )

    wait = next(
        action
        for action in runtime.actions
        if action[0] == "wait" and action[1] == (400,)
    )
    assert wait[2] is True
    assert wait[3]["timeout"] == 75.0
    assert wait[3]["label"] == "魔狱_挑战：等待大道外域 #400"


def test_next_moyu_challenge_time_switches_between_single_job_rounds():
    assert next_moyu_challenge_time(datetime(2026, 8, 7, 11, 58, 59)) == datetime(
        2026, 8, 7, 11, 59
    )
    assert next_moyu_challenge_time(datetime(2026, 8, 7, 11, 59)) == datetime(
        2026, 8, 7, 17, 59
    )
    assert next_moyu_challenge_time(datetime(2026, 8, 7, 17, 59)) == datetime(
        2026, 8, 8, 11, 59
    )


def test_admission_keeps_evening_reward_recovery_open_until_2200(monkeypatch):
    monkeypatch.setattr(moyu_challenge, "job_now", lambda: _Clock.value)
    runner = _Runner()

    _Clock.value = datetime(2026, 8, 7, 18, 30)
    assert runner.moyu_challenge_admission(
        {"__scheduler_task_id": "moyu-challenge"}
    ) is None

    _Clock.value = datetime(2026, 8, 7, 22, 0)
    result = runner.moyu_challenge_admission(
        {"__scheduler_task_id": "moyu-challenge"}
    )
    assert result is not None
    assert "next_time" not in result


def test_admission_uses_job_business_time_for_planned_run(monkeypatch):
    runner = _Runner()
    monkeypatch.setattr(
        moyu_challenge,
        "job_now",
        lambda: datetime(2026, 8, 7, 12, 21),
    )

    result = runner.moyu_challenge_admission(
        {"__scheduler_task_id": "moyu-challenge"}
    )

    assert result is not None
    assert result["message"] == "魔狱_挑战：当前不在挑战或晚间领奖窗口，未执行游戏操作"
    assert runner.next_times == [("moyu-challenge", "2026-08-07 17:59:00")]


def test_reward_selection_prefers_better_rank_and_second_round_on_tie():
    first = {"round": 1, "activity_id": 250301, "rank": 8, "claimed": False}
    second = {"round": 2, "activity_id": 250301, "rank": 11, "claimed": False}
    assert select_moyu_reward([first, second]) == first

    second["rank"] = 8
    assert select_moyu_reward([first, second]) == second


def test_reward_page_accepts_live_title_without_requiring_round_row_ocr():
    runner = MoyuChallengeTaskMixin()

    assert runner._moyu_reward_text("?魔狱封阵奖励") is True
    assert runner._moyu_reward_text("魔狱奖励 第一轮 第二轮") is True
    assert runner._moyu_reward_text("魔狱封阵") is False


def test_reward_claim_uses_specialized_scene_539_and_named_confirm(monkeypatch):
    class Runtime:
        def __init__(self):
            self.actions = []
            self.ocr_reads = iter(["魔狱封阵奖励", "1/1 已领取"])

        def current_scene(self, scene_ids, *, update):
            self.actions.append(("current", tuple(scene_ids), update))
            return 401, 100.0, "frame"

        def wait_click(self, scene_id, shape):
            self.actions.append(("wait_click", scene_id, shape))
            if False:
                yield None

        def wait_view(self, *scene_ids, **options):
            self.actions.append(("wait_view", scene_ids, options))
            if False:
                yield None
            return SimpleNamespace(id=scene_ids[0])

        def cur_frame(self, *, update):
            self.actions.append(("frame", update))
            return "frame"

        def ocr_text(self, frame_data_url=None, *, update=False):
            self.actions.append(("ocr", frame_data_url, update))
            return next(self.ocr_reads)

        def ocr_text_in_shapes(self, scene_id, shapes, **options):
            self.actions.append(("ocr_shapes", scene_id, shapes, options))
            return "领取"

        def click_shape_center(self, scene_id, shape):
            self.actions.append(("click", scene_id, shape))

        def wait_action_settle(self, seconds):
            self.actions.append(("settle", seconds))
            if False:
                yield None

        def expect_views(self, *scene_ids):
            runtime = self

            class ExpectedViews:
                def __enter__(self):
                    runtime.actions.append(("expect_enter", scene_ids))

                def __exit__(self, exc_type, exc, traceback):
                    runtime.actions.append(("expect_exit", scene_ids))

            return ExpectedViews()

    monkeypatch.setattr(moyu_challenge, "datetime", _Clock)
    _Clock.value = datetime(2026, 8, 7, 20, 0)
    monkeypatch.setattr(
        moyu_challenge,
        "read_godsoul_boss_reward_snapshot",
        lambda: {
            "complete": True,
            "already_claimed": False,
            "rewards": [
                {"round": 1, "activity_id": 250301, "rank": 12, "claimed": False},
                {"round": 2, "activity_id": 250301, "rank": 7, "claimed": False},
            ],
        },
    )
    runtime = Runtime()

    result = _consume(MoyuChallengeTaskMixin()._moyu_claim_reward(runtime, {}))

    assert result["claimed"] is True
    assert result["selected"]["round"] == 2
    assert ("click", 466, "第2轮") in runtime.actions
    assert ("click", 466, "领取") in runtime.actions
    assert ("expect_enter", (539,)) in runtime.actions
    assert any(action[:2] == ("wait_view", (539,)) for action in runtime.actions)
    assert ("click", 539, "确认") in runtime.actions


def test_already_open_reward_page_is_not_misread_as_background_scene_401(monkeypatch):
    class Runtime:
        def __init__(self):
            self.actions = []

        def current_scene(self, scene_ids, *, update):
            self.actions.append(("current", tuple(scene_ids), update))
            return 466, 100.0, "frame"

        def wait_click(self, scene_id, shape):
            self.actions.append(("wait_click", scene_id, shape))
            if False:
                yield None

        def cur_frame(self, *, update):
            return "frame"

        def ocr_text(self, frame_data_url=None, *, update=False):
            return "魔狱封阵奖励 今日已领取奖励 1/1"

        def ocr_text_in_shapes(self, scene_id, shapes, **options):
            self.actions.append(("ocr_shapes", scene_id, shapes, options))
            return "已领取"

    monkeypatch.setattr(moyu_challenge, "datetime", _Clock)
    _Clock.value = datetime(2026, 8, 7, 20, 0)
    runtime_reads = []
    monkeypatch.setattr(
        moyu_challenge,
        "read_godsoul_boss_reward_snapshot",
        lambda: runtime_reads.append("read") or pytest.fail(
            "已领取 GUI 终态不应再读取排名或进入领取事务"
        ),
    )
    runtime = Runtime()

    result = _consume(MoyuChallengeTaskMixin()._moyu_claim_reward(runtime, {}))

    assert result["already_claimed"] is True
    assert ("current", (466, 401), True) in runtime.actions
    assert not any(action[0] == "wait_click" for action in runtime.actions)
    assert not any(action[:3] == ("click", 466, "领取") for action in runtime.actions)
    assert not any(action[0] == "expect_enter" for action in runtime.actions)
    assert runtime_reads == []


def test_reward_claim_fails_closed_when_claim_button_state_is_unknown(monkeypatch):
    class Runtime:
        def current_scene(self, scene_ids, *, update):
            return 466, 100.0, "frame"

        def cur_frame(self, *, update):
            return "frame"

        def ocr_text(self, frame_data_url=None, *, update=False):
            return "魔狱封阵奖励"

        def ocr_text_in_shapes(self, scene_id, shapes, **options):
            return "奖励"

    monkeypatch.setattr(moyu_challenge, "datetime", _Clock)
    _Clock.value = datetime(2026, 8, 7, 20, 0)
    monkeypatch.setattr(
        moyu_challenge,
        "read_godsoul_boss_reward_snapshot",
        lambda: pytest.fail("未知 GUI 状态不应读取排名或继续事务"),
    )

    with pytest.raises(RuntimeError, match="拒绝点击"):
        _consume(MoyuChallengeTaskMixin()._moyu_claim_reward(Runtime(), {}))


def test_reward_runtime_gap_defers_without_clicking_claim(monkeypatch):
    class Runtime:
        def __init__(self):
            self.actions = []

        def current_scene(self, scene_ids, *, update):
            return 466, 100.0, "frame"

        def cur_frame(self, *, update):
            return "frame"

        def ocr_text(self, frame_data_url=None, *, update=False):
            return "魔狱封阵奖励"

        def ocr_text_in_shapes(self, scene_id, shapes, **options):
            return "领取"

        def click_shape_center(self, scene_id, shape):
            self.actions.append(("click", scene_id, shape))

    monkeypatch.setattr(moyu_challenge, "datetime", _Clock)
    _Clock.value = datetime(2026, 8, 7, 20, 0)
    monkeypatch.setattr(
        moyu_challenge,
        "read_godsoul_boss_reward_snapshot",
        lambda: {"complete": False, "reason": "Runtime root missing"},
    )
    runtime = Runtime()

    result = _consume(MoyuChallengeTaskMixin()._moyu_claim_reward(runtime, {}))

    assert result["deferred"] is True
    assert "拒绝猜测领取" in result["message"]
    assert runtime.actions == []


def test_evening_runtime_gap_returns_world_and_schedules_coarse_reward_recheck(
    monkeypatch,
):
    class Runner(_Runner):
        def _moyu_claim_reward(self, runtime, payload):
            del runtime, payload
            self.actions.append("reward")
            yield "running"
            return {
                "claimed": False,
                "already_claimed": False,
                "deferred": True,
                "message": "排名证据暂不可用",
            }

    _Clock.value = datetime(2026, 8, 7, 20, 0)
    monkeypatch.setattr(moyu_challenge, "datetime", _Clock)
    runner = Runner()
    runtime = _Runtime()

    result = _consume(runner.moyu_challenge_flow(runtime))

    assert runner.actions == ["reward", "close"]
    assert result["result"] == "success"
    assert runtime.next_time == "2026-08-07 20:10:00"


def test_return_world_uses_named_return_actions_after_scene_identity():
    class Runtime:
        def __init__(self):
            self.actions = []

        def current_scene(self, scene_ids, *, update):
            return 401, 100.0, "frame"

        def click_shape_center(self, scene_id, shape):
            self.actions.append(("click", scene_id, shape))

        def wait_view(self, *scene_ids, **options):
            self.actions.append(("wait", scene_ids, options))
            if False:
                yield None
            return SimpleNamespace(id=400 if 400 in scene_ids else scene_ids[0])

    runtime = Runtime()

    _consume(MoyuChallengeTaskMixin()._moyu_return_world(runtime))

    assert ("click", 401, "返回") in runtime.actions
    assert ("click", 400, "返回") in runtime.actions
    assert any(action[1] == (34,) for action in runtime.actions if action[0] == "wait")


def test_return_world_reuses_narrow_314_transition_guard_for_unknown_animation():
    class Runtime:
        def __init__(self):
            self.ctx = {"existing": True}
            self.guard_seen = None

        def current_scene(self, scene_ids, *, update):
            return None, 0.0, "animation-frame"

        def goto_view(self, scene_id):
            assert scene_id == 34
            self.guard_seen = dict(self.ctx["_go_scene_unknown_transition_guard"])
            yield "waiting"

    runtime = Runtime()

    _consume(MoyuChallengeTaskMixin()._moyu_return_world(runtime))

    assert runtime.guard_seen == {
        "reference_scene_id": 314,
        "similarity_threshold": 94.0,
        "wait_seconds": 120.0,
        "phase": "moyu_wait_mozu_world_transition",
        "label": "魔狱结算回城动画",
    }
    assert "_go_scene_unknown_transition_guard" not in runtime.ctx


def test_return_world_restores_outer_transition_guard():
    outer = {"reference_scene_id": 999, "label": "outer"}

    class Runtime:
        def __init__(self):
            self.ctx = {"_go_scene_unknown_transition_guard": outer}

        def current_scene(self, scene_ids, *, update):
            return None, 0.0, "animation-frame"

        def goto_view(self, scene_id):
            assert self.ctx["_go_scene_unknown_transition_guard"]["reference_scene_id"] == 314
            if False:
                yield None

    runtime = Runtime()
    _consume(MoyuChallengeTaskMixin()._moyu_return_world(runtime))

    assert runtime.ctx["_go_scene_unknown_transition_guard"] is outer


def test_reward_confirmation_accepts_confirm_or_ok_but_not_unrelated_text():
    runner = MoyuChallengeTaskMixin()

    assert runner._moyu_reward_confirm_text("取消  确认") is True
    assert runner._moyu_reward_confirm_text("取消  确定") is True
    assert runner._moyu_reward_confirm_text("点击空白关闭") is False


def test_reward_claim_action_state_prefers_already_claimed_over_claimable():
    runner = MoyuChallengeTaskMixin()

    assert runner._moyu_reward_claim_action_state(" 已 领 取 ") == "claimed"
    assert runner._moyu_reward_claim_action_state("领取") == "claimable"
    assert runner._moyu_reward_claim_action_state("奖励") is None


def test_evening_missed_challenge_still_claims_reward(monkeypatch):
    _Clock.value = datetime(2026, 8, 7, 17, 59)
    monkeypatch.setattr(moyu_challenge, "datetime", _Clock)
    runner = _Runner()

    result = _consume(runner.moyu_challenge_flow(_Runtime()))

    assert runner.actions == ["open", "challenge", "reward", "close"]
    assert result["reward"]["claimed"] is True
    assert "next_time" not in result


def test_morning_missed_challenge_returns_world_without_reward(monkeypatch):
    _Clock.value = datetime(2026, 8, 7, 11, 59)
    monkeypatch.setattr(moyu_challenge, "datetime", _Clock)
    runner = _Runner()

    result = _consume(runner.moyu_challenge_flow(_Runtime()))

    assert runner.actions == ["open", "challenge", "world"]
    assert "reward" not in result
    assert "next_time" not in result


def test_moyu_accepts_real_battle_scene_85_and_dynamic_settlement(monkeypatch):
    runner = MoyuChallengeTaskMixin()
    runtime = _ChallengeRuntime([463, 85, 465])
    snapshots = iter(
        [
            {"ok": True, "complete": True, "settled": False},
            {
                "ok": True,
                "complete": True,
                "settled": True,
                "settlement": {"map_id": 999303, "total_damage": 1507680},
            },
        ]
    )
    monkeypatch.setattr(
        moyu_challenge,
        "read_godsoul_boss_challenge_snapshot",
        lambda: next(snapshots),
    )

    result = _consume(runner._moyu_try_challenge(runtime, {}))

    assert result["entered"] is True
    assert result["responded"] is True
    assert result["completed"] is True
    assert ("click", 463, "确认") in runtime.actions
    assert any(action[:2] == ("wait", (464, 85, 465)) for action in runtime.actions)
    assert any(action[:2] == ("wait", (465, 401)) for action in runtime.actions)
    assert any(action[:3] == ("click_then_view", 465, "继续") for action in runtime.actions)


def test_moyu_confirmation_ignores_transient_401_until_real_battle(monkeypatch):
    class FilteringRuntime(_ChallengeRuntime):
        def __init__(self):
            super().__init__([])
            self.timeline = iter([463, 401, 85, 465])

        def wait_view(self, *scene_ids, **options):
            self.actions.append(("wait", scene_ids, options))
            if False:
                yield None
            while True:
                value = next(self.timeline)
                if value in scene_ids:
                    return SimpleNamespace(id=value)

    runtime = FilteringRuntime()
    monkeypatch.setattr(
        moyu_challenge,
        "read_godsoul_boss_challenge_snapshot",
        lambda: {"ok": True, "complete": True, "settled": False},
    )

    result = _consume(MoyuChallengeTaskMixin()._moyu_try_challenge(runtime, {}))

    assert result["completed"] is True
    assert ("wait", (464, 85, 465), {
        "timeout": 30.0,
        "label": "魔狱_挑战：确认后的战斗或结算响应",
    }) in runtime.actions
    assert not any(
        action[0] == "wait" and action[1] == (464, 85, 465, 401)
        for action in runtime.actions
    )
    assert any(action[:2] == ("wait", (465, 401)) for action in runtime.actions)


def test_evening_unproven_confirmed_attempt_defers_reward_without_reopening(monkeypatch):
    class Runner(_Runner):
        def _moyu_try_challenge(self, runtime, payload):
            del runtime, payload
            self.actions.append("challenge")
            yield "running"
            return {
                "entered": True,
                "responded": True,
                "completed": False,
                "message": "已进入战斗；等待结算超时，拒绝在本轮重复挑战",
            }

    _Clock.value = datetime(2026, 8, 7, 18, 3)
    monkeypatch.setattr(moyu_challenge, "datetime", _Clock)
    runner = Runner()
    runtime = _Runtime()

    result = _consume(runner.moyu_challenge_flow(runtime))

    assert result["result"] == "success"
    assert result["reward_deferred"] is True
    assert result["current_scene"] is None
    assert runner.actions == ["open", "challenge"]
    assert runtime.next_time == "2026-08-07 18:20:00"
    assert "未重新打开活动" in result["message"]


def test_moyu_uses_challenge_action_when_same_button_changes_text(monkeypatch):
    runner = MoyuChallengeTaskMixin()
    runtime = _ChallengeButtonRuntime([TimeoutError("window ended")])
    monkeypatch.setattr(
        moyu_challenge,
        "read_godsoul_boss_challenge_snapshot",
        lambda: {"ok": True, "complete": True, "settled": False},
    )

    result = _consume(runner._moyu_try_challenge(runtime, {}))

    assert ("ocr_button", 401, ("报名",), 12) in runtime.actions
    assert ("click", 401, "挑战") in runtime.actions
    assert result["entered"] is False


def test_moyu_settled_runtime_preflight_never_clicks_challenge(monkeypatch):
    runner = MoyuChallengeTaskMixin()
    runtime = _ChallengeButtonRuntime([])
    snapshot = {"ok": True, "complete": True, "settled": True}
    monkeypatch.setattr(
        moyu_challenge,
        "read_godsoul_boss_challenge_snapshot",
        lambda: snapshot,
    )

    result = _consume(runner._moyu_try_challenge(runtime, {}))

    assert result == {
        "entered": False,
        "responded": True,
        "completed": True,
        "message": "Runtime 已确认本轮结算，未重复点击挑战",
        "runtime_snapshot": snapshot,
    }
    assert runtime.actions == []


def test_moyu_confirmation_response_never_retries_same_round_on_visual_timeout(
    monkeypatch,
):
    runner = MoyuChallengeTaskMixin()
    runtime = _ChallengeRuntime([463, TimeoutError("battle scene mismatch")])
    snapshots = iter(
        [
            {"ok": True, "complete": True, "settled": False},
            {"ok": True, "complete": True, "settled": True},
        ]
    )
    monkeypatch.setattr(
        moyu_challenge,
        "read_godsoul_boss_challenge_snapshot",
        lambda: next(snapshots),
    )

    result = _consume(runner._moyu_try_challenge(runtime, {}))

    assert result["entered"] is True
    assert result["responded"] is True
    assert result["completed"] is True
    assert "拒绝重复挑战" in result["message"]
