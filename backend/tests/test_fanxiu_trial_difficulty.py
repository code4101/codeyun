from __future__ import annotations

import threading

from backend.core.fanxiu.data_annotation.trial_difficulty import (
    ObservedTrialDifficulty,
    build_even_trial_difficulty_plan,
    find_current_trial_difficulty,
)
from backend.core.fanxiu.data_annotation.trial_progression import (
    ObservedTrialAttempts,
    ObservedTrialHomeState,
    parse_xianqiao_trial_attempts,
)
from backend.core.fanxiu.runtime.behavior_tree import create_fanxiu_runtime_runner


def _finish(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def test_even_trial_difficulty_model_matches_known_levels():
    level_25 = build_even_trial_difficulty_plan(25)
    level_26 = build_even_trial_difficulty_plan(26)

    assert level_25.positions == (5, 5, 5, 5, 4)
    assert level_25.values == (10, 10, 15, 10, 40)
    assert level_26.positions == (5, 5, 5, 5, 5)
    assert level_26.values == (10, 10, 15, 10, 50)


def test_current_trial_difficulty_parser_uses_the_live_display_text():
    observation = find_current_trial_difficulty(
        [{"text": "当前难度为25级，完成挑战可得以上奖励"}]
    )

    assert observation == ObservedTrialDifficulty(
        level=25,
        text="当前难度为25级，完成挑战可得以上奖励",
    )


def test_trial_settings_orchestration_keeps_the_business_order(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    runtime = runner._fanxiu_runtime(
        {"images": {358: {"id": 358, "title": "设置难度", "width": 900, "height": 1600, "shapes": []}}},
        stop_event=threading.Event(),
    )
    events: list[str] = []

    def allocate(*_args, **_kwargs):
        events.append("five_elements")
        if False:
            yield None
        return {"after": {"remaining": 0}}

    def read(*_args, **_kwargs):
        events.append("read_current")
        return ObservedTrialDifficulty(level=25, text="当前难度为25级")

    def configure(_view, target_level, **_kwargs):
        events.append(f"configure_{target_level}")
        if False:
            yield None
        return {"final_level": target_level}

    monkeypatch.setattr(runtime, "allocate_balanced_points", allocate)
    monkeypatch.setattr(runtime, "read_current_trial_difficulty", read)
    monkeypatch.setattr(runtime, "configure_even_trial_difficulty", configure)

    result = _finish(runtime.prepare_xianqiao_trial_settings(358))

    assert events == ["five_elements", "read_current", "configure_26"]
    assert result["current_level"] == 25
    assert result["target_level"] == 26
    assert result["difficulty"] == {"final_level": 26}


def test_trial_settings_accepts_absolute_target_after_a_failed_higher_configuration(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    runtime = runner._fanxiu_runtime(
        {"images": {358: {"id": 358, "title": "设置难度", "width": 900, "height": 1600, "shapes": []}}},
        stop_event=threading.Event(),
    )
    configured: list[int] = []

    def allocate(*_args, **_kwargs):
        if False:
            yield None
        return {"after": {"remaining": 0}}

    def configure(_view, target_level, **_kwargs):
        configured.append(target_level)
        if False:
            yield None
        return {"final_level": target_level}

    monkeypatch.setattr(runtime, "allocate_balanced_points", allocate)
    monkeypatch.setattr(
        runtime,
        "read_current_trial_difficulty",
        lambda *_args, **_kwargs: ObservedTrialDifficulty(level=40, text="当前难度为40级"),
    )
    monkeypatch.setattr(runtime, "configure_even_trial_difficulty", configure)

    result = _finish(runtime.prepare_xianqiao_trial_settings(358, target_level=36))

    assert result["current_level"] == 40
    assert result["target_level"] == 36
    assert configured == [36]


def _trial_challenge_runtime():
    runner = create_fanxiu_runtime_runner()
    images = {
        scene_id: {
            "id": scene_id,
            "title": title,
            "width": 900,
            "height": 1600,
            "shapes": [{"title": shape, "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.05}],
        }
        for scene_id, title, shape in (
            (357, "仙窍试炼", "挑战"),
            (359, "挑战确认", "开始挑战"),
            (360, "难度确认", "继续挑战"),
            (366, "扫荡确认", "开启扫荡"),
        )
    }
    images[34] = {
        "id": 34,
        "title": "世界",
        "width": 900,
        "height": 1600,
        "shapes": [],
    }
    return runner._fanxiu_runtime({"images": images}, stop_event=threading.Event())


def test_trial_challenge_reacts_to_each_scene_instead_of_difficulty_history(monkeypatch):
    runtime = _trial_challenge_runtime()
    observations = iter((357, 357, 359, 359, 360))
    clicks: list[tuple[int, str]] = []

    monkeypatch.setattr(
        runtime,
        "current_scene",
        lambda *_args, **_kwargs: (next(observations), 100.0, "frame"),
    )
    monkeypatch.setattr(
        runtime,
        "click_shape",
        lambda view, shape, **_kwargs: clicks.append((int(view), str(shape))),
    )

    result = _finish(runtime.start_xianqiao_trial_challenge(settle_seconds=0))

    assert clicks == [(357, "挑战"), (359, "开始挑战"), (360, "继续挑战")]
    assert result["exit_reason"] == "continue_confirmed"


def test_trial_challenge_can_resume_directly_from_optional_confirmation(monkeypatch):
    runtime = _trial_challenge_runtime()
    observations = iter((359, 360))
    clicks: list[tuple[int, str]] = []

    monkeypatch.setattr(
        runtime,
        "current_scene",
        lambda *_args, **_kwargs: (next(observations), 100.0, "frame"),
    )
    monkeypatch.setattr(
        runtime,
        "click_shape",
        lambda view, shape, **_kwargs: clicks.append((int(view), str(shape))),
    )

    result = _finish(runtime.start_xianqiao_trial_challenge(settle_seconds=0))

    assert clicks == [(359, "开始挑战"), (360, "继续挑战")]
    assert result["exit_reason"] == "continue_confirmed"


def test_trial_challenge_handles_sweep_as_an_observed_branch(monkeypatch):
    runtime = _trial_challenge_runtime()
    observations = iter((357, 366))
    clicks: list[tuple[int, str]] = []
    delays: list[float] = []

    monkeypatch.setattr(
        runtime,
        "current_scene",
        lambda *_args, **_kwargs: (next(observations), 100.0, "frame"),
    )
    monkeypatch.setattr(
        runtime,
        "click_shape",
        lambda view, shape, **_kwargs: clicks.append((int(view), str(shape))),
    )

    def wait_home(*_args, **_kwargs):
        if False:
            yield None
        return runtime.view(357)

    def settle(seconds):
        delays.append(float(seconds))
        if False:
            yield None

    monkeypatch.setattr(runtime, "wait_view", wait_home)
    monkeypatch.setattr(runtime, "wait_action_settle", settle)

    result = _finish(
        runtime.start_xianqiao_trial_challenge(
            settle_seconds=0,
            sweep_result_delay=5,
        )
    )

    assert clicks == [(357, "挑战"), (366, "开启扫荡")]
    assert delays == [0.0, 5.0]
    assert result["exit_reason"] == "sweep_completed"
    assert result["last_scene"] == 357


def test_trial_challenge_accepts_direct_entry_when_no_confirmation_appears(monkeypatch):
    runtime = _trial_challenge_runtime()
    observations = iter((357, None, None, None))
    clicks: list[tuple[int, str]] = []

    monkeypatch.setattr(
        runtime,
        "current_scene",
        lambda *_args, **_kwargs: (next(observations), 0.0, "frame"),
    )
    monkeypatch.setattr(
        runtime,
        "click_shape",
        lambda view, shape, **_kwargs: clicks.append((int(view), str(shape))),
    )

    result = _finish(
        runtime.start_xianqiao_trial_challenge(
            stable_departure_polls=3,
            settle_seconds=0,
        )
    )

    assert clicks == [(357, "挑战")]
    assert result["exit_reason"] == "left_confirmation_chain"


def test_trial_result_treats_362_as_battle_and_waits_for_success_exit(monkeypatch):
    runtime = _trial_challenge_runtime()
    runtime.ctx["images"].update({
        361: {
            "id": 361,
            "title": "成功结算",
            "width": 900,
            "height": 1600,
            "shapes": [{"title": "退出", "x": 0.4, "y": 0.8, "w": 0.2, "h": 0.05}],
        },
        362: {
            "id": 362,
            "title": "仙窍战斗中",
            "width": 900,
            "height": 1600,
            "shapes": [],
        },
        365: {
            "id": 365,
            "title": "失败结算",
            "width": 900,
            "height": 1600,
            "shapes": [{"title": "退出", "x": 0.4, "y": 0.8, "w": 0.2, "h": 0.05}],
        },
    })
    events: list[str] = []
    waited = iter((362, 361))

    def wait_view(*_args, **_kwargs):
        value = next(waited)
        events.append("entered_362" if value == 362 else "result_361")
        if False:
            yield None
        return runtime.view(value)

    monkeypatch.setattr(runtime, "wait_view", wait_view)
    monkeypatch.setattr(runtime, "cur_frame", lambda **_kwargs: "result-frame")
    monkeypatch.setattr(runtime, "ocr_text", lambda **_kwargs: "挑战成功 点击退出")

    result = _finish(runtime.wait_xianqiao_trial_result(result_settle_seconds=0))

    assert events == ["entered_362", "result_361"]
    assert result["outcome"] == "success"
    assert result["result_scene"] == 361


def test_trial_result_recognizes_failure_by_scene_identity(monkeypatch):
    runtime = _trial_challenge_runtime()
    runtime.ctx["images"].update({
        361: {"id": 361, "title": "成功结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
        362: {"id": 362, "title": "仙窍战斗中", "width": 900, "height": 1600, "shapes": []},
        365: {"id": 365, "title": "失败结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
    })
    waited = iter((362, 365))

    def immediate_wait(*_args, **_kwargs):
        if False:
            yield None
        return runtime.view(next(waited))

    monkeypatch.setattr(runtime, "wait_view", immediate_wait)
    monkeypatch.setattr(runtime, "cur_frame", lambda **_kwargs: "failure-frame")
    monkeypatch.setattr(runtime, "ocr_text", lambda **_kwargs: "挑战失败")
    clicks: list[tuple[int, str]] = []
    monkeypatch.setattr(runtime, "click_shape", lambda view, shape, **_kwargs: clicks.append((int(view), str(shape))))

    result = _finish(runtime.wait_xianqiao_trial_result(result_settle_seconds=0))

    assert result["outcome"] == "failure"
    assert result["result_scene"] == 365
    assert result["ocr_text"] == "挑战失败"
    assert clicks == []


def test_complete_trial_challenge_clicks_exit_only_for_known_success(monkeypatch):
    runtime = _trial_challenge_runtime()
    runtime.ctx["images"].update({
        361: {"id": 361, "title": "成功结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
        362: {"id": 362, "title": "仙窍战斗中", "width": 900, "height": 1600, "shapes": []},
        365: {"id": 365, "title": "失败结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
    })
    clicks: list[tuple[int, str]] = []

    def started(**_kwargs):
        if False:
            yield None
        return {"exit_reason": "continue_confirmed"}

    def finished(**_kwargs):
        if False:
            yield None
        return {"outcome": "success", "result_scene": 361, "_frame_data_url": "result-frame"}

    def wait_home(*_args, **_kwargs):
        if False:
            yield None
        return runtime.view(357)

    monkeypatch.setattr(runtime, "start_xianqiao_trial_challenge", started)
    monkeypatch.setattr(runtime, "wait_xianqiao_trial_result", finished)
    monkeypatch.setattr(runtime, "wait_view", wait_home)
    monkeypatch.setattr(runtime, "click_shape", lambda view, shape, **_kwargs: clicks.append((int(view), str(shape))))

    result = _finish(runtime.complete_xianqiao_trial_challenge(settle_seconds=0))

    assert clicks == [(361, "退出")]
    assert result["returned_home"] is True


def test_complete_trial_challenge_treats_returned_sweep_as_terminal(monkeypatch):
    runtime = _trial_challenge_runtime()

    def swept(**_kwargs):
        if False:
            yield None
        return {"exit_reason": "sweep_completed", "last_scene": 357, "actions": []}

    monkeypatch.setattr(runtime, "start_xianqiao_trial_challenge", swept)
    monkeypatch.setattr(
        runtime,
        "wait_xianqiao_trial_result",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("扫荡后不应等待战斗结算")),
    )

    result = _finish(runtime.complete_xianqiao_trial_challenge(settle_seconds=0))

    assert result["result"] == {"outcome": "sweep", "result_scene": 357}
    assert result["returned_home"] is True


def test_trial_result_reports_auto_expired_popup_when_game_returns_to_world(monkeypatch):
    runtime = _trial_challenge_runtime()
    runtime.ctx["images"].update({
        361: {"id": 361, "title": "成功结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
        362: {"id": 362, "title": "仙窍战斗中", "width": 900, "height": 1600, "shapes": []},
        365: {"id": 365, "title": "失败结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
    })
    waited = iter((362, 34))

    def wait_view(*_args, **_kwargs):
        if False:
            yield None
        return runtime.view(next(waited))

    monkeypatch.setattr(runtime, "wait_view", wait_view)

    result = _finish(runtime.wait_xianqiao_trial_result())

    assert result["outcome"] == "result_expired"
    assert result["result_scene"] == 34


def test_trial_attempt_parser():
    assert parse_xianqiao_trial_attempts("今日剩余:奖励次数:2/5") == ObservedTrialAttempts(
        remaining=2,
        capacity=5,
        text="今日剩余:奖励次数:2/5",
    )
    assert parse_xianqiao_trial_attempts("剩余奖励次数：１／３").remaining == 1


def test_trial_home_observation_reuses_one_frame_for_attempts_and_sweep(monkeypatch):
    runtime = _trial_challenge_runtime()
    seen_frames: list[str] = []

    monkeypatch.setattr(runtime, "cur_frame", lambda **_kwargs: "home-frame")

    def read_attempts(*_args, **kwargs):
        seen_frames.append(kwargs["frame_data_url"])
        return ObservedTrialAttempts(remaining=5, capacity=5, text="奖励次数:5/5")

    def score(*_args, **kwargs):
        seen_frames.append(kwargs["frame_data_url"])
        return 93.0

    monkeypatch.setattr(runtime, "read_xianqiao_trial_attempts", read_attempts)
    monkeypatch.setattr(runtime, "shape_score", score)

    observed = runtime.observe_xianqiao_trial_home()

    assert seen_frames == ["home-frame", "home-frame"]
    assert observed.sweep_available is True
    assert observed.attempts.remaining == 5


def test_trial_probe_uses_sweep_button_to_increment_and_rolls_back_after_failure(monkeypatch):
    runtime = _trial_challenge_runtime()
    observations = iter((
        ObservedTrialHomeState(
            attempts=ObservedTrialAttempts(remaining=2, capacity=5, text="奖励次数:2/5"),
            sweep_available=True,
            sweep_score=96.0,
        ),
        ObservedTrialHomeState(
            attempts=ObservedTrialAttempts(remaining=1, capacity=5, text="奖励次数:1/5"),
            sweep_available=True,
            sweep_score=94.0,
        ),
    ))
    attempts_after = iter((
        ObservedTrialAttempts(remaining=1, capacity=2, text="奖励次数:1/2"),
        ObservedTrialAttempts(remaining=1, capacity=2, text="奖励次数:1/2"),
    ))
    outcomes = iter(("success", "failure"))
    adjustments: list[int] = []

    monkeypatch.setattr(runtime, "current_scene", lambda *_args, **_kwargs: (357, 100.0, "frame"))
    monkeypatch.setattr(runtime, "observe_xianqiao_trial_home", lambda *_args, **_kwargs: next(observations))
    monkeypatch.setattr(runtime, "read_xianqiao_trial_attempts", lambda *_args, **_kwargs: next(attempts_after))

    def adjust(increment, **_kwargs):
        adjustments.append(increment)
        if False:
            yield None
        return {"difficulty_increment": increment}

    def challenge(**_kwargs):
        if False:
            yield None
        return {"result": {"outcome": next(outcomes)}, "returned_home": True}

    monkeypatch.setattr(runtime, "adjust_xianqiao_trial_level", adjust)
    monkeypatch.setattr(runtime, "complete_xianqiao_trial_challenge", challenge)

    result = _finish(runtime.probe_xianqiao_trial_until_failure())

    assert adjustments == [1, 1, -1]
    assert result["exit_reason"] == "failure_found"
    assert result["remaining_attempts"] == 1
    assert result["sweep_required"] is True
    assert result["rollback_settings"] == {"difficulty_increment": -1}
    assert [trial["mode"] for trial in result["trials"]] == [
        "incremented_from_sweep",
        "incremented_from_sweep",
    ]


def test_trial_probe_treats_all_successful_attempts_as_normal_daily_completion(monkeypatch):
    runtime = _trial_challenge_runtime()
    observations = iter((
        ObservedTrialHomeState(
            attempts=ObservedTrialAttempts(remaining=1, capacity=5, text="奖励次数:1/5"),
            sweep_available=False,
            sweep_score=0.0,
        ),
        ObservedTrialHomeState(
            attempts=ObservedTrialAttempts(remaining=0, capacity=5, text="奖励次数:0/5"),
            sweep_available=True,
            sweep_score=95.0,
        ),
    ))
    attempts_after = iter((ObservedTrialAttempts(remaining=0, capacity=5, text="奖励次数:0/5"),))
    adjustments: list[int] = []

    monkeypatch.setattr(runtime, "current_scene", lambda *_args, **_kwargs: (357, 100.0, "frame"))
    monkeypatch.setattr(runtime, "observe_xianqiao_trial_home", lambda *_args, **_kwargs: next(observations))
    monkeypatch.setattr(runtime, "read_xianqiao_trial_attempts", lambda *_args, **_kwargs: next(attempts_after))

    def adjust(increment, **_kwargs):
        adjustments.append(increment)
        if False:
            yield None
        return {"difficulty_increment": increment}

    def challenge(**_kwargs):
        if False:
            yield None
        return {"result": {"outcome": "success"}, "returned_home": True}

    monkeypatch.setattr(runtime, "adjust_xianqiao_trial_level", adjust)
    monkeypatch.setattr(runtime, "complete_xianqiao_trial_challenge", challenge)

    result = _finish(runtime.probe_xianqiao_trial_until_failure())

    assert adjustments == []
    assert result["exit_reason"] == "attempts_exhausted"
    assert result["sweep_required"] is False
    assert result["trials"][0]["mode"] == "challenge_existing_overlevel"


def test_trial_daily_buys_first_then_uses_ui_driven_progression(monkeypatch):
    runtime = _trial_challenge_runtime()
    events: list[str] = []

    def purchase(target, **_kwargs):
        events.append(f"purchase_{target}")
        if False:
            yield None
        return {"purchased_after": target}

    def progress(**_kwargs):
        events.append("progress")
        if False:
            yield None
        return {"exit_reason": "attempts_exhausted", "sweep_required": False}

    def leave(**_kwargs):
        events.append("leave")
        if False:
            yield None
        return {"terminal_scene": 34}

    monkeypatch.setattr(runtime, "purchase_xianqiao_trial_attempts", purchase)
    monkeypatch.setattr(runtime, "probe_xianqiao_trial_until_failure", progress)
    monkeypatch.setattr(runtime, "leave_xianqiao_trial", leave)

    result = _finish(runtime.run_xianqiao_trial_daily(settle_seconds=0))

    assert events == ["purchase_3", "progress", "leave"]
    assert result["purchase"] == {"purchased_after": 3}
    assert result["progression"]["exit_reason"] == "attempts_exhausted"
    assert result["current_scene"] == 34


def test_trial_daily_sweeps_remaining_attempts_after_failure_then_leaves(monkeypatch):
    runtime = _trial_challenge_runtime()
    events: list[str] = []

    def purchase(*_args, **_kwargs):
        events.append("purchase")
        if False:
            yield None
        return {"purchased_after": 3}

    def progress(**_kwargs):
        events.append("probe")
        if False:
            yield None
        return {"exit_reason": "failure_found", "sweep_required": True, "remaining_attempts": 2}

    def sweep(**_kwargs):
        events.append("sweep")
        if False:
            yield None
        return {"exit_reason": "attempts_exhausted", "remaining_attempts": 0}

    def leave(**_kwargs):
        events.append("leave")
        if False:
            yield None
        return {"terminal_scene": 34}

    monkeypatch.setattr(runtime, "purchase_xianqiao_trial_attempts", purchase)
    monkeypatch.setattr(runtime, "probe_xianqiao_trial_until_failure", progress)
    monkeypatch.setattr(runtime, "sweep_remaining_xianqiao_trial_attempts", sweep)
    monkeypatch.setattr(runtime, "leave_xianqiao_trial", leave)

    result = _finish(runtime.run_xianqiao_trial_daily(settle_seconds=0))

    assert events == ["purchase", "probe", "sweep", "leave"]
    assert result["sweep"]["remaining_attempts"] == 0
    assert result["result"] == "success"


def test_sweep_remaining_trial_attempts_requires_real_count_progress(monkeypatch):
    runtime = _trial_challenge_runtime()
    observations = iter((
        ObservedTrialHomeState(
            attempts=ObservedTrialAttempts(remaining=2, capacity=5, text="奖励次数:2/5"),
            sweep_available=True,
            sweep_score=98.0,
        ),
        ObservedTrialHomeState(
            attempts=ObservedTrialAttempts(remaining=1, capacity=5, text="奖励次数:1/5"),
            sweep_available=True,
            sweep_score=97.0,
        ),
    ))
    after = iter((
        ObservedTrialAttempts(remaining=1, capacity=5, text="奖励次数:1/5"),
        ObservedTrialAttempts(remaining=0, capacity=5, text="奖励次数:0/5"),
    ))

    monkeypatch.setattr(runtime, "observe_xianqiao_trial_home", lambda *_args, **_kwargs: next(observations))
    monkeypatch.setattr(runtime, "read_xianqiao_trial_attempts", lambda *_args, **_kwargs: next(after))

    def sweep_once(**_kwargs):
        if False:
            yield None
        return {"result": {"outcome": "sweep"}, "returned_home": True}

    monkeypatch.setattr(runtime, "complete_xianqiao_trial_challenge", sweep_once)

    result = _finish(runtime.sweep_remaining_xianqiao_trial_attempts(settle_seconds=0))

    assert result["remaining_attempts"] == 0
    assert len(result["sweeps"]) == 2


def test_trial_result_can_resume_directly_from_failure_popup(monkeypatch):
    runtime = _trial_challenge_runtime()
    runtime.ctx["images"].update({
        361: {"id": 361, "title": "成功结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
        362: {"id": 362, "title": "仙窍战斗中", "width": 900, "height": 1600, "shapes": []},
        365: {"id": 365, "title": "失败结算", "width": 900, "height": 1600, "shapes": [{"title": "退出"}]},
    })
    wait_calls = 0

    def wait_view(*_args, **_kwargs):
        nonlocal wait_calls
        wait_calls += 1
        if False:
            yield None
        return runtime.view(365)

    monkeypatch.setattr(runtime, "wait_view", wait_view)
    monkeypatch.setattr(runtime, "cur_frame", lambda **_kwargs: "failure-frame")
    monkeypatch.setattr(runtime, "ocr_text", lambda **_kwargs: "变强途径 退出")

    result = _finish(runtime.wait_xianqiao_trial_result(result_settle_seconds=0))

    assert wait_calls == 1
    assert result["outcome"] == "failure"
