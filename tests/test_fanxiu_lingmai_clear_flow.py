from backend.core.fanxiu.data_annotation.behavior_tree_executor import BehaviorTreeExecutor


def _drain(action):
    while True:
        try:
            next(action)
        except StopIteration as done:
            return done.value


def test_lingmai_clear_checks_unchecked_image_before_clicking_one_click_explore(monkeypatch):
    class Runtime:
        def __init__(self, score):
            self.score = score
            self.calls = []

        def cur_frame(self, *, update):
            return "frame"

        def shape_score(self, scene, shape, *, frame_data_url):
            return self.score

        def ocr_text_in_shapes(self, *_args, **_kwargs):
            return "体力 30/1900"

        def wait_click(self, scene, shape):
            self.calls.append(("wait_click", scene, shape))
            yield

        def wait_action_settle(self, seconds):
            self.calls.append(("settle", seconds))
            yield

        def wait_click_then_scene(self, scene, shape, target):
            self.calls.append(("wait_click_then_scene", scene, shape, target))
            yield

    runner = BehaviorTreeExecutor.__new__(BehaviorTreeExecutor)
    runner._log = lambda *_args, **_kwargs: None

    def continue_amount(_runtime, _payload, *, task_label):
        if False:
            yield
        return "manual_check_pending"

    monkeypatch.setattr(runner, "_continue_daily_lingmai_clear_from_amount", continue_amount)

    unchecked = Runtime(100.0)
    assert _drain(runner._continue_daily_lingmai_clear_from_explore(unchecked, {}, task_label="灵脉_清体力")) == "manual_check_pending"
    assert unchecked.calls == [
        ("wait_click", 313, "一键探索"),
        ("settle", 1.0),
        ("wait_click_then_scene", 313, "确定", 314),
    ]

    checked = Runtime(81.0)
    assert _drain(runner._continue_daily_lingmai_clear_from_explore(checked, {}, task_label="灵脉_清体力")) == "manual_check_pending"
    assert checked.calls == [("wait_click_then_scene", 313, "确定", 314)]


def test_lingmai_clear_accepts_313_stamina_shape_when_checked_state_misses_scene_identity(monkeypatch):
    class Context:
        def wait_click_then_scene(self, *_args, **_kwargs):
            yield
            raise TimeoutError("#313 identity missed")

        def cur_frame(self, *, update=False):
            return "checked-313-frame"

        def current_scene(self, scene_ids, *, update=False):
            assert (scene_ids, update) == ([286, 313], False)
            return -1, 0.0, "checked-313-frame"

        def ocr_text_in_shapes(self, scene, shapes, **options):
            assert (scene, shapes, options) == (
                313,
                ("体力",),
                {"frame_data_url": "checked-313-frame"},
            )
            return "剩余聚灵体力 30/268"

    runner = BehaviorTreeExecutor.__new__(BehaviorTreeExecutor)
    runner._log = lambda *_args, **_kwargs: None

    def check_upgrade(*_args, **_kwargs):
        if False:
            yield
        return "insufficient"

    def continue_explore(*_args, **_kwargs):
        if False:
            yield
        return "continued"

    monkeypatch.setattr(runner, "_check_daily_lingmai_guiyuan_upgrade", check_upgrade)
    monkeypatch.setattr(runner, "_continue_daily_lingmai_clear_from_explore", continue_explore)

    assert _drain(
        runner._continue_daily_lingmai_clear_from_zaohua(
            Context(),
            {},
            task_label="灵脉_清体力",
        )
    ) == "continued"


