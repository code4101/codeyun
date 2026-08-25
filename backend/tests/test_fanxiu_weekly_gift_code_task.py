from __future__ import annotations

import threading
from datetime import datetime

import pytest

import backend.core.fanxiu.data_annotation.behavior_tree_runtime as behavior_tree_runtime
import backend.core.fanxiu.data_annotation.tasks.gift_code as gift_code
from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
from backend.core.fanxiu.data_annotation.runner import create_behavior_tree_runtime_runner
from backend.core.fanxiu.data_annotation.scheduler_defaults import default_data_annotation_scheduler_tasks
from backend.core.fanxiu.data_annotation.tasks.gift_code import GiftCodeTaskMixin
from backend.core.fanxiu.data_annotation.tasks.misc_actions import MiscActionTaskMixin


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


class _Runner(GiftCodeTaskMixin):
    def __init__(self) -> None:
        self.executed_codes: list[list[str]] = []
        self.next_times: list[tuple[str, str]] = []
        self.logs: list[tuple[str, str]] = []
        self.fetched_codes: list[str] = []

    def _execute_gift_code_task(
        self,
        _ctx,
        codes,
        _stop_event,
        *,
        on_codes_processed=None,
    ) -> dict:
        self.executed_codes.append(list(codes))
        if on_codes_processed is not None:
            on_codes_processed()
        return {"current_scene": 34}

    def _persist_scheduler_task_next_time(self, task_id, next_time) -> None:
        self.next_times.append((task_id, next_time))

    def _log(self, kind, message) -> None:
        self.logs.append((kind, message))

    def _fetch_weekly_gift_codes(self, _stop_event) -> list[str]:
        return list(self.fetched_codes)


def test_next_weekly_gift_code_trigger_is_always_next_monday_at_2330() -> None:
    assert gift_code.next_weekly_gift_code_trigger_at(
        datetime(2026, 8, 3, 23, 29),
    ) == datetime(2026, 8, 10, 23, 30)
    assert gift_code.next_weekly_gift_code_trigger_at(
        datetime(2026, 8, 5, 12, 0),
    ) == datetime(2026, 8, 10, 23, 30)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("请输入正确的兑换码\n点击输入兑换码\n兑换", True),
        ("请 输 入 正 确 的 兑 换 码\n点击输入兑换码\n兑换", True),
        ("设置\n兑换礼包\n游戏公告", False),
    ],
)
def test_gift_page_text_ready_uses_window_specific_copy(text, expected) -> None:
    assert GiftCodeTaskMixin._gift_page_text_ready(text) is expected


def test_gift_input_confirm_point_requires_unique_bottom_right_confirm() -> None:
    fragments = [
        {"text": "确认", "x": 400, "y": 700, "w": 80, "h": 40},
        {"text": "确定", "x": 825, "y": 1510, "w": 60, "h": 50},
    ]

    assert GiftCodeTaskMixin._gift_input_confirm_point(
        fragments,
        frame_width=900,
        frame_height=1600,
    ) == (855.0, 1535.0)

    with pytest.raises(RuntimeError, match="匹配到 0 项"):
        GiftCodeTaskMixin._gift_input_confirm_point(
            [{"text": "确定", "x": 100, "y": 100, "w": 60, "h": 50}],
            frame_width=900,
            frame_height=1600,
        )


