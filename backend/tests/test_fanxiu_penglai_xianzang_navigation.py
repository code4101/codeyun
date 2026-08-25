from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation import (
    XianzangActivityUnavailable,
    enter_xianzang,
    open_xianzang_tab,
    read_xianzang_page,
)


class _FakeRuntime:
    def __init__(self, page: str):
        self.page = page
        self.clicks: list[tuple] = []

    def _observation(self):
        observations = {
            "world": (34, 100.0, "VIP 蓬莱仙藏 限时活动"),
            "main": (None, 85.9, "蓬莱仙藏 规则 活动时间 自选 累计 鉴宝十次 任务 商店"),
            "task": (450, 100.0, "蓬莱仙藏 炼宝试炼一 任务 商店"),
            "store": (449, 100.0, "蓬莱仙藏 礼包商店 灵石礼包一 任务 商店"),
            "optional": (448, 100.0, "蓬莱仙藏 自选奖励 珍宝奖励 确认"),
        }
        return observations[self.page]

    def current_scene(self, scene_ids, *, update: bool):
        assert update is True
        scene_id, score, text = self._observation()
        if scene_id not in scene_ids:
            return None, 0.0, text
        return scene_id, score, text

    def ocr_text(self, frame):
        return frame

    def cur_frame(self, *, update: bool):
        assert update is True
        return self._observation()[2]

    def click_ocr_text(self, scene_id, target, **kwargs):
        self.clicks.append(("ocr", scene_id, target, kwargs))
        self.page = "main"

    def click_shape(self, scene_id, title, **kwargs):
        self.clicks.append(("shape", scene_id, title, kwargs))
        self.page = {
            "蓬莱仙藏": "main",
            "pengla蓬莱仙藏": "main",
            "任务": "task",
            "商店": "store",
            "自选": "optional",
            "返回": "world",
        }[title]


def test_enter_xianzang_from_world_clicks_business_text_and_verifies_main(monkeypatch):
    runtime = _FakeRuntime("world")
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation.time.sleep",
        lambda _seconds: None,
    )

    result = enter_xianzang(runtime)

    assert result.page == "蓬莱仙藏"
    assert runtime.clicks[0][:3] == ("ocr", 34, "蓬莱仙藏")


def test_enter_xianzang_is_idempotent_on_main_page():
    runtime = _FakeRuntime("main")

    result = enter_xianzang(runtime)

    assert result.page == "蓬莱仙藏"
    assert runtime.clicks == []


def test_open_task_tab_from_store_uses_shared_tab_shape(monkeypatch):
    runtime = _FakeRuntime("store")
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation.time.sleep",
        lambda _seconds: None,
    )

    result = open_xianzang_tab(runtime, "任务")

    assert result.page == "任务"
    assert result.scene_id == 450
    assert runtime.clicks[0][:3] == ("shape", 447, "任务")


def test_open_task_tab_retries_only_while_fresh_page_stays_on_main(monkeypatch):
    class IgnoreFirstTaskClickRuntime(_FakeRuntime):
        def click_shape(self, scene_id, title, **kwargs):
            self.clicks.append(("shape", scene_id, title, kwargs))
            if title == "任务" and sum(
                click[2] == "任务" for click in self.clicks
            ) == 1:
                return
            self.page = "task"

    runtime = IgnoreFirstTaskClickRuntime("main")
    ticks = iter((0.0, 0.0, 0.0, 1.1, 1.1, 1.2))
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation.time.monotonic",
        lambda: next(ticks),
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation.time.sleep",
        lambda _seconds: None,
    )

    result = open_xianzang_tab(runtime, "任务")

    assert result.page == "任务"
    assert [click[2] for click in runtime.clicks] == ["任务", "任务"]


