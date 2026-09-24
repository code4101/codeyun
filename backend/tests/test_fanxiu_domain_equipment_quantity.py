from backend.core.fanxiu.data_annotation.tasks import domain_equipment
from backend.core.fanxiu.data_annotation.tasks.domain_equipment import _quantity


def test_material_quantity_uses_shape_crop_when_full_frame_misses_counter():
    class Context:
        def __init__(self):
            self.crops = []

        def cur_frame(self, *, update):
            assert update
            return "frame"

        def ocr_fragments_in_shapes(self, scene, shapes, *, padding, crop, frame_data_url):
            assert (scene, shapes, padding, frame_data_url) == (819, ["材料数量"], 0, "frame")
            self.crops.append(crop)
            return [{"text": "1267/7"}] if crop else []

    context = Context()
    operation = _quantity(context, 819, "材料数量")
    try:
        next(operation)
    except StopIteration as finished:
        assert finished.value == (1267, 7)
    else:
        raise AssertionError("complete counter must not wait for another frame")
    assert context.crops == [False, True]


def test_resume_recast_candidate_compares_scores_before_return(monkeypatch):
    scenes = iter([819, 818, 814])

    def scene(_context, _expected):
        if False:
            yield
        return next(scenes)

    monkeypatch.setattr(domain_equipment, "_scene", scene)
    monkeypatch.setattr(
        domain_equipment, "_score",
        lambda _context, scene_id, shape: {
            (819, "当前评分"): 100,
            (819, "最新评分"): 110,
            (818, "重铸评分"): 110,
        }[(scene_id, shape)],
    )

    class Context:
        def __init__(self):
            self.clicks = []

        def click_shape_center(self, scene_id, shape):
            self.clicks.append((scene_id, shape))

    context = Context()
    operation = domain_equipment.finish_domain_equipment(
        context, equipped=False, recast_required=True,
    )
    try:
        next(operation)
    except StopIteration as finished:
        assert finished.value["scene"] == 814
    else:
        raise AssertionError("higher candidate should complete without another action")
    assert context.clicks == [(819, "保留新属性"), (818, "返回")]
