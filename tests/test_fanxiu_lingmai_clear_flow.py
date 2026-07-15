from backend.core.fanxiu.data_annotation.runtime_runner import DataAnnotationRuntimeRunner


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

    runner = DataAnnotationRuntimeRunner.__new__(DataAnnotationRuntimeRunner)
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


def test_lingmai_clear_clicks_annotated_scrollbar_right_endpoint_then_confirms():
    class Shape:
        def box(self):
            return {"x": 230.0, "y": 960.0, "w": 455.0, "h": 52.0}

    class Runtime:
        def __init__(self):
            self.calls = []

        def shape(self, scene, title):
            assert (scene, title) == (314, "滚动条")
            return Shape()

        def click_frame_point(self, scene, x, y):
            self.calls.append(("click_frame_point", scene, x, y))

        def wait_action_settle(self, seconds):
            self.calls.append(("settle", seconds))
            yield

        def wait_click_then_view(self, scene, shape, targets, **options):
            self.calls.append(("wait_click_then_view", scene, shape, targets, options))
            yield
            return 285

    runtime = Runtime()
    runner = DataAnnotationRuntimeRunner.__new__(DataAnnotationRuntimeRunner)
    runner._log = lambda *_args, **_kwargs: None

    result = _drain(runner._continue_daily_lingmai_clear_from_amount(runtime, {}, task_label="灵脉_清体力"))

    assert result == "success"
    assert runtime.calls == [
        ("click_frame_point", 314, 685.0, 986.0),
        ("settle", 1.0),
        (
            "wait_click_then_view",
            314,
            "确定",
            [315, 285],
            {"timeout": 15.0, "label": "灵脉_清体力：等待 #314 确定后的 #315 或 #285"},
        ),
    ]


def test_lingmai_clear_clicks_transient_315_when_observed():
    class View:
        id = 315

    class Shape:
        def box(self):
            return {"x": 230.0, "y": 960.0, "w": 455.0, "h": 52.0}

    class Runtime:
        def __init__(self):
            self.calls = []

        def shape(self, scene, title):
            return Shape()

        def click_frame_point(self, scene, x, y):
            self.calls.append(("click_frame_point", scene, x, y))

        def wait_action_settle(self, seconds):
            self.calls.append(("settle", seconds))
            yield

        def wait_click_then_view(self, scene, shape, targets, **options):
            self.calls.append(("wait_click_then_view", scene, shape, targets, options))
            yield
            return View() if scene == 314 else 285

    runtime = Runtime()
    runner = DataAnnotationRuntimeRunner.__new__(DataAnnotationRuntimeRunner)
    runner._log = lambda *_args, **_kwargs: None

    result = _drain(runner._continue_daily_lingmai_clear_from_amount(runtime, {}, task_label="灵脉_清体力"))

    assert result == "success"
    assert runtime.calls[-1] == (
        "wait_click_then_view",
        315,
        "继续",
        285,
        {"settle_seconds": 0.2, "timeout": 2.0, "label": "灵脉_清体力：点击 #315 继续后等待 #285"},
    )


def test_lingmai_clear_tolerates_transient_315_expiring_before_click():
    class Runtime:
        def __init__(self):
            self.calls = []

        def wait_click_then_view(self, scene, shape, target, **options):
            self.calls.append(("wait_click_then_view", scene, shape, target, options))
            yield
            raise TimeoutError("#315 expired")

        def wait_view(self, scene, **options):
            self.calls.append(("wait_view", scene, options))
            yield
            return 285

    runtime = Runtime()
    runner = DataAnnotationRuntimeRunner.__new__(DataAnnotationRuntimeRunner)
    runner._log = lambda *_args, **_kwargs: None

    result = _drain(runner._continue_daily_lingmai_clear_from_transient(runtime, {}, task_label="灵脉_清体力"))

    assert result == "success"
    assert runtime.calls == [
        (
            "wait_click_then_view",
            315,
            "继续",
            285,
            {"settle_seconds": 0.2, "timeout": 2.0, "label": "灵脉_清体力：点击 #315 继续后等待 #285"},
        ),
        (
            "wait_view",
            285,
            {"timeout": 15.0, "label": "灵脉_清体力：等待有时效性的 #315 自动消失并回到 #285"},
        ),
    ]
