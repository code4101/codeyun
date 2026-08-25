from backend.core.fanxiu.data_annotation.behavior_tree_runtime import BehaviorTreeRuntimeRunner


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

        def wait_click(self, scene, shape):
            self.calls.append(("wait_click", scene, shape))
            yield

        def wait_action_settle(self, seconds):
            self.calls.append(("settle", seconds))
            yield

        def wait_click_then_view(self, scene, shape, target):
            self.calls.append(("wait_click_then_view", scene, shape, target))
            yield

    runner = BehaviorTreeRuntimeRunner.__new__(BehaviorTreeRuntimeRunner)
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
        ("wait_click_then_view", 313, "确定", 314),
    ]

    checked = Runtime(81.0)
    assert _drain(runner._continue_daily_lingmai_clear_from_explore(checked, {}, task_label="灵脉_清体力")) == "manual_check_pending"
    assert checked.calls == [("wait_click_then_view", 313, "确定", 314)]


def test_lingmai_clear_drags_annotated_scrollbar_then_confirms():
    class Runtime:
        def __init__(self):
            self.calls = []

        def drag_shape_to_frame_edge(self, scene, shape, **options):
            self.calls.append(("drag_shape_to_frame_edge", scene, shape, options))

        def wait_action_settle(self, seconds):
            self.calls.append(("settle", seconds))
            yield

        def wait_click_then_view(self, scene, shape, targets, **options):
            self.calls.append(("wait_click_then_view", scene, shape, targets, options))
            yield
            return 285

    runtime = Runtime()
    runner = BehaviorTreeRuntimeRunner.__new__(BehaviorTreeRuntimeRunner)
    runner._log = lambda *_args, **_kwargs: None

    result = _drain(runner._continue_daily_lingmai_clear_from_amount(runtime, {}, task_label="灵脉_清体力"))

    assert result == "success"
    assert runtime.calls == [
        ("drag_shape_to_frame_edge", 314, "滚动条", {"direction": "right", "duration": 0.6}),
        ("settle", 1.0),
        (
            "wait_click_then_view",
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

        def wait_click_then_view(self, scene, shape, targets, **options):
            self.calls.append(("wait_click_then_view", scene, shape, targets, options))
            yield
            return View() if scene == 314 else 285

    runtime = Runtime()
    runner = BehaviorTreeRuntimeRunner.__new__(BehaviorTreeRuntimeRunner)
    runner._log = lambda *_args, **_kwargs: None

    result = _drain(runner._continue_daily_lingmai_clear_from_amount(runtime, {}, task_label="灵脉_清体力"))

    assert result == "success"
    assert runtime.calls[-1] == (
        "wait_click_then_view",
        315,
        "继续",
        [313, 285],
        {"settle_seconds": 0.2, "timeout": 2.0, "label": "灵脉_清体力：点击 #315 继续后等待 #313/#285"},
    )


def test_lingmai_clear_fails_when_confirm_returns_to_313():
    class Runtime:
        def drag_shape_to_frame_edge(self, *_args, **_kwargs):
            pass

        def wait_action_settle(self, _seconds):
            yield

        def wait_click_then_view(self, *_args, **_kwargs):
            yield
            return 313

    runner = BehaviorTreeRuntimeRunner.__new__(BehaviorTreeRuntimeRunner)
    runner._log = lambda *_args, **_kwargs: None

    try:
        _drain(runner._continue_daily_lingmai_clear_from_amount(Runtime(), {}, task_label="灵脉_清体力"))
    except RuntimeError as exc:
        assert "滚动条未拖满" in str(exc)
    else:
        raise AssertionError("返回 #313 时必须失败，不能循环掩盖拖拽不足")


def test_lingmai_clear_tolerates_transient_315_expiring_before_click():
    class Runtime:
        def __init__(self):
            self.calls = []

        def wait_click_then_view(self, scene, shape, target, **options):
            self.calls.append(("wait_click_then_view", scene, shape, target, options))
            yield
            raise TimeoutError("#315 expired")

        def wait_view(self, *scenes, **options):
            self.calls.append(("wait_view", scenes, options))
            yield
            return 285

    runtime = Runtime()
    runner = BehaviorTreeRuntimeRunner.__new__(BehaviorTreeRuntimeRunner)
    runner._log = lambda *_args, **_kwargs: None

    result = _drain(runner._continue_daily_lingmai_clear_from_transient(runtime, {}, task_label="灵脉_清体力"))

    assert result == "success"
    assert runtime.calls == [
        (
            "wait_click_then_view",
            315,
            "继续",
            [313, 285],
            {"settle_seconds": 0.2, "timeout": 2.0, "label": "灵脉_清体力：点击 #315 继续后等待 #313/#285"},
        ),
        (
            "wait_view",
            (313, 285),
            {"timeout": 15.0, "label": "灵脉_清体力：等待有时效性的 #315 自动消失并回到 #313/#285"},
        ),
    ]
