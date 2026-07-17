from __future__ import annotations

import threading

from backend.core.fanxiu.data_annotation.trial_difficulty import (
    ObservedTrialDifficulty,
    build_even_trial_difficulty_plan,
    find_current_trial_difficulty,
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
        )
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
