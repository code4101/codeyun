from backend.core.fanxiu.data_annotation.behavior_tree_executor import BehaviorTreeExecutor


def _drain(action):
    while True:
        try:
            next(action)
        except StopIteration as done:
            return done.value


def test_purchase_open_retries_when_wait_returns_underlying_308():
    class Context:
        def __init__(self):
            self.calls = []
            self.attempt = 0

        def wait_click_then_scene(self, scene, shape, target, **options):
            self.attempt += 1
            self.calls.append(("wait_click_then_scene", scene, shape, target, options))
            yield
            return 308 if self.attempt == 1 else 311

        def sample_scene_once(self, scene_ids, *, update=False):
            self.calls.append(("sample_scene_once", scene_ids, update))
            return 308, 100.0, "base-frame"

        def ocr_text(self, frame):
            assert frame == "base-frame"
            return "快速战斗 剩余挑战次数 5"

        def wait_action_settle(self, seconds):
            self.calls.append(("wait_action_settle", seconds))
            yield

    runner = BehaviorTreeExecutor.__new__(BehaviorTreeExecutor)
    runner._log = lambda *_args, **_kwargs: None
    context = Context()

    assert _drain(
        runner._open_daily_xianyuan_duel_purchase(
            context,
            {},
            reason="首次检查购买档位",
        )
    ) is None
    assert context.attempt == 2
    assert ("sample_scene_once", [311, 308], True) in context.calls
    assert ("wait_action_settle", 1.0) in context.calls