def test_lingmai_clear_accepts_286_only_when_runtime_confirms_completed(monkeypatch):
    class Context:
        def __init__(self):
            self.calls = []

        def wait_click_then_scene(self, *_args, **_kwargs):
            yield
            raise TimeoutError("landed on #286")

        def cur_frame(self, *, update=False):
            return "select-slot-frame"

        def current_scene(self, scene_ids, *, update=False):
            assert (scene_ids, update) == ([286, 313], False)
            return 286, 100.0, "select-slot-frame"

        def go_scene(self, scene_id):
            self.calls.append(("go_scene", scene_id))
            yield

    runner = BehaviorTreeExecutor.__new__(BehaviorTreeExecutor)
    runner._log = lambda *_args, **_kwargs: None

    def check_upgrade(*_args, **_kwargs):
        if False:
            yield
        return "insufficient"

    monkeypatch.setattr(runner, "_check_daily_lingmai_guiyuan_upgrade", check_upgrade)
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.daily_foundation.refresh_lingmai_daily_status",
        lambda: {
            "available": True,
            "completed": True,
            "remaining_milliseconds": 0,
        },
    )
    monkeypatch.setattr(
        runner,
        "_complete_daily_clear_task",
        lambda payload, **_kwargs: f"completed:{payload['marker']}",
    )

    context = Context()
    assert _drain(
        runner._continue_daily_lingmai_clear_from_zaohua(
            context,
            {"marker": "runtime-terminal"},
            task_label="灵脉_清体力",
        )
    ) == "completed:runtime-terminal"
    assert context.calls == [("go_scene", 34)]


def test_lingmai_clear_drags_annotated_scrollbar_then_confirms():
    class Runtime:
        def __init__(self):
            self.calls = []

        def drag_shape_to_frame_edge(self, scene, shape, **options):
            self.calls.append(("drag_shape_to_frame_edge", scene, shape, options))

        def wait_action_settle(self, seconds):
            self.calls.append(("settle", seconds))
            yield

        def cur_frame(self, *, update=False):
            self.calls.append(("cur_frame", update))
            return "amount-frame"

        def ocr_text_in_shapes(self, scene, shapes, **options):
            self.calls.append(("ocr_text_in_shapes", scene, shapes, options))
            return "消耗体力 1170/1179"

        def wait_click_then_scene(self, scene, shape, targets, **options):
            self.calls.append(("wait_click_then_scene", scene, shape, targets, options))
            yield
            return 285

    runtime = Runtime()
    runner = BehaviorTreeExecutor.__new__(BehaviorTreeExecutor)
    runner._log = lambda *_args, **_kwargs: None

    result = _drain(runner._continue_daily_lingmai_clear_from_amount(runtime, {}, task_label="灵脉_清体力"))

    assert result == "success"
    assert runtime.calls == [
        ("drag_shape_to_frame_edge", 314, "滚动条", {"direction": "right", "duration": 0.6}),
        ("settle", 1.0),
        ("cur_frame", True),
        ("ocr_text_in_shapes", 314, ("消耗体力",), {"frame_data_url": "amount-frame"}),
        (
            "wait_click_then_scene",
            314,
            "确定",
            [315, 313, 285],
            {"timeout": 15.0, "label": "灵脉_清体力：等待 #314 确定后的 #315/#313/#285"},
        ),
    ]


def test_lingmai_clear_clicks_transient_315_when_observed():
    class View:
        id = 315

    class Runtime:
        def __init__(self):
            self.calls = []

        def drag_shape_to_frame_edge(self, scene, shape, **options):
            self.calls.append(("drag_shape_to_frame_edge", scene, shape, options))

        def wait_action_settle(self, seconds):
            self.calls.append(("settle", seconds))
            yield

        def cur_frame(self, *, update=False):
            return "amount-frame"

        def ocr_text_in_shapes(self, *_args, **_kwargs):
            return "消耗体力 1170/1179"

        def wait_click_then_scene(self, scene, shape, targets, **options):
            self.calls.append(("wait_click_then_scene", scene, shape, targets, options))
            yield
            return View() if scene == 314 else 285

    runtime = Runtime()
    runner = BehaviorTreeExecutor.__new__(BehaviorTreeExecutor)
    runner._log = lambda *_args, **_kwargs: None

    result = _drain(runner._continue_daily_lingmai_clear_from_amount(runtime, {}, task_label="灵脉_清体力"))

    assert result == "success"
    assert runtime.calls[-1] == (
        "wait_click_then_scene",
        315,
        "继续",
        [313, 285],
        {"settle_seconds": 0.2, "timeout": 2.0, "label": "灵脉_清体力：点击 #315 继续后等待 #313/#285"},
    )


