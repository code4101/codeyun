"""Traversal contracts for sparse text changes hidden by image similarity.

These observations isolate search/endpoint logic; they do not simulate a game
Task or assert that a gesture succeeds on a device.
"""

from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.behavior_tree_executor import BehaviorTreeContext


def run_search(pages, *, target="林银屏", limit=4):
    position = 0
    gestures = []
    shape = SimpleNamespace(load_direction="down", title="窗口")

    def frame(**_kwargs):
        return str(position)

    def find(_view, wanted, *, frame_data_url, **_kwargs):
        return wanted if wanted in pages[int(frame_data_url)] else None

    def scroll(_shape, *, direction):
        nonlocal position
        gestures.append(direction)
        position = max(0, min(len(pages) - 1, position + (1 if direction == "down" else -1)))
        if False:
            yield
        # Real incident: changed=False at 96.9167% despite changed names.
        return False

    context = SimpleNamespace(
        view=lambda _view: object(),
        resolve_shape_selector=lambda *_args: shape,
        cur_frame=frame,
        find_ocr_text=find,
        scroll_shape_content=scroll,
        ocr_fragments_in_shapes=lambda *_args, frame_data_url, **_kwargs: [
            {"text": name} for name in pages[int(frame_data_url)]
        ],
    )
    search = BehaviorTreeContext.wait_ocr_any_text(
        context, 435, (target,), in_shapes=("窗口",),
        max_scrolls_per_direction=limit, timeout_seconds=30,
    )
    while True:
        try:
            next(search)
        except StopIteration as result:
            return result.value, gestures


def test_false_visual_change_still_searches_last_post_scroll_frame():
    found, gestures = run_search([
        ["冰凤", "美杜莎", "凌玉灵"],
        ["凌玉灵", "林银屏", "慕沛灵", "曲魂"],
    ], limit=1)
    assert found == "林银屏"
    assert gestures == ["down"]


def test_new_text_continues_past_false_visual_boundary():
    found, gestures = run_search([["甲"], ["乙"], ["丙"], ["林银屏"]])
    assert found == "林银屏"
    assert gestures == ["down"] * 3


def test_empty_ocr_cannot_end_search_and_direction_limits_remain_bounded():
    found, gestures = run_search([[]], limit=3)
    assert found is None
    assert gestures == ["down"] * 3 + ["up"] * 3


def test_nonempty_unchanged_boundary_requires_two_observations_per_direction():
    found, gestures = run_search([["甲"]], limit=8)
    assert found is None
    assert gestures == ["down", "down", "up", "up"]
