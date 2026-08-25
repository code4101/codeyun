from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from backend.core.fanxiu.data_annotation.tasks.daily_task_reward_navigation import (
    DAILY_TASK_REWARD_NAVIGATION_SPECS,
    navigate_to_daily_task_reward_cover,
)


def _drain(generator: Any) -> Any:
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


class _Owner:
    def __init__(self) -> None:
        self.enter_calls: list[str] = []
        self.open_calls: list[dict[str, Any]] = []

    def _enter_daily_from_world_like(self, _ctx, _runtime, _stop_event, _frame, scene_id, _text, *, label):
        self.enter_calls.append(f"{scene_id}:{label}")
        if False:
            yield None
        return 69

    def _open_daily_entry_from_daily(self, _ctx, _stop_event, _payload, **kwargs):
        self.open_calls.append(kwargs)
        if False:
            yield None
        return "open"


class _Runtime:
    def __init__(self, *, initial_scene: int, waited_scenes: list[int] | None = None) -> None:
        self.initial_scene = initial_scene
        self.waited_scenes = list(waited_scenes or [])
        self.clicks: list[tuple[int, str, tuple[int, ...]]] = []

    def current_scene(self, _candidates, *, update=True):
        assert update is True
        return self.initial_scene, 100.0, "frame"

    def ocr_text(self, _frame):
        return ""

    def wait_scene(self, *_scenes, **_kwargs):
        if False:
            yield None
        return SimpleNamespace(id=self.waited_scenes.pop(0))

    def wait_click_then_view(self, scene_id, shape, candidates, **_kwargs):
        self.clicks.append((scene_id, shape, tuple(candidates)))
        if False:
            yield None
        return SimpleNamespace(id=self.waited_scenes.pop(0))

    def wait_click(self, scene_id, shape):
        self.clicks.append((scene_id, shape, ()))
        if False:
            yield None


@pytest.mark.parametrize(
    ("domain", "landing", "blocker_click"),
    [
        ("lundao", [304, 296], (304, "返回")),
        ("qixi_mojie", [330, 319], (330, "确定")),
        ("lingmai", [312, 285], (312, "确认")),
    ],
)
def test_navigation_uses_only_entry_and_cover_blocker_actions(domain, landing, blocker_click):
    owner = _Owner()
    runtime = _Runtime(initial_scene=34, waited_scenes=landing)

    result = _drain(
        navigate_to_daily_task_reward_cover(
            owner,
            {},
            threading.Event(),
            {},
            runtime,
            domain,
        )
    )

    spec = DAILY_TASK_REWARD_NAVIGATION_SPECS[domain]
    assert result == {
        "ok": True,
        "domain": domain,
        "scene_id": spec.target_scene_id,
        "status": "cover_ready",
    }
    assert len(owner.enter_calls) == 1
    assert owner.open_calls[0]["title_pattern"] == spec.title_pattern
    assert runtime.clicks[0][:2] == blocker_click


def test_lundao_kicked_notice_then_seated_cover_is_normalized_without_seat_action():
    owner = _Owner()
    runtime = _Runtime(initial_scene=391, waited_scenes=[304, 296])

    result = _drain(
        navigate_to_daily_task_reward_cover(
            owner, {}, threading.Event(), {}, runtime, "lundao"
        )
    )

    assert result["scene_id"] == 296
    assert owner.enter_calls == []
    assert owner.open_calls == []
    assert [(scene, shape) for scene, shape, _ in runtime.clicks] == [
        (391, "确认"),
        (304, "返回"),
    ]


def test_lundao_night_closed_cover_is_an_equivalent_zero_click_landing():
    owner = _Owner()
    runtime = _Runtime(initial_scene=549)

    result = _drain(
        navigate_to_daily_task_reward_cover(
            owner, {}, threading.Event(), {}, runtime, "lundao"
        )
    )

    assert result["scene_id"] == 549
    assert owner.enter_calls == []
    assert owner.open_calls == []
    assert runtime.clicks == []


@pytest.mark.parametrize(
    ("domain", "target"),
    [("lundao", 296), ("qixi_mojie", 319), ("lingmai", 285)],
)
def test_retry_already_at_target_is_zero_click(domain, target):
    owner = _Owner()
    runtime = _Runtime(initial_scene=target)

    result = _drain(
        navigate_to_daily_task_reward_cover(
            owner, {}, threading.Event(), {}, runtime, domain
        )
    )

    assert result["scene_id"] == target
    assert owner.enter_calls == []
    assert owner.open_calls == []
    assert runtime.clicks == []


def test_unknown_post_entry_landing_fails_closed():
    owner = _Owner()
    runtime = _Runtime(initial_scene=69, waited_scenes=[999])

    with pytest.raises(RuntimeError, match="安全导航未到达 #319"):
        _drain(
            navigate_to_daily_task_reward_cover(
                owner, {}, threading.Event(), {}, runtime, "qixi_mojie"
            )
        )


def test_unknown_domain_rejected_before_actions():
    with pytest.raises(ValueError, match="未知日常任务奖励域"):
        _drain(
            navigate_to_daily_task_reward_cover(
                _Owner(), {}, threading.Event(), {}, _Runtime(initial_scene=34), "other"
            )
        )
