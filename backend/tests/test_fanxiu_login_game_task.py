from __future__ import annotations

import threading

import pytest

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.tasks.login_game import LoginGameTaskMixin
from backend.core.fanxiu.data_annotation.tasks import login_game as login_game_module


@pytest.fixture(autouse=True)
def _isolate_mumu_startup_state(monkeypatch):
    monkeypatch.setattr(login_game_module, "mark_mumu_device_startup_ready", lambda **_kwargs: {})
    monkeypatch.setattr(
        login_game_module,
        "mumu_device_health_check",
        lambda **_kwargs: {"status": "healthy"},
    )


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


class _FakeRuntime:
    def __init__(self, scenes, *, ocr_text="", bubble_visible=False, cover_action_visible=False):
        self.scenes = list(scenes)
        self._ocr_text = ocr_text
        self.bubble_visible = bool(bubble_visible)
        self.cover_action_visible = bool(cover_action_visible)
        self.actions: list[tuple] = []
        self.completion_message = ""

    def current_scene(self, _scene_ids, *, update=False):
        assert update is True
        scene_id = self.scenes.pop(0)
        return scene_id, 100.0 if scene_id is not None else 0.0, f"frame-{scene_id}"

    def wait_click_then_view(self, scene_id, shape, targets, **_options):
        self.actions.append(("click", scene_id, shape, targets))
        if False:
            yield None
        return targets

    def wait_action_settle(self, seconds):
        self.actions.append(("settle", seconds))
        if False:
            yield None

    def goto_view(self, scene_id):
        self.actions.append(("goto", scene_id))
        if False:
            yield None
        return "success"

    def ocr_text(self, _frame):
        return self._ocr_text

    def match_view(self, scene_id, *, frame_data_url):
        assert scene_id == 18
        assert frame_data_url.startswith("frame-")
        return self.cover_action_visible, (100.0 if self.cover_action_visible else 0.0), frame_data_url

    def shape_matches(self, scene_id, shape, *, frame_data_url):
        assert frame_data_url.startswith("frame-")
        assert (scene_id, shape) == (421, "气泡")
        if not self.bubble_visible:
            return None
        return {
            "unique_match": True,
            "resolved_box": {"x1": 10, "y1": 20, "x2": 50, "y2": 60},
        }

    def click_shape_center(self, scene_id, shape):
        self.actions.append(("click_center", scene_id, shape))

    def set_completion_message(self, message):
        self.completion_message = message


class _FakeRunner(LoginGameTaskMixin):
    scene_threshold = 80

    def __init__(self, scenes, *, ocr_text="", bubble_visible=False, cover_action_visible=False):
        self._lock = threading.Lock()
        self.runtime = _FakeRuntime(
            scenes,
            ocr_text=ocr_text,
            bubble_visible=bubble_visible,
            cover_action_visible=cover_action_visible,
        )
        self.logs: list[tuple[str, str]] = []
        self.overlay_checks = 0

    def _fanxiu_runtime(self, _ctx, _asset_tree_path=None, *, stop_event):
        assert isinstance(stop_event, threading.Event)
        return self.runtime

    def _raise_if_stopped(self, _stop_event):
        return None

    def _known_blocking_overlay_info(self, _ctx):
        self.overlay_checks += 1
        return None

    def _set_status_locked(self, *_args, **_kwargs):
        return None

    def _log(self, level, message):
        self.logs.append((level, message))

    def _schedule_bubble_reconcile_after_login(self, *, now):
        del now
        return "bubble-weekly-pills"


def test_login_game_is_visible_manual_standard_job_without_stable_world_start():
    register_fanxiu_data_annotation_default_runtime_jobs()

    definition = get_fanxiu_data_annotation_task_cell_definition("login_game")

    assert definition is not None
    assert definition.label == "登录"
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    assert definition.standard_job_id == "login-game"
    assert definition.standard_job_description == "手动"
    assert definition.standard_job_payload == {"unbounded_runtime": True}
    assert not hasattr(definition, "lifecycle")


def test_visible_login_job_returns_to_dormant_after_normal_completion():
    next_time_updates = []
    world_navigation = []

    class FakeRunner:
        def _fanxiu_runtime(self, _ctx, _asset_tree_path=None, *, stop_event):
            class Runtime:
                @staticmethod
                def goto_view(scene_id):
                    world_navigation.append(int(scene_id))
                    if False:
                        yield None
                    return scene_id
            return Runtime()

        def _execute_login_game_task(self, _ctx, _stop_event, _payload):
            yield "running"
            self._login_game_terminal_message = "登录游戏完成，已在 #34；气泡已隐藏"
            return "success"

        def _persist_scheduler_task_next_time(self, task_id, next_time):
            next_time_updates.append((task_id, next_time))

    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("login_game")

    assert definition is not None
    assert _drain(definition.handler(FakeRunner(), {}, {}, threading.Event())) == {
        "result": "success",
        "message": "登录游戏完成，已在 #34；气泡已隐藏",
    }
    assert next_time_updates == [("login-game", None)]
    assert world_navigation == []