def test_clear_and_type_reads_confirm_only_from_dedicated_shape(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    image = {
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "输入兑换码", "x": 0.16, "y": 0.4, "w": 0.4, "h": 0.05},
            {"title": "输入确定", "x": 0.91, "y": 0.94, "w": 0.08, "h": 0.04},
        ],
    }
    events: list[tuple] = []

    monkeypatch.setattr(behavior_tree_runtime.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner, "_image", lambda _ctx, key: image if key == "gift" else None)
    monkeypatch.setattr(runner, "_click_shape", lambda *_args: events.append(("input",)))
    monkeypatch.setattr(runner, "_keyevents", lambda _ctx, keys: events.append(("keys", tuple(keys))))
    monkeypatch.setattr(runner, "_text", lambda _ctx, text: events.append(("text", text)))
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "input-overlay-frame")
    fragment_calls = 0

    def fragments(frame, observed_image, titles, **kwargs):
        nonlocal fragment_calls
        fragment_calls += 1
        assert frame == "input-overlay-frame"
        assert observed_image is image
        assert titles == ["输入确定"]
        assert kwargs == {"padding": 8, "ctx": {}}
        if fragment_calls == 1:
            return []
        return [{"text": "确定", "x": 824, "y": 1518, "w": 58, "h": 39}]

    monkeypatch.setattr(runner, "_ocr_fragments_in_shapes", fragments)
    monkeypatch.setattr(runner, "_clear_tick_frame", lambda _ctx: events.append(("refresh",)))
    monkeypatch.setattr(
        runner,
        "_click_frame_point",
        lambda _ctx, _image, x, y: events.append(("confirm", x, y)),
    )

    runner._clear_and_type({}, "蝉息风清", threading.Event())

    assert fragment_calls == 2
    assert ("refresh",) in events
    assert events.index(("text", "蝉息风清")) < events.index(("confirm", 853.0, 1537.5))
    assert events[-1] == ("confirm", 853.0, 1537.5)


def test_open_gift_retries_only_while_formal_settings_scene_remains(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    image = {
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "兑换礼包", "x": 0.14, "y": 0.33, "w": 0.14, "h": 0.03},
        ],
    }
    frames = iter(["settings-0", "settings-1", "settings-2", "settings-3", "settings-4", "gift"])
    clicks: list[tuple[float, float]] = []

    monkeypatch.setattr(behavior_tree_runtime.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner, "_image", lambda _ctx, key: image if key == "settings" else None)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(runner, "_is_gift_page_ready", lambda _ctx, frame: frame == "gift")
    monkeypatch.setattr(runner, "_identify_scene", lambda _ctx, _frame, _keys: ("settings", 100.0))
    monkeypatch.setattr(runner, "_clear_tick_frame", lambda _ctx: None)
    monkeypatch.setattr(
        runner,
        "_click_frame_point",
        lambda _ctx, _image, x, y: clicks.append((x, y)),
    )
    monkeypatch.setattr(runner, "_log", lambda *_args: None)

    runner._open_gift({}, threading.Event())

    assert len(clicks) == 2
    assert all(point == pytest.approx((189.0, 496.0)) for point in clicks)


def test_weekly_gift_code_success_advances_business_next_time(monkeypatch) -> None:
    monkeypatch.setattr(gift_code, "_now", lambda: datetime(2026, 8, 3, 23, 40))
    runner = _Runner()

    result = _drain(
        runner._execute_weekly_gift_code_task(
            {},
            object(),
            {
                "__scheduler_task_id": "gift-code-instance",
                "codes": ["秋风初至", "晚风知秋", "秋风初至", ""],
            },
        ),
    )

    assert result["result"] == "success"
    assert result["code_count"] == 2
    assert "next_time" not in result
    assert runner.executed_codes == [["秋风初至", "晚风知秋"]]
    assert runner.next_times == [("gift-code-instance", "2026-08-10 23:30:00")]


def test_weekly_gift_code_uses_crawler_when_payload_codes_are_empty() -> None:
    runner = _Runner()
    runner.fetched_codes = ["秋风初至", "晚风知秋"]

    result = _drain(runner._execute_weekly_gift_code_task({}, object(), {}))

    assert result["code_count"] == 2
    assert runner.executed_codes == [["秋风初至", "晚风知秋"]]
    assert runner.next_times


def test_weekly_gift_code_empty_crawler_result_fails_closed() -> None:
    runner = _Runner()

    with pytest.raises(RuntimeError, match="论坛未返回任何兑换码"):
        _drain(runner._execute_weekly_gift_code_task({}, object(), {}))

    assert runner.executed_codes == []
    assert runner.next_times == []


