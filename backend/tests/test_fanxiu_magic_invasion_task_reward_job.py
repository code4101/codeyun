from backend.core.fanxiu.data_annotation.tasks.magic_invasion_task_rewards import (
    claim_magic_invasion_task_rewards,
)


class _View:
    def __init__(self, scene_id: int) -> None:
        self.id = scene_id


class _Runtime:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def wait_click_then_view(self, scene, shape, target, **_kwargs):
        self.events.append(("click_then_view", scene, shape, target))
        target_scene = target[0] if isinstance(target, tuple) else target
        yield None
        return _View(target_scene)

    def wait_click(self, scene, shape, **_kwargs):
        self.events.append(("click", scene, shape))
        yield None

    def wait_action_settle(self, seconds):
        self.events.append(("settle", seconds))
        yield None


def _finish(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def _snapshot(authorized, claimed=(), *, expected=None):
    result = {
        "ok": True,
        "available": True,
        "complete": True,
        "authorized_claim_task_ids": list(authorized),
        "claimed_task_ids": list(claimed),
        "task_subtypes": {"11": 1, "12": 1, "21": 2},
    }
    if expected is not None:
        result["expected_task_claimed"] = expected in set(claimed)
    return result


def test_claims_exact_first_rows_across_both_tabs() -> None:
    states = iter(
        (
            _snapshot([11, 12, 21]),
            _snapshot([12, 21], [11], expected=11),
            _snapshot([21], [11, 12], expected=12),
            _snapshot([], [11, 12, 21], expected=21),
        )
    )

    def reader(_activity_id, *, expected_claimed_task_id=None):
        row = next(states)
        if expected_claimed_task_id is not None:
            row["expected_task_claimed"] = (
                expected_claimed_task_id in row["claimed_task_ids"]
            )
        return row

    runtime = _Runtime()
    result = _finish(
        claim_magic_invasion_task_rewards(
            runtime,
            activity_id=1070011,
            reader=reader,
        )
    )

    assert result["claimed_task_ids"] == [11, 12, 21]
    assert [event for event in runtime.events if event[:1] == ("click",)] == [
        ("click", 510, "首条任务领取区"),
        ("click", 510, "首条任务领取区"),
        ("click", 511, "首条任务领取区"),
    ]
    assert ("click_then_view", 510, "修为页签", 511) in runtime.events
    assert ("click_then_view", 511, "活动主页", 509) in runtime.events


def test_no_claimable_task_is_idempotent_without_opening_ui() -> None:
    runtime = _Runtime()
    result = _finish(
        claim_magic_invasion_task_rewards(
            runtime,
            activity_id=8070001,
            reader=lambda _activity_id: _snapshot([]),
        )
    )

    assert result["status"] == "already_settled"
    assert runtime.events == []