def test_login_game_uses_current_account_and_reaches_world():
    runner = _FakeRunner([15, 17, 18, 19, 34])

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert runner.runtime.actions == [
        ("click_center", 15, "登录"),
        ("settle", 2.0),
        ("click_center", 17, "同意"),
        ("settle", 2.0),
        ("click_center", 18, "进入游戏"),
        ("settle", 2.0),
    ]
    assert runner.runtime.completion_message == (
        "登录游戏完成，已在 #19；气泡周事务已触发"
    )


def test_login_game_continues_from_formal_character_entry_scene():
    runner = _FakeRunner([661, 34])

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert runner.runtime.actions == [
        ("click_center", 661, "进入"),
        ("settle", 2.0),
    ]
    assert runner.runtime.completion_message == (
        "登录游戏完成，已在 #34；气泡周事务已触发"
    )


def test_login_completion_runs_bubble_lifecycle_followup_hook():
    runner = _FakeRunner([34])
    calls = []
    runner._schedule_bubble_reconcile_after_login = (
        lambda **kwargs: calls.append(kwargs) or "bubble-weekly-pills"
    )

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert len(calls) == 1
    assert "now" in calls[0]
    assert any("bubble-weekly-pills" in message for _level, message in runner.logs)


def test_login_completion_hides_recreated_bubble_inline_before_success():
    runner = _FakeRunner([611, 34])
    calls = []

    def reconcile(_ctx, _stop_event, _payload):
        calls.append("hide")
        if False:
            yield None
        return {"mode": "hidden_inline", "hide": {"result": "success"}}

    runner._reconcile_bubble_after_login = reconcile

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert calls == ["hide"]
    assert runner.runtime.actions == [("goto", 34)]
    assert runner.runtime.completion_message == "登录游戏完成，已在 #34；气泡已隐藏"
    assert any("同步确认气泡隐藏" in message for _level, message in runner.logs)


def test_login_never_reports_success_when_inline_hide_fails():
    runner = _FakeRunner([34])

    def reconcile(_ctx, _stop_event, _payload):
        raise RuntimeError("bubble still visible")
        yield

    runner._reconcile_bubble_after_login = reconcile

    with pytest.raises(RuntimeError, match="still visible"):
        _drain(runner._execute_login_game_task({}, threading.Event()))

    assert runner.runtime.completion_message == ""


def test_login_visible_bubble_proves_arbitrary_business_page_ready_and_reconciles():
    runner = _FakeRunner([None], bubble_visible=True)
    calls = []

    def reconcile(_ctx, _stop_event, _payload):
        calls.append("hide")
        if False:
            yield None
        return {"mode": "hidden_inline", "hide": {"result": "success"}}

    runner._reconcile_bubble_after_login = reconcile

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert calls == ["hide"]
    assert runner.runtime.actions == []
    assert runner.runtime.completion_message == (
        "登录游戏完成，已在 #421 气泡覆盖的已登录业务页；气泡已隐藏"
    )


def test_login_visible_bubble_does_not_override_cover_action():
    runner = _FakeRunner(
        [None, 18, 19],
        bubble_visible=True,
        cover_action_visible=True,
    )

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert ("click_center", 18, "进入游戏") in runner.runtime.actions
    assert runner.runtime.completion_message == (
        "登录游戏完成，已在 #19；气泡周事务已触发"
    )


def test_login_reconciles_bubble_without_restart_token(monkeypatch):
    runner = _FakeRunner([34])
    calls = []
    runner._schedule_bubble_reconcile_after_login = (
        lambda **kwargs: calls.append(("reconcile", kwargs)) or "bubble-weekly-pills"
    )

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert [kind for kind, _kwargs in calls] == ["reconcile"]


def test_scheduled_business_defers_to_login_by_next_time_without_login_actions():
    runner = _FakeRunner([14])
    scheduled = []
    runner._schedule_login_job_first = lambda: scheduled.append("login-game")
    runner._persist_scheduler_task_next_time = lambda task_id, next_time: scheduled.append(
        (task_id, next_time)
    )

    result = _drain(
        runner._ensure_world_ready_via_login_game(
            {},
            threading.Event(),
            {"__scheduler_task_id": "daily-redpacket"},
        )
    )

    assert result == "scheduled"
    assert scheduled[0] == "login-game"
    assert scheduled[1][0] == "daily-redpacket"
    assert scheduled[1][1]
    assert runner.runtime.actions == []


