from __future__ import annotations

import threading
from datetime import datetime
from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks.prayer_daily_resource import (
    PrayerDailyResourceTaskMixin,
    _PrayerStabilityWindow,
    _prayer_store_fragments,
    _prayer_task_fragments,
    exact_ocr_fragment,
    fragment_center,
    prayer_entry_action_point,
    prayer_entry_action_points,
    prayer_enter_fragment,
    prayer_entry_fragment,
    prayer_page_state,
    prayer_reward_overlay_dismissed,
    prayer_store_tab_fragment,
    prayer_store_state,
    prayer_task_one_key_fragment,
    prayer_new_round_confirm_visible,
    prayer_task_state,
    prayer_task_tab_fragment,
)


def _fragment(text: str, x: float, y: float, w: float = 40, h: float = 30):
    return {"text": text, "x": x, "y": y, "w": w, "h": h}


def test_prayer_daily_resource_is_one_daily_standard_job() -> None:
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("prayer_daily_resource")
    assert definition is not None
    assert definition.scheduler_supported is True
    assert not hasattr(definition, "lifecycle")

    tasks = [
        item
        for item in default_data_annotation_scheduler_tasks(datetime(2026, 8, 7, 1, 0, 0))
        if item["task_type"] == "prayer_daily_resource"
    ]
    assert len(tasks) == 1
    assert tasks[0]["id"] == "prayer-daily-resource"
    assert tasks[0]["label"] == "祈愿_每日资源"
    assert tasks[0]["trigger_description"] == "每日"
    assert tasks[0]["next_time"] == "2026-08-08 00:00:00"
    assert tasks[0]["error_retry_delay_seconds"] == 600


def test_prayer_entry_uses_unique_left_menu_suffix_not_weekly_prefix() -> None:
    assert prayer_entry_fragment(
        [
            _fragment("场景内寻得", 550, 410),
            _fragment("锻体祈愿", 140, 410, 125, 34),
            _fragment("祈愿任务", 550, 1350, 45, 165),
        ]
    )["text"] == "锻体祈愿"

    assert prayer_entry_fragment(
        [
            _fragment("淬体祈愿", 140, 410),
            _fragment("炼丹祈愿", 160, 500),
        ]
    ) is None

    low_entry = _fragment("锻体祈愿", 6, 1034, 126, 34)
    assert prayer_entry_fragment([low_entry]) is low_entry
    assert fragment_center(low_entry) == (69.0, 1051.0)
    assert prayer_entry_action_point(low_entry) == (69.0, 976.2)
    assert prayer_entry_action_points(low_entry) == (
        (69.0, 976.2),
        (106.8, 976.2),
        (69.0, 1051.0),
    )


def test_prayer_enter_action_is_unique_and_limited_to_left_activity_menu() -> None:
    enter = _fragment("进入", 120, 633, 64, 29)
    assert prayer_enter_fragment([enter]) is enter
    assert prayer_enter_fragment([_fragment("进入", 650, 633)]) is None
    assert prayer_enter_fragment([enter, _fragment("进入", 140, 720)]) is None


def test_exact_free_action_must_be_unique_and_not_match_gift_title() -> None:
    fragments = [
        _fragment("免费祈愿礼包", 57, 683, 221, 38),
        _fragment("免费", 121, 1146, 92, 55),
        _fragment("488", 477, 1159),
    ]
    assert exact_ocr_fragment(fragments, "免费")["x"] == 121
    assert exact_ocr_fragment(fragments + [_fragment("免费", 500, 1100)], "免费") is None


def test_store_state_accepts_removed_free_card_only_after_store_is_loaded() -> None:
    claimable = [
        _fragment("免费祈愿礼包", 57, 683, 221, 38),
        _fragment("免费", 121, 1146, 92, 55),
        _fragment("祈愿商店", 778, 1351, 43, 160),
    ]
    assert prayer_store_state(claimable, "免费祈愿礼包 每日限购：1") == "claimable"

    claimed = [
        _fragment("祈愿灵石礼包", 57, 683, 221, 38),
        _fragment("祈愿商店", 778, 1351, 43, 160),
    ]
    assert prayer_store_state(claimed, "祈愿灵石礼包 每日限购：5 适度娱乐，理性消费") == "claimed"
    assert prayer_store_state([], "每日限购：5") == "loading"
    assert prayer_store_state(
        [_fragment("每日限购：0", 158, 730), _fragment("售罄", 132, 1160)],
        "免费祈愿礼包 每日限购：0 售罄",
        store_context_confirmed=True,
    ) == "claimed"
    assert prayer_store_state([], "", store_context_confirmed=True) == "loading"


def test_reward_receipt_dismissal_survives_click_through_to_another_prayer_tab() -> None:
    unrelated_tab_text = (
        "天魂 特殊效果：洗灵仙谕38星 升级条件：灵祖铸魂至386/390段 前往铸魂"
    )
    assert prayer_reward_overlay_dismissed(
        unrelated_tab_text,
        reward_seen=True,
    ) is True
    assert prayer_reward_overlay_dismissed(
        "恭喜获得 点击屏幕继续",
        reward_seen=True,
    ) is False
    assert prayer_reward_overlay_dismissed(
        unrelated_tab_text,
        reward_seen=False,
    ) is False


