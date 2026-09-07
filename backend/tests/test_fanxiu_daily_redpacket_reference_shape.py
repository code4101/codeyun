from __future__ import annotations

from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.tasks.daily_redpacket import (
    DailyRedpacketTaskMixin,
)


class _Runner(DailyRedpacketTaskMixin):
    def __init__(self) -> None:
        self.matches: list[tuple[dict, dict, str, str]] = []
        self.logs: list[tuple[str, str]] = []

    @staticmethod
    def _find_shape(image, title):
        return next(shape for shape in image["shapes"] if shape["title"] == title)

    def _match_shape(self, ctx, image, shape, frame, *, condition):
        self.matches.append((image, shape, frame, condition))
        return {"matched": True, "similarity": 96.0, "resolved_box": {"x": 1}}

    def _log(self, kind, message):
        self.logs.append((kind, message))


def test_claim_loop_consumes_sold_out_popup_after_open_without_counting_it():
    class Context:
        def __init__(self):
            self.clicks = []

        def wait_click(self, scene_id, shape, **_options):
            self.clicks.append((scene_id, shape))
            if False:
                yield None

        def wait_scene(self, scene_ids, **_options):
            assert list(scene_ids) == [398, 399, 672]
            if False:
                yield None
            return SimpleNamespace(id=672)

    class Runner(_Runner):
        def __init__(self):
            super().__init__()
            self.dismissed = 0

        def _dismiss_daily_redpacket_sold_out(self, _context, **_options):
            self.dismissed += 1
            if False:
                yield None
            return SimpleNamespace(id=30)

    runner = Runner()
    context = Context()
    generator = runner._claim_daily_redpackets(
        context,
        transition_timeout=5,
        max_open_count=10,
        current=SimpleNamespace(id=397),
    )

    try:
        next(generator)
    except StopIteration as done:
        opened = done.value
    else:  # pragma: no cover
        raise AssertionError("sold-out branch unexpectedly yielded")

    assert opened == 0
    assert context.clicks == [(397, "开")]
    assert runner.dismissed == 1
    assert any("已抢光红包" in message for _kind, message in runner.logs)
