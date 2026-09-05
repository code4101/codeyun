from __future__ import annotations

from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.tasks.daily_redpacket import (
    DailyRedpacketTaskMixin,
)


class _Context:
    def __init__(self) -> None:
        self.scene_requests: list[list[int]] = []

    def current_scene(self, scene_ids, **_options):
        self.scene_requests.append(list(scene_ids))
        if False:
            yield None
        return 34, 100.0, "live-frame"

    def wait_action_settle(self, _seconds):
        if False:
            yield None


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


def test_reference_shape_guards_real_world_scene_instead_of_reference_scene():
    runner = _Runner()
    context = _Context()
    image = {"id": 395, "shapes": [{"title": "聊天"}]}
    generator = runner._wait_daily_redpacket_world_reference_shape(
        context,
        {"images": {395: image}},
        "聊天",
        timeout=1.0,
        label="红包入口",
    )

    try:
        next(generator)
    except StopIteration as done:
        frame, matched_image, shape, match = done.value
    else:  # pragma: no cover - the successful first frame must return directly
        raise AssertionError("reference shape unexpectedly yielded")

    assert context.scene_requests == [[34]]
    assert frame == "live-frame"
    assert matched_image is image
    assert shape["title"] == "聊天"
    assert match["similarity"] == 96.0
    assert runner.matches[0][2:] == ("live-frame", "image")


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