def test_prayer_page_state_distinguishes_main_store_and_unknown() -> None:
    main = [
        _fragment("祈愿任务", 778, 1090, 43, 160),
        _fragment("祈愿商店", 778, 1351, 43, 160),
    ]
    store = [
        _fragment("免费", 121, 1146, 92, 55),
        _fragment("祈愿商店", 778, 1351, 43, 160),
    ]
    assert prayer_page_state(None, main, "") == "main"
    assert prayer_page_state(455, main, "") == "main"
    assert prayer_page_state(456, store, "免费祈愿礼包") == "store"
    assert prayer_page_state(449, [_fragment("祈愿商店", 778, 1351)], "") == "unknown"
    assert prayer_page_state(None, [_fragment("神焰炼化", 400, 500)], "神焰炼化") == "unknown"


def test_prayer_store_tab_requires_full_text_in_lower_right() -> None:
    fragments = [
        _fragment("祈愿", 77, 585, 66, 30),
        _fragment("祈愿任务", 546, 1358, 46, 166),
        _fragment("祈愿商店", 781, 1359, 43, 160),
    ]
    assert prayer_store_tab_fragment(fragments)["x"] == 781
    assert prayer_store_tab_fragment([_fragment("祈愿商店", 77, 585, 120, 30)]) is None


def test_prayer_task_one_key_accepts_animated_missing_first_glyph_only_in_task_area() -> None:
    fragments = [
        _fragment("祈愿任务", 543, 1346, 43, 166),
        _fragment("领取", 684, 939, 80, 42),
        _fragment("键领取", 408, 1246, 117, 40),
    ]
    assert prayer_task_tab_fragment(fragments)["x"] == 543
    assert prayer_task_one_key_fragment(fragments)["x"] == 408
    assert prayer_task_one_key_fragment([_fragment("键领取", 50, 400)]) is None
    assert prayer_task_one_key_fragment(
        fragments + [_fragment("一键领取", 420, 1200)]
    ) is None


def test_prayer_task_state_requires_a_real_claimable_row() -> None:
    common = [
        _fragment("祈愿任务", 543, 1346, 43, 166),
        _fragment("键领取", 408, 1246, 117, 40),
    ]
    assert prayer_task_state(common + [_fragment("领取", 684, 939, 80, 42)]) == "claimable"
    assert prayer_task_state(common + [_fragment("获取积分", 662, 933, 153, 41)]) == "settled"
    assert prayer_task_state([_fragment("祈愿任务", 543, 1346, 43, 166)]) == "loading"
    assert prayer_task_state(common + [_fragment("领取", 120, 500)]) == "settled"
    assert prayer_task_state(
        [_fragment("键领取", 408, 1246, 117, 40), _fragment("领取", 684, 939, 80, 42)],
        task_context_confirmed=True,
    ) == "claimable"


def test_prayer_new_round_confirmation_requires_statement_and_unique_action() -> None:
    prompt = [
        _fragment("已领取完本轮次奖励，开启新一轮奖励", 195, 703, 557, 31),
        _fragment("任务", 436, 744, 74, 37),
        _fragment("确认", 419, 1038, 84, 45),
    ]
    assert prayer_new_round_confirm_visible(prompt) is True
    assert prayer_new_round_confirm_visible([
        _fragment("取元本轮次关励，开后新一轮关励任务", 195, 703, 557, 31),
        _fragment("确认", 419, 1038, 84, 45),
    ]) is True
    assert prayer_new_round_confirm_visible(prompt[:-1]) is False
    assert prayer_new_round_confirm_visible([_fragment("确认", 419, 1038, 84, 45)]) is False


def test_prayer_task_fragments_are_bound_to_455_business_shapes() -> None:
    class Runtime:
        def ocr_fragments_in_shapes(self, scene, shapes, **kwargs):
            assert scene == 455
            assert shapes == ("任务奖励", "一键领取")
            assert kwargs == {"padding": 0, "frame_data_url": "task-frame", "crop": True}
            return [_fragment("领取", 684, 939)]

    assert _prayer_task_fragments(Runtime(), "task-frame")[0]["text"] == "领取"


def test_prayer_store_fragments_are_bound_to_456_business_shapes() -> None:
    class Runtime:
        def ocr_fragments_in_shapes(self, scene, shapes, **kwargs):
            assert scene == 456
            assert shapes == ("免费祈愿礼包", "免费", "商品列表")
            assert kwargs == {"padding": 0, "frame_data_url": "store-frame", "crop": True}
            return [_fragment("免费", 121, 1146)]

    assert _prayer_store_fragments(Runtime(), "store-frame")[0]["text"] == "免费"


def test_stability_window_completes_two_slow_samples_before_deadline(monkeypatch) -> None:
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.prayer_daily_resource.time.monotonic",
        lambda: clock.now,
    )
    tracker = _PrayerStabilityWindow(12.0)

    assert tracker.should_sample() is True
    clock.now = 12.5
    tracker.observe("settled")
    assert tracker.should_sample() is True
    clock.now = 25.0
    tracker.observe("settled")

    assert tracker.sample_count == 2
    assert tracker.stable is True
    assert tracker.stable_state == "settled"


