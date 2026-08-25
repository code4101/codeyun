from __future__ import annotations

import threading

from backend.core.fanxiu.behavior_tree.runtime import create_behavior_tree_runtime_runner


def test_mojie_remaining_parser_anchors_value_after_business_label() -> None:
    runner = create_behavior_tree_runtime_runner()

    assert runner._daily_mojie_raid_remaining_ocr_fallback(
        "灵力+1.2兆本周剩余进攻次数：0本周剩余鼓舞次数：15"
    ) == 0


def test_mojie_remaining_parser_supports_ocr_digit_variants() -> None:
    runner = create_behavior_tree_runtime_runner()

    assert runner._daily_mojie_raid_remaining_ocr_fallback("本周剩余进攻次数：８") == 8
    assert runner._daily_mojie_raid_remaining_ocr_fallback("剩余进攻次数: B") == 8
    assert runner._daily_mojie_raid_remaining_ocr_fallback("剩余进攻次数: O") == 0
    assert runner._daily_mojie_raid_remaining_ocr_fallback("本周剩余鼓舞次数：15") is None


def test_mojie_task_prefers_anchored_zero_over_contaminated_first_number(
    tmp_path,
    monkeypatch,
) -> None:
    runner = create_behavior_tree_runtime_runner()
    asset_tree_path = tmp_path / "asset-tree.json"
    asset_tree_path.write_text("[]", encoding="utf-8")
    calls: list[tuple] = []
    contaminated = "灵力+1.2兆本周剩余进攻次数：0本周剩余鼓舞次数：15"

    def done(value=None):
        if False:
            yield None
        return value

    class FakeRuntime:
        def current_scene(self, _preferred, *, update=False):
            return 319, 100.0, "frame319"

        def ocr_text(self, _frame):
            return "奇袭魔界"

        def ocr_numbers_in_shapes(self, scene_id, shape_titles, **kwargs):
            calls.append(("ocr_numbers_in_shapes", scene_id, tuple(shape_titles), kwargs))
            return [1], contaminated

        def wait_action_settle(self, seconds=1.0):
            calls.append(("wait_action_settle", seconds))
            return done(None)

        def wait_click(self, scene_id, shape, **kwargs):
            calls.append(("wait_click", scene_id, shape, kwargs))
            return done(None)

        def wait_click_then_view(self, *_args, **_kwargs):
            raise AssertionError("真实剩余次数为 0 时不得点击「参与进攻」")

    scheduled: list[str] = []
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(
        runner,
        "_schedule_next_mojie_raid_week",
        lambda _payload, *, reason: scheduled.append(reason) or "2026-08-24 10:00:00",
    )

    generator = runner._execute_daily_mojie_raid_task(
        {"asset_tree_path": asset_tree_path},
        threading.Event(),
        {},
    )
    try:
        while True:
            next(generator)
    except StopIteration as stopped:
        result = stopped.value

    assert result == "success"
    assert scheduled == ["连续两帧确认剩余次数为 0，本周已完成"]
    assert calls == [
        ("ocr_numbers_in_shapes", 319, ("剩余次数",), {"padding": 16}),
        ("wait_action_settle", 2.0),
        ("ocr_numbers_in_shapes", 319, ("剩余次数",), {"padding": 16}),
        ("wait_click", 319, "返回", {}),
    ]
