from backend.core.fanxiu.data_annotation.runtime_runner import DataAnnotationRuntimeRunner


class _Runtime:
    def __init__(self):
        self.calls = []

    def wait_click_then_view(self, scene, shape, target):
        self.calls.append(("wait_click_then_view", scene, shape, target))
        yield

    def wait_click(self, scene, shape):
        self.calls.append(("wait_click", scene, shape))
        yield


def test_daily_dongtian_known_occupation_chain():
    runtime = _Runtime()

    runner = DataAnnotationRuntimeRunner.__new__(DataAnnotationRuntimeRunner)
    list(runner._daily_dongtian_continue_enemy_occupation(runtime))

    assert runtime.calls == [
        ("wait_click_then_view", 341, "\u4f4d\u7f6e1", 342),
        ("wait_click_then_view", 342, "\u5360\u9886", 343),
        ("wait_click_then_view", 343, "\u5360\u9886", 344),
        ("wait_click_then_view", 344, "\u6218\u6597", 346),
        ("wait_click", 346, "\u7ee7\u7eed"),
    ]
