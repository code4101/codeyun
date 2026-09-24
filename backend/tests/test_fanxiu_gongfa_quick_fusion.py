"""The empty fusion toast is shorter than the normal click/settle path."""

from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.tasks.gongfa_cultivation import quick_fusion


def test_quick_fusion_reads_the_first_frame_after_fast_click():
    class Context:
        def __init__(self):
            self.clicked = False
            self.frames = 0

        def wait_scene(self, scene_ids, **kwargs):
            if False:
                yield
            return SimpleNamespace(scene_id=781)

        def click_shape_center_fast(self, scene_id, title):
            assert (scene_id, title) == (781, "快速融合")
            self.clicked = True

        def cur_frame(self, *, update):
            assert update and self.clicked
            self.frames += 1
            return str(self.frames)

        def ocr_text_in_shapes(self, scene_id, titles, **kwargs):
            assert scene_id == 781 and titles == ["操作反馈"]
            return "暂无可融合功法" if kwargs["frame_data_url"] == "1" else ""

    context = Context()
    generator = quick_fusion(context)
    try:
        next(generator)
    except StopIteration as done:
        assert done.value == {"outcome": "nothing_to_fuse"}
    else:
        raise AssertionError("quick_fusion unexpectedly yielded")
    assert context.frames == 8
