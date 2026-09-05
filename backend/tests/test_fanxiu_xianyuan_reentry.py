import threading

import pytest

from backend.core.fanxiu.data_annotation import behavior_tree_executor  # noqa: F401
from backend.core.fanxiu.data_annotation.tasks.daily_foundation import (
    DailyFoundationTaskMixin,
)
from backend.core.fanxiu.data_annotation.tasks.xianyuan_reentry import (
    DAILY_XIANYUAN_REENTRY_REQUESTED,
    DAILY_XIANYUAN_SHARED_DEADLINE_KEY,
    daily_xianyuan_reentry_limit,
    daily_xianyuan_shared_deadline,
)


def test_daily_xianyuan_reentry_limit_defaults_to_one_and_is_hard_bounded() -> None:
    assert daily_xianyuan_reentry_limit({}) == 1
    assert daily_xianyuan_reentry_limit({"xianyuan_max_reentries": 0}) == 0
    assert daily_xianyuan_reentry_limit({"xianyuan_max_reentries": 2}) == 2
    assert daily_xianyuan_reentry_limit({"xianyuan_max_reentries": 99}) == 2


def test_daily_xianyuan_reentries_reuse_the_first_attack_deadline() -> None:
    payload = {"attack_dialogue_timeout": 45.0}

    first = daily_xianyuan_shared_deadline(payload, now=100.0)
    second = daily_xianyuan_shared_deadline(payload, now=130.0)

    assert first == 145.0
    assert second == first
    assert payload[DAILY_XIANYUAN_SHARED_DEADLINE_KEY] == first


def test_daily_xianyuan_top_level_stops_after_one_default_reentry() -> None:
    class PureReentryHarness(DailyFoundationTaskMixin):
        def __init__(self) -> None:
            self.attempts = 0

        def _execute_daily_xianyuan_task_once(self, ctx, stop_event, payload):
            self.attempts += 1
            if False:
                yield None
            return DAILY_XIANYUAN_REENTRY_REQUESTED

        def _log(self, *_args) -> None:
            return None

    harness = PureReentryHarness()
    task = harness._execute_daily_xianyuan_task({}, threading.Event(), {})

    with pytest.raises(RuntimeError, match="整单重入次数超过上限 1"):
        list(task)

    assert harness.attempts == 2