def test_scheduled_business_accepts_stable_world_without_scheduling_login():
    runner = _FakeRunner([34])
    scheduled = []
    runner._schedule_login_job_first = lambda: scheduled.append("login-game")

    result = _drain(
        runner._ensure_world_ready_via_login_game(
            {},
            threading.Event(),
            {"__scheduler_task_id": "lilian-event"},
        )
    )

    assert result is False
    assert scheduled == []
    assert runner.runtime.actions == []


def test_login_accepts_fresh_world_without_startup_page_or_restart(monkeypatch):
    ready_reasons = []
    recoveries = []
    monkeypatch.setattr(
        login_game_module,
        "mark_mumu_device_startup_ready",
        lambda **kwargs: ready_reasons.append(kwargs.get("reason")) or {},
    )
    monkeypatch.setattr(
        login_game_module,
        "recover_mumu_device",
        lambda **kwargs: recoveries.append(kwargs) or {"recovered": True, "status": "healthy"},
    )
    runner = _FakeRunner([34])

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert runner.runtime.actions == []
    assert ready_reasons == ["login_game_scene_34"]
    assert recoveries == []
    assert not any("公告" in message or "封面" in message for _level, message in runner.logs)


def test_registered_login_job_clears_next_time_when_login_required_world_is_fresh(monkeypatch):
    next_time_updates = []

    class _RegisteredRunner(_FakeRunner):
        def _persist_scheduler_task_next_time(self, task_id, next_time):
            next_time_updates.append((task_id, next_time))

    runner = _RegisteredRunner([34])
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("login_game")

    assert definition is not None
    assert _drain(definition.handler(runner, {}, {}, threading.Event())) == {
        "result": "success",
        "message": "登录游戏完成，已在 #34；气泡周事务已触发",
    }
    assert next_time_updates == [("login-game", None)]
    assert runner.runtime.actions == []


def test_world_task_preflight_reuses_login_action_for_formal_cover_scene():
    runner = _FakeRunner([18, 18, 34])

    result = _drain(runner._ensure_world_ready_via_login_game({}, threading.Event()))

    assert result is True
    assert runner.runtime.actions == [
        ("click_center", 18, "进入游戏"),
        ("settle", 2.0),
    ]
    assert any("调用标准“登录游戏”动作" in message for _level, message in runner.logs)


def test_world_task_preflight_does_not_infer_login_from_resource_loading_ocr():
    runner = _FakeRunner(
        [None, 18, 34],
        ocr_text="AppVer:2.46.700211 正在初始化资源...89%",
    )

    result = _drain(runner._ensure_world_ready_via_login_game({}, threading.Event()))

    assert result is True
    assert runner.runtime.actions == [
        ("click_center", 18, "进入游戏"),
        ("settle", 2.0),
    ]


def test_world_task_preflight_leaves_non_login_business_scene_to_navigation():
    runner = _FakeRunner([69])

    result = _drain(runner._ensure_world_ready_via_login_game({}, threading.Event()))

    assert result is False
    assert runner.runtime.actions == []


def test_login_game_marks_restart_ready_only_after_world(monkeypatch):
    ready_reasons = []
    monkeypatch.setattr(
        login_game_module,
        "mark_mumu_device_startup_ready",
        lambda **kwargs: ready_reasons.append(kwargs.get("reason")) or {},
    )
    runner = _FakeRunner([34])

    assert _drain(runner._execute_login_game_task({}, threading.Event())) == "success"
    assert ready_reasons == ["login_game_scene_34"]


def test_login_game_stops_on_account_picker():
    runner = _FakeRunner([16])

    with pytest.raises(RuntimeError, match="避免误登"):
        _drain(runner._execute_login_game_task({}, threading.Event()))

    assert runner.runtime.actions == []


def test_login_game_does_not_infer_cover_action_from_full_frame_ocr():
    runner = _FakeRunner(
        [None, 34],
        ocr_text="AppVer:2.46.700211 进入游戏 健康游戏忠告",
    )

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert runner.runtime.actions == [("settle", 2.0)]


def test_login_game_waits_for_resource_loading_instead_of_reporting_success():
    runner = _FakeRunner(
        [None, 34],
        ocr_text="AppVer:2.46.700211 正在初始化资源...76%",
    )

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert runner.runtime.actions == [("settle", 2.0)]
    assert runner.runtime.completion_message == (
        "登录游戏完成，已在 #34；气泡周事务已触发"
    )