def test_weekly_gift_code_is_one_standard_monday_job() -> None:
    register_fanxiu_data_annotation_default_runtime_jobs()
    now = datetime(2026, 8, 5, 12, 0)
    jobs = [
        item
        for item in default_data_annotation_scheduler_tasks(now=now)
        if item["task_type"] == "weekly_gift_code"
    ]
    definition = get_fanxiu_data_annotation_task_cell_definition("weekly_gift_code")

    assert len(jobs) == 1
    assert jobs[0]["id"] == "gift-code-weekly"
    assert jobs[0]["label"] == "每周_礼包码"
    assert jobs[0]["trigger_description"] == "每周"
    assert jobs[0]["next_time"] == "2026-08-10 23:30:00"
    assert jobs[0]["payload"] == {}
    assert definition is not None
    assert definition.scheduler_supported is True
    assert not hasattr(definition, "lifecycle")


class _SettingsRuntime:
    def __init__(self, scene_id, *, first_landing=None) -> None:
        self.scene_id = scene_id
        self.first_landing = first_landing
        self.actions: list[tuple] = []

    def current_scene(self, scene_ids, *, update=False):
        assert scene_ids == [34, 35, 49]
        assert update is True
        return self.scene_id, 100.0 if self.scene_id is not None else 0.0, "frame"

    def goto_view(self, scene_id):
        self.actions.append(("goto_view", scene_id))
        yield "running"

    def click_shape_center_then_view(self, scene_id, shape, *targets, **kwargs):
        target_value = targets[0] if len(targets) == 1 else targets
        self.actions.append(("click", scene_id, shape, target_value, kwargs.get("label")))
        yield "running"
        return self.first_landing if self.first_landing is not None else targets[0]


class _SettingsRunner(MiscActionTaskMixin):
    def __init__(self) -> None:
        self.logs: list[tuple[str, str]] = []

    def _log(self, kind, message) -> None:
        self.logs.append((kind, message))


@pytest.mark.parametrize(
    ("scene_id", "expected_actions"),
    [
        (49, []),
        (35, [("click", 35, "设置", 49, "打开设置页：等待设置页 #49")]),
        (
            34,
            [
                ("click", 34, "打开下方菜单", 35, "打开设置页：等待下方菜单 #35"),
                ("click", 35, "设置", 49, "打开设置页：等待设置页 #49"),
            ],
        ),
        (
            None,
            [
                ("goto_view", 34),
                ("click", 34, "打开下方菜单", 35, "打开设置页：等待下方菜单 #35"),
                ("click", 35, "设置", 49, "打开设置页：等待设置页 #49"),
            ],
        ),
    ],
)
def test_open_settings_page_component_uses_shortest_current_path(scene_id, expected_actions) -> None:
    runtime = _SettingsRuntime(scene_id)
    runner = _SettingsRunner()

    result = _drain(runner._open_settings_page(runtime))

    assert result == 49
    assert runtime.actions == expected_actions
    assert runner.logs[-1][0] == "success"


@pytest.mark.parametrize(
    ("scene_id", "first_landing", "expected_actions"),
    [
        (
            49,
            35,
            [
                ("click", 49, "回退", (34, 35), "离开设置页：等待世界 #34 或下方菜单 #35"),
                ("click", 35, "关闭下方菜单", 34, "离开设置页：等待世界 #34"),
            ],
        ),
        (35, None, [("click", 35, "关闭下方菜单", 34, "离开设置页：等待世界 #34")]),
        (34, None, []),
    ],
)
def test_leave_settings_page_uses_annotated_shape_chain(scene_id, first_landing, expected_actions) -> None:
    runtime = _SettingsRuntime(scene_id, first_landing=first_landing)
    runner = _SettingsRunner()

    result = _drain(runner._leave_settings_page(runtime))

    assert result == 34
    assert runtime.actions == expected_actions
    assert runner.logs[-1][0] == "success"


def test_leave_settings_page_accepts_direct_49_to_34_landing() -> None:
    runtime = _SettingsRuntime(49, first_landing=34)
    runner = _SettingsRunner()

    result = _drain(runner._leave_settings_page(runtime))

    assert result == 34
    assert runtime.actions == [
        ("click", 49, "回退", (34, 35), "离开设置页：等待世界 #34 或下方菜单 #35"),
    ]
    assert runner.logs[-1] == ("success", "离开设置页：#49 回退已直接到达 #34")