def test_each_tab_retry_gets_a_complete_post_click_observation_window(monkeypatch):
    class SlowRecognitionRuntime(_FakeRuntime):
        def click_shape(self, scene_id, title, **kwargs):
            self.clicks.append(("shape", scene_id, title, kwargs))
            if title == "任务" and len(self.clicks) >= 2:
                self.page = "task"

    runtime = SlowRecognitionRuntime("main")
    # The first fresh recognition returns only after the original 8-second
    # deadline.  The second click must still receive its own settling window.
    ticks = iter((0.0, 0.0, 0.0, 9.0, 9.0, 9.0, 10.0))
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation.time.monotonic",
        lambda: next(ticks),
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation.time.sleep",
        lambda _seconds: None,
    )

    result = open_xianzang_tab(runtime, "任务", timeout_seconds=8.0)

    assert result.page == "任务"
    assert [click[2] for click in runtime.clicks] == ["任务", "任务"]


def test_open_current_tab_is_idempotent():
    runtime = _FakeRuntime("task")

    result = open_xianzang_tab(runtime, "任务")

    assert result.page == "任务"
    assert runtime.clicks == []


def test_open_main_from_task_uses_disambiguated_task_tab(monkeypatch):
    runtime = _FakeRuntime("task")
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation.time.sleep",
        lambda _seconds: None,
    )

    result = open_xianzang_tab(runtime, "蓬莱仙藏")

    assert result.page == "蓬莱仙藏"
    assert runtime.clicks[0][:3] == ("shape", 450, "pengla蓬莱仙藏")


def test_unknown_tab_is_rejected_without_clicking():
    runtime = _FakeRuntime("main")

    with pytest.raises(ValueError, match="尚未实现"):
        open_xianzang_tab(runtime, "兑换宝阁")  # type: ignore[arg-type]

    assert runtime.clicks == []


def test_read_page_returns_none_outside_xianzang():
    assert read_xianzang_page(_FakeRuntime("world")) is None


def test_task_content_wins_when_shared_title_tie_is_reported_as_447():
    class SharedTitleTieRuntime(_FakeRuntime):
        def current_scene(self, scene_ids, *, update: bool):
            assert scene_ids == [447, 448, 449, 450]
            assert update is True
            return (
                447,
                100.0,
                "蓬莱仙藏福炼宝试炼四已完成兑换宝阁蓬莱仙藏任务商店",
            )

    result = read_xianzang_page(SharedTitleTieRuntime("task"))

    assert result is not None
    assert result.page == "任务"
    assert result.scene_id == 450
    assert result.score == 100.0


@pytest.mark.parametrize(
    ("text", "page", "scene_id"),
    [
        ("蓬莱仙藏自选奖励珍宝奖励确认", "自选", 448),
        ("蓬莱仙藏礼包商店灵石礼包一任务商店", "商店", 449),
    ],
)
def test_other_specific_pages_also_win_a_shared_447_title_tie(text, page, scene_id):
    class SharedTitleTieRuntime(_FakeRuntime):
        def current_scene(self, scene_ids, *, update: bool):
            assert update is True
            return 447, 100.0, text

    result = read_xianzang_page(SharedTitleTieRuntime("main"))

    assert result is not None
    assert result.page == page
    assert result.scene_id == scene_id


def test_enter_xianzang_marks_stable_missing_menu_as_weekly_unavailable(monkeypatch):
    runtime = _FakeRuntime("world")
    runtime._observation = lambda: (34, 100.0, "VIP 限时活动")
    ticks = iter((0.0, 0.0, 61.0))
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation.time.monotonic",
        lambda: next(ticks),
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation.time.sleep",
        lambda _seconds: None,
    )

    with pytest.raises(XianzangActivityUnavailable, match="连续 60 秒"):
        enter_xianzang(runtime, availability_timeout_seconds=60)

    assert runtime.clicks == []


def test_leave_xianzang_uses_explicit_return_and_verifies_world(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation import (
        leave_xianzang,
    )

    runtime = _FakeRuntime("main")
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation.time.sleep",
        lambda _seconds: None,
    )

    assert leave_xianzang(runtime) == (34, 100.0)
    assert runtime.clicks[0][:3] == ("shape", 447, "返回")