def test_lingmai_clear_refuses_confirm_when_314_remainder_is_not_below_30():
    class Runtime:
        def __init__(self):
            self.confirmed = False

        def drag_shape_to_frame_edge(self, *_args, **_kwargs):
            pass

        def wait_action_settle(self, _seconds):
            yield

        def cur_frame(self, *, update=False):
            return "amount-frame"

        def ocr_text_in_shapes(self, *_args, **_kwargs):
            return "消耗体力 1140/1179"

        def wait_click_then_scene(self, *_args, **_kwargs):
            self.confirmed = True
            yield
            return 285

    runner = BehaviorTreeExecutor.__new__(BehaviorTreeExecutor)
    runner._log = lambda *_args, **_kwargs: None
    runtime = Runtime()

    try:
        _drain(runner._continue_daily_lingmai_clear_from_amount(runtime, {}, task_label="灵脉_清体力"))
    except RuntimeError as exc:
        assert "#314 滚动条未拖到末端" in str(exc)
        assert "差值 39 不小于 30" in str(exc)
    else:
        raise AssertionError("#314 差值不小于 30 时必须失败")
    assert runtime.confirmed is False


def test_lingmai_entry_keeps_confirmed_285_when_followup_frame_is_transient_unknown(monkeypatch):
    class Context:
        def cur_frame(self, *, update=False):
            return "transient-frame"

        def ocr_text(self, _frame):
            return "上次聚灵已确认"

    runner = BehaviorTreeExecutor.__new__(BehaviorTreeExecutor)
    runner._log = lambda *_args, **_kwargs: None

    def open_entry(*_args, **_kwargs):
        if False:
            yield
        return "open"

    def wait_entry(*_args, **_kwargs):
        if False:
            yield
        return 285

    monkeypatch.setattr(runner, "_open_daily_entry_from_daily", open_entry)
    monkeypatch.setattr(runner, "_wait_daily_lingmai_zaohua_after_entry", wait_entry)

    result = _drain(
        runner._enter_daily_lingmai_zaohua_from_world_or_daily(
            {},
            None,
            {},
            Context(),
            69,
            "daily-frame",
            "日常",
            task_label="灵脉_清体力",
        )
    )

    assert result == (285, 100.0, "transient-frame")


def test_lingmai_clear_tolerates_transient_315_expiring_before_click():
    class Runtime:
        def __init__(self):
            self.calls = []

        def wait_click_then_scene(self, scene, shape, target, **options):
            self.calls.append(("wait_click_then_scene", scene, shape, target, options))
            yield
            raise TimeoutError("#315 expired")

        def wait_scene(self, *scenes, **options):
            self.calls.append(("wait_scene", scenes, options))
            yield
            return 285

    runtime = Runtime()
    runner = BehaviorTreeExecutor.__new__(BehaviorTreeExecutor)
    runner._log = lambda *_args, **_kwargs: None

    result = _drain(runner._continue_daily_lingmai_clear_from_transient(runtime, {}, task_label="灵脉_清体力"))

    assert result == "success"
    assert runtime.calls == [
        (
            "wait_click_then_scene",
            315,
            "继续",
            [313, 285],
            {"settle_seconds": 0.2, "timeout": 2.0, "label": "灵脉_清体力：点击 #315 继续后等待 #313/#285"},
        ),
        (
            "wait_scene",
            (313, 285),
            {"timeout": 15.0, "label": "灵脉_清体力：等待有时效性的 #315 自动消失并回到 #313/#285"},
        ),
    ]