def test_leave_settings_page_fails_closed_from_unknown_scene() -> None:
    runtime = _SettingsRuntime(None)
    runner = _SettingsRunner()

    with pytest.raises(RuntimeError, match="需要 fresh #49/#35/#34"):
        _drain(runner._leave_settings_page(runtime))

    assert runtime.actions == []


class _RedeemRuntime:
    def __init__(
        self,
        *,
        scene_id: int = 49,
        fail_departure: bool = False,
        events: list[str] | None = None,
    ) -> None:
        self.scene_id = scene_id
        self.actions: list[tuple[int, str, int]] = []
        self.fail_departure = fail_departure
        self.events = events

    def current_scene(self, scene_ids, *, update=False):
        assert scene_ids == [34, 35, 49]
        assert update is True
        return self.scene_id, 100.0, "frame"

    def click_shape_center_then_view(self, scene_id, shape, *targets, **_kwargs):
        self.actions.append((scene_id, shape, targets))
        if self.events is not None and len(self.actions) == 1:
            self.events.append("departure")
        if self.fail_departure:
            raise RuntimeError("无法从当前#49找到可达#34的路径")
        target = targets[0]
        self.scene_id = target
        yield "running"
        return target


class _InterruptedDepartureRuntime:
    def current_scene(self, scene_ids, *, update=False):
        assert scene_ids == [34, 35, 49]
        assert update is True
        return 49, 100.0, "frame"

    def click_shape_center_then_view(self, _scene_id, _shape, *_targets, **_kwargs):
        raise InterruptedError("Cell 已停止")
        yield "unreachable"


class _RedeemRunner(GiftCodeTaskMixin, MiscActionTaskMixin):
    def __init__(self, *, fail_at: int | None = None) -> None:
        self._lock = threading.RLock()
        self.processed: list[tuple[str, bool]] = []
        self.statuses: list[tuple[str, str]] = []
        self.logs: list[tuple[str, str]] = []
        self.fail_at = fail_at

    def _raise_if_stopped(self, _stop_event) -> None:
        return None

    def _set_status_locked(self, _status, message, *, phase, **_kwargs) -> None:
        self.statuses.append((phase, message))

    def _log_locked(self, kind, message) -> None:
        self.logs.append((kind, message))

    def _log(self, kind, message) -> None:
        self.logs.append((kind, message))

    def _process_code(self, _ctx, code, is_last, _stop_event) -> None:
        self.processed.append((code, is_last))
        if self.fail_at == len(self.processed):
            raise RuntimeError(f"第 {self.fail_at} 个兑换失败")


class _WeeklyRedeemRunner(_RedeemRunner):
    def __init__(self, runtime: _RedeemRuntime, events: list[str]) -> None:
        super().__init__()
        self.runtime = runtime
        self.events = events
        self.next_times: list[tuple[str, str]] = []

    def _execute_gift_code_task(
        self,
        ctx,
        codes,
        stop_event,
        *,
        on_codes_processed=None,
    ):
        return (
            yield from self._redeem_gift_codes_from_settings(
                ctx,
                self.runtime,
                codes,
                stop_event,
                on_codes_processed=on_codes_processed,
            )
        )

    def _persist_scheduler_task_next_time(self, task_id, next_time) -> None:
        self.events.append("checkpoint")
        self.next_times.append((task_id, next_time))

    def _log(self, kind, message) -> None:
        self.logs.append((kind, message))


def test_redeem_gift_codes_from_settings_processes_all_then_returns_world() -> None:
    runner = _RedeemRunner()
    runtime = _RedeemRuntime()

    result = _drain(
        runner._redeem_gift_codes_from_settings(
            {},
            runtime,
            ["秋风初至", "晚风知秋"],
            object(),
        ),
    )

    assert runner.processed == [("秋风初至", False), ("晚风知秋", True)]
    assert runtime.actions == [
        (49, "回退", (34, 35)),
    ]
    assert result == {
        "result": "success",
        "message": "已处理 2 个礼包码并返回世界",
        "current_scene": 34,
        "code_count": 2,
    }


