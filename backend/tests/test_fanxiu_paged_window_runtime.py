from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.behavior_tree_runtime import BehaviorTreeRuntime


class _FakePagedRuntime:
    def __init__(self, pages: list[str], index: int) -> None:
        self.pages = pages
        self.index = index
        self.moves: list[str] = []
        self.target_shape = SimpleNamespace(
            load_direction="right",
            raw={"loadMode": "paged", "loadInitialPosition": "unknown"},
        )

    def shape(self, _view, _shape):
        return self.target_shape

    def _shape_path(self, _shape):
        return "[活动卡片]"

    def paged_content_snapshot(self, _shape):
        text = self.pages[self.index]
        return {"text": text, "signature": text.encode()}

    def step_paged_content(self, _shape, *, direction):
        self.moves.append(direction)
        before = self.paged_content_snapshot(self.target_shape)
        delta = 1 if direction == "right" else -1
        next_index = min(len(self.pages) - 1, max(0, self.index + delta))
        changed = next_index != self.index
        self.index = next_index
        after = self.paged_content_snapshot(self.target_shape)
        if False:
            yield None
        return {"changed": changed, "before": before, "after": after}

    @staticmethod
    def image_signature_similarity(left: bytes, right: bytes) -> float:
        return 100.0 if left == right else 0.0


def _run(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


def test_unknown_bounded_cursor_rewinds_to_start_before_forward_scan() -> None:
    runtime = _FakePagedRuntime(["过去", "当前", "未来"], index=1)

    found = _run(
        BehaviorTreeRuntime.find_paged_content(
            runtime,
            "ignored",
            lambda page: page["text"] == "未来",
        )
    )

    assert found["text"] == "未来"
    # Unknown + bounded first establishes the real left/start edge, then
    # performs one canonical forward pass.
    assert runtime.moves == ["left", "left", "right", "right"]


def test_find_paged_content_stops_at_bounded_edge() -> None:
    runtime = _FakePagedRuntime(["甲", "乙"], index=0)
    runtime.target_shape.raw.pop("loadInitialPosition")

    found = _run(
        BehaviorTreeRuntime.find_paged_content(
            runtime,
            "ignored",
            lambda page: page["text"] == "不存在",
        )
    )

    assert found is None
    assert runtime.moves == ["right", "right"]


class _FakeCyclicPagedRuntime(_FakePagedRuntime):
    def step_paged_content(self, _shape, *, direction):
        self.moves.append(direction)
        before = self.paged_content_snapshot(self.target_shape)
        delta = 1 if direction == "right" else -1
        self.index = (self.index + delta) % len(self.pages)
        after = self.paged_content_snapshot(self.target_shape)
        if False:
            yield None
        return {"changed": True, "before": before, "after": after}


def test_find_paged_content_detects_cycle_from_repeated_page_signature() -> None:
    runtime = _FakeCyclicPagedRuntime(["甲", "乙", "丙"], index=1)
    runtime.target_shape.raw["loadBoundary"] = "cyclic"

    found = _run(
        BehaviorTreeRuntime.find_paged_content(
            runtime,
            "ignored",
            lambda page: page["text"] == "不存在",
            max_pages=20,
        )
    )

    assert found is None
    assert len(runtime.moves) == 3


def test_similar_card_templates_do_not_count_as_repeated_business_pages() -> None:
    runtime = _FakePagedRuntime(["灵宠竞武", "兽渊探秘", "炼体法相"], index=1)

    def visually_similar(_left: bytes, _right: bytes) -> float:
        return 99.0

    runtime.image_signature_similarity = visually_similar
    found = _run(
        BehaviorTreeRuntime.find_paged_content(
            runtime,
            "ignored",
            lambda page: page["text"] == "炼体法相",
        )
    )

    assert found["text"] == "炼体法相"
    assert runtime.moves == ["left", "left", "right", "right"]