def test_login_game_restarts_mumu_after_resource_loading_timeout(monkeypatch):
    runner = _FakeRunner(
        [None, None, 34],
        ocr_text="AppVer:2.46.700211 正在初始化资源...76%",
    )
    recoveries = []
    ticks = iter((0.0, 301.0))
    monkeypatch.setattr(login_game_module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(
        login_game_module,
        "recover_mumu_device",
        lambda **kwargs: recoveries.append(kwargs) or {"status": "healthy", "recovered": True},
    )

    result = _drain(
        runner._execute_login_game_task(
            {}, threading.Event(), {"loading_timeout_seconds": 300}
        )
    )

    assert result == "success"
    assert recoveries == [{
        "vmindex": "1",
        "reason": "login_game_loading_timeout",
        "force_restart": True,
    }]


def test_login_game_uses_formal_cover_scene_after_formal_announcement():
    runner = _FakeRunner([14, 18, 34])

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert runner.runtime.actions == [
        ("click_center", 14, "关闭公告"),
        ("settle", 2.0),
        ("click_center", 18, "进入游戏"),
        ("settle", 2.0),
    ]


@pytest.mark.parametrize(
    ("scene_id", "shape"),
    [(14, "关闭公告"), (15, "登录"), (17, "同意")],
)
@pytest.mark.parametrize("next_scene_id", [None, 49])
def test_formal_login_action_reclassifies_unknown_or_business_scene_without_retry(
    scene_id,
    shape,
    next_scene_id,
):
    runner = _FakeRunner(
        [scene_id, next_scene_id] + ([49] if next_scene_id is None else [])
    )

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    expected_actions = [
        ("click_center", scene_id, shape),
        ("settle", 2.0),
    ]
    if next_scene_id is None:
        expected_actions.append(("settle", 2.0))
    assert runner.runtime.actions == expected_actions


def test_login_game_does_not_treat_world_ocr_as_scene_identity():
    runner = _FakeRunner([20, 34], ocr_text="储物袋 角色 装备 星海 功法书")

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert runner.runtime.actions == []


def test_login_game_waits_for_unknown_frame_until_game_scene_is_recognized():
    runner = _FakeRunner(
        [None, 34],
        ocr_text="玄阴祭炼诀 获得额外周天功法经验 虚天殿 前往",
    )

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert runner.runtime.actions == [("settle", 2.0)]
    assert runner.runtime.completion_message == (
        "登录游戏完成，已在 #34；气泡周事务已触发"
    )


def test_login_game_recovers_device_before_scene_actions_when_health_is_not_ready(monkeypatch):
    runner = _FakeRunner([18, 34])
    recoveries = []
    monkeypatch.setattr(
        login_game_module,
        "mumu_device_health_check",
        lambda **_kwargs: {"status": "stopped"},
    )
    monkeypatch.setattr(
        login_game_module,
        "recover_mumu_device",
        lambda **kwargs: recoveries.append(kwargs) or {"status": "healthy", "recovered": True},
    )

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert recoveries == [{
        "vmindex": "1",
        "reason": "login_game_device_not_started",
    }]
    assert runner.runtime.actions == [
        ("click_center", 18, "进入游戏"),
        ("settle", 2.0),
    ]


def test_login_game_keeps_non_login_in_game_scene_without_navigation():
    runner = _FakeRunner([22])

    result = _drain(
        runner._execute_login_game_task(
            {},
            threading.Event(),
            {"require_startup_page_before_world": True},
        )
    )

    assert result == "success"
    assert runner.runtime.actions == []


def test_login_game_rejects_recognized_scene_outside_explicit_terminal_set():
    runner = _FakeRunner([69])

    with pytest.raises(RuntimeError, match="不是已定义的登录终态"):
        _drain(runner._execute_login_game_task({}, threading.Event()))

    assert runner.runtime.actions == []
    assert runner.runtime.completion_message == ""


def test_scene_49_with_announcement_text_never_uses_overlay_or_scene_14_coordinates():
    runner = _FakeRunner([49], ocr_text="游戏公告 更新公告 关闭公告")

    result = _drain(runner._execute_login_game_task({}, threading.Event()))

    assert result == "success"
    assert runner.runtime.actions == []
    assert runner.overlay_checks == 0
    assert runner.runtime.completion_message == (
        "登录游戏完成，已在 #49；气泡周事务已触发"
    )


def test_login_game_does_not_report_success_when_device_recovery_is_not_healthy(monkeypatch):
    monkeypatch.setattr(
        login_game_module,
        "mumu_device_health_check",
        lambda **_kwargs: {"status": "stopped"},
    )
    monkeypatch.setattr(
        login_game_module,
        "recover_mumu_device",
        lambda **_kwargs: {"status": "broken", "recovered": False},
    )
    runner = _FakeRunner([None])

    with pytest.raises(RuntimeError, match="标准恢复失败"):
        _drain(runner._execute_login_game_task({}, threading.Event()))

    assert runner.runtime.actions == []