def test_all_codes_checkpoint_before_departure_failure_returns_success_warning(monkeypatch) -> None:
    monkeypatch.setattr(gift_code, "_now", lambda: datetime(2026, 8, 10, 23, 40))
    events: list[str] = []
    runtime = _RedeemRuntime(fail_departure=True, events=events)
    runner = _WeeklyRedeemRunner(runtime, events)

    result = _drain(
        runner._execute_weekly_gift_code_task(
            {},
            object(),
            {
                "__scheduler_task_id": "gift-code-instance",
                "codes": [f"礼包{i}" for i in range(1, 7)],
            },
        ),
    )

    assert len(runner.processed) == 6
    assert events == ["checkpoint", "departure"]
    assert runner.next_times == [("gift-code-instance", "2026-08-17 23:30:00")]
    assert result["result"] == "success"
    assert result["current_scene"] == 49
    assert "next_time" not in result
    assert "离场失败" in result["departure_warning"]
    assert [kind for kind, _message in runner.logs[-2:]] == ["warning", "success"]


def test_registered_weekly_handler_does_not_repeat_failed_departure(monkeypatch) -> None:
    monkeypatch.setattr(gift_code, "_now", lambda: datetime(2026, 8, 10, 23, 40))
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("weekly_gift_code")
    assert definition is not None
    events: list[str] = []
    departure_runtime = _RedeemRuntime(fail_departure=True, events=events)

    class _HandlerRunner(_WeeklyRedeemRunner):
        def __init__(self) -> None:
            super().__init__(departure_runtime, events)

        def _fanxiu_runtime(self, _ctx, *, stop_event):
            pytest.fail("正式 weekly handler 不得在业务执行前强制导航")

    runner = _HandlerRunner()
    result = _drain(
        definition.handler(
            runner,
            {},
            {
                "__scheduler_task_id": "gift-code-instance",
                "codes": [f"礼包{i}" for i in range(1, 7)],
            },
            threading.Event(),
        ),
    )

    assert result["result"] == "success"
    assert result["current_scene"] == 49
    assert "next_time" not in result
    assert events == ["checkpoint", "departure"]


def test_departure_interruption_is_not_downgraded_to_warning() -> None:
    runner = _RedeemRunner()
    checkpoint_calls: list[str] = []

    with pytest.raises(InterruptedError, match="Cell 已停止"):
        _drain(
            runner._redeem_gift_codes_from_settings(
                {},
                _InterruptedDepartureRuntime(),
                ["礼包1"],
                object(),
                on_codes_processed=lambda: checkpoint_calls.append("checkpoint") or "next",
            ),
        )

    assert checkpoint_calls == ["checkpoint"]
    assert not any(kind == "warning" for kind, _message in runner.logs)


def test_mid_batch_failure_stays_error_and_does_not_checkpoint() -> None:
    runner = _RedeemRunner(fail_at=3)
    runtime = _RedeemRuntime()
    checkpoint_calls: list[str] = []

    with pytest.raises(RuntimeError, match="第 3 个兑换失败"):
        _drain(
            runner._redeem_gift_codes_from_settings(
                {},
                runtime,
                [f"礼包{i}" for i in range(1, 7)],
                object(),
                on_codes_processed=lambda: checkpoint_calls.append("checkpoint") or "unused",
            ),
        )

    assert len(runner.processed) == 3
    assert checkpoint_calls == []
    assert runtime.actions == []


def test_weekly_checkpoint_is_idempotent_when_executor_repeats_callback(monkeypatch) -> None:
    monkeypatch.setattr(gift_code, "_now", lambda: datetime(2026, 8, 10, 23, 40))

    class _RepeatedCheckpointRunner(_Runner):
        def _execute_gift_code_task(
            self,
            _ctx,
            codes,
            _stop_event,
            *,
            on_codes_processed=None,
        ) -> dict:
            self.executed_codes.append(list(codes))
            assert on_codes_processed is not None
            on_codes_processed()
            on_codes_processed()
            return {"current_scene": 34}

    runner = _RepeatedCheckpointRunner()
    result = _drain(
        runner._execute_weekly_gift_code_task(
            {},
            object(),
            {"codes": ["礼包1"], "__scheduler_task_id": "gift-code-instance"},
        ),
    )

    assert "next_time" not in result
    assert runner.next_times == [("gift-code-instance", "2026-08-17 23:30:00")]
