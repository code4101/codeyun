from __future__ import annotations

from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju import (
    play_one_tiandi_yiju_natural_round,
)


def _run(generator):
    try:
        while True:
            next(generator)
    except StopIteration as done:
        return done.value


def _snapshot(*, strength: int, personal: int, switches=None) -> dict:
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "choose_state_loaded": True,
        "strength": strength,
        "max_strength": 100,
        "consume_per_play": 1,
        "personal_score": personal,
        "alliance_score": 1000,
        "resource_spending_choices": switches
        or {
            "multiple_score_item": False,
            "double_reward_item": False,
            "auto_use_strength_item": False,
        },
    }


class _Runtime:
    def __init__(self):
        self.clicks = []

    def wait_click_then_view(self, source, shape, target, **_options):
        self.clicks.append((source, shape, target))
        if False:
            yield None
        return target

    def click_shape_center(self, source, shape):
        self.clicks.append((source, shape, None))

    def wait_action_settle(self, _seconds):
        if False:
            yield None
        return None


def test_single_natural_round_follows_verified_scene_chain() -> None:
    runtime = _Runtime()
    snapshots = iter([
        _snapshot(strength=33, personal=19595),
        _snapshot(strength=33, personal=19595),
        _snapshot(strength=32, personal=19595),
    ])
    result = _run(play_one_tiandi_yiju_natural_round(runtime, reader=lambda: next(snapshots)))

    assert result["status"] == "completed"
    assert result["transition"]["strength_spent"] == 1
    assert result["transition"]["success_terminal_confirmed"] == 1
    assert runtime.clicks == [
        (678, "己方中心棋点候选", 679),
        (679, "对弈", 680),
        (680, "对弈", 681),
        (681, "点击屏幕继续", 680),
        (680, "关闭", 678),
        (678, "离开", 304),
    ]


def test_enabled_resource_switch_is_closed_and_rechecked() -> None:
    runtime = _Runtime()
    enabled = {
        "multiple_score_item": True,
        "double_reward_item": False,
        "auto_use_strength_item": False,
    }
    snapshots = iter([
        _snapshot(strength=5, personal=10),
        _snapshot(strength=5, personal=10, switches=enabled),
        _snapshot(strength=5, personal=10),
        _snapshot(strength=4, personal=10),
    ])
    result = _run(play_one_tiandi_yiju_natural_round(runtime, reader=lambda: next(snapshots)))

    assert result["status"] == "completed"
    assert (680, "四倍棋符开关", None) in runtime.clicks


def test_zero_natural_strength_performs_no_gui_action() -> None:
    runtime = _Runtime()
    result = _run(
        play_one_tiandi_yiju_natural_round(
            runtime,
            reader=lambda: _snapshot(strength=0, personal=10),
        )
    )
    assert result["status"] == "no_natural_strength"
    assert runtime.clicks == []