def test_target_stability_ignores_first_stale_claimable_frame(monkeypatch) -> None:
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.prayer_daily_resource.time.monotonic",
        lambda: clock.now,
    )
    tracker = _PrayerStabilityWindow(12.0, accepted_states=frozenset({"claimed"}))

    tracker.observe("claimable")
    assert tracker.stable_count == 0
    tracker.observe("claimed")
    assert tracker.stable_count == 1
    tracker.observe("claimed")

    assert tracker.stable is True
    assert tracker.sample_count == 3


def test_task_claim_wait_records_state_text_timing_count_and_scene(monkeypatch) -> None:
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.prayer_daily_resource.time.monotonic",
        lambda: clock.now,
    )

    claimable = [
        _fragment("键领取", 408, 1246, 117, 40),
        _fragment("领取", 684, 939, 80, 42),
    ]
    settled = [
        _fragment("键领取", 408, 1246, 117, 40),
        _fragment("获取积分", 662, 933, 153, 41),
    ]

    class Runtime:
        def __init__(self) -> None:
            self.samples = [claimable, claimable, claimable, settled, settled]
            self.clicked: list[tuple[int, float, float]] = []

        def click_frame_point(self, scene, x, y):
            self.clicked.append((scene, x, y))

        def cur_frame(self, *, update):
            assert update is True
            clock.now += 12.5
            return f"frame-{len(self.samples)}"

        def ocr_fragments_in_shapes(self, scene, shapes, **kwargs):
            return self.samples.pop(0)

        def wait_action_settle(self, _seconds):
            if False:
                yield None

    class Runner(PrayerDailyResourceTaskMixin):
        def __init__(self) -> None:
            self.logs: list[tuple[str, str]] = []

        def _raise_if_stopped(self, stop_event):
            assert stop_event.is_set() is False

        def _log(self, kind, message):
            self.logs.append((kind, message))

    runner = Runner()
    runtime = Runtime()
    main_fragments = [
        _fragment("祈愿任务", 543, 1346, 43, 166),
        _fragment("祈愿商店", 781, 1359, 43, 160),
    ]
    operation = runner._claim_prayer_task_rewards(
        runtime,
        threading.Event(),
        main_fragments,
        timeout_seconds=12.0,
    )

    try:
        while True:
            next(operation)
    except StopIteration as exc:
        result = exc.value

    assert result == "claimed"
    assert len(runtime.clicked) == 2
    assert runtime.samples == []
    assert any(
        "phase=task_claim_verify" in message
        and "scene=#455" in message
        and "state=claimable" in message
        and "text=" in message
        and "timing=" in message
        and "stable_count=0" in message
        for _kind, message in runner.logs
    )
    assert runner.logs[-1][1].endswith("stable_count=2 sample=3")


def test_task_claim_repeats_only_after_a_new_claimable_batch(monkeypatch) -> None:
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.prayer_daily_resource.time.monotonic",
        lambda: clock.now,
    )

    batch_a = [
        _fragment("累计获得1000洗灵积分", 200, 800),
        _fragment("键领取", 408, 1246, 117, 40),
        _fragment("领取", 684, 939, 80, 42),
    ]
    batch_b = [
        _fragment("累计获得18000洗灵积分", 200, 800),
        _fragment("键领取", 408, 1246, 117, 40),
        _fragment("领取", 684, 939, 80, 42),
    ]
    settled = [
        _fragment("键领取", 408, 1246, 117, 40),
        _fragment("获取积分", 662, 933, 153, 41),
    ]

    class Runtime:
        def __init__(self) -> None:
            self.samples = [
                batch_a,
                batch_a,
                batch_a,  # 点击后的旧帧，不得再次点击。
                [],
                batch_b,
                batch_b,
                [],
                settled,
                settled,
            ]
            self.clicked: list[tuple[int, float, float]] = []

        def click_frame_point(self, scene, x, y):
            self.clicked.append((scene, x, y))

        def cur_frame(self, *, update):
            assert update is True
            clock.now += 1.0
            return f"frame-{len(self.samples)}"

        def ocr_fragments_in_shapes(self, scene, shapes, **kwargs):
            return self.samples.pop(0)

        def wait_action_settle(self, _seconds):
            if False:
                yield None

    class Runner(PrayerDailyResourceTaskMixin):
        def _raise_if_stopped(self, stop_event):
            assert stop_event.is_set() is False

        def _log(self, _kind, _message):
            pass

    runtime = Runtime()
    operation = Runner()._claim_prayer_task_rewards(
        runtime,
        threading.Event(),
        [_fragment("祈愿任务", 543, 1346, 43, 166)],
        timeout_seconds=12.0,
    )
    try:
        while True:
            next(operation)
    except StopIteration as exc:
        result = exc.value

    assert result == "claimed"
    assert len(runtime.clicked) == 3  # 页签 + 两个确有进展的领取批次。
    assert runtime.samples == []
