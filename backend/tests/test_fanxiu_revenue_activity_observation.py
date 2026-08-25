from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation.activity_menu import (
    ActivityMenuItem,
    ActivityMenuReadTimings,
    ActivityMenuSnapshot,
)
from backend.core.fanxiu.instrumentation.revenue_activity_observation import (
    build_revenue_activity_observation_snapshot,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
)


def _menu(*items: ActivityMenuItem) -> ActivityMenuSnapshot:
    return ActivityMenuSnapshot(
        kind="world_left",
        status="loaded",
        complete=True,
        items=tuple(items),
        pid=2626,
        process_start_ticks=4748,
        fingerprint="menu-sha256",
        reason="当前活动菜单已完整加载",
        timings=ActivityMenuReadTimings(1, 2, 3, 6, "hot"),
    )


def test_observation_is_exact_visible_intersection_not_schedule() -> None:
    snapshot = build_revenue_activity_observation_snapshot(
        [
            {
                "activity_id": 712,
                "template_id": 909,
                "name": "万宝臻宝",
                "display": 1,
                "icon": "mainui_icon_0775",
            },
            {"activity_id": 666000, "template_id": 1, "name": "寻访仙侣"},
        ],
        _menu(
            ActivityMenuItem(
                index=1,
                key="activity:1102066",
                name="活动1102066",
                activity_id=1102066,
                base_id=610001,
            ),
            ActivityMenuItem(
                index=2,
                key="activity:712",
                name="万宝臻宝",
                activity_id=712,
                base_id=909,
            ),
        ),
        captured_at="2026-08-19T09:53:42+08:00",
        pid=2626,
        process_start_ticks=4748,
    )

    assert snapshot["count"] == 1
    assert snapshot["items"][0]["activity_id"] == 712
    assert snapshot["items"][0]["is_schedule_occurrence"] is False
    assert "schedule_id" not in snapshot["items"][0]
    assert "start_at" not in snapshot["items"][0]
    assert snapshot["evidence"]["revenue_item_count"] == 2


def test_observation_rejects_template_and_menu_identity_conflict() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match="templateId"):
        build_revenue_activity_observation_snapshot(
            [{"activity_id": 712, "template_id": 999, "name": "万宝臻宝"}],
            _menu(
                ActivityMenuItem(
                    index=1,
                    key="activity:712",
                    name="万宝臻宝",
                    activity_id=712,
                    base_id=909,
                )
            ),
            captured_at="2026-08-19T09:53:42+08:00",
            pid=2626,
            process_start_ticks=4748,
        )


def test_observation_rejects_cross_process_join() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match="进程身份不一致"):
        build_revenue_activity_observation_snapshot(
            [{"activity_id": 712, "template_id": 909, "name": "万宝臻宝"}],
            _menu(),
            captured_at="2026-08-19T09:53:42+08:00",
            pid=9999,
            process_start_ticks=4748,
        )
