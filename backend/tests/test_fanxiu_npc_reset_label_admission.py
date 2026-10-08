"""Token-subsequence OCR must not count one reset control twice."""
import time

import pytest

from backend.core.fanxiu.data_annotation.tasks import xianyuan_gift_gui as gifts
from backend.core.fanxiu.instrumentation import xianyuan_gift_page as page
from backend.core.fanxiu.data_annotation import kernel_scheduler_control as scheduler


def drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as result:
            return result.value


@pytest.mark.parametrize('labels,expected', [
    ({'重置礼物', '重置'}, '重置礼物'),
    ({'重置回礼', '重置'}, '重置回礼'),
    ({'重置'}, '重置'),
    ({'重置礼物', '重置回礼', '重置'}, None),
])
@pytest.mark.parametrize('loading_frames', [0, 2])
@pytest.mark.parametrize('confirmation_modal', [False, True])
def test_full_reset_label_precedes_token_subsequence(monkeypatch, labels, expected, loading_frames, confirmation_modal):
    clicks = []
    discoveries = []
    class Scene(int):
        frame_data_url = 'actual-frame'
    class Context:
        settled = 0
        def wait_scene(self, scenes, **kwargs):
            yield from ()
            return Scene(47 if confirmation_modal and scenes == [47, 762] else 762)
        def wait_click(self, *args, **kwargs):
            yield from ()
        def wait_action_settle(self, *args):
            self.settled += 1
            yield from ()
        def find_ocr_text(self, scene, label, **kwargs):
            if scene == 47:
                assert kwargs['in_shapes'] == ['确认区域']
                return object() if label == '确认' else None
            if self.settled < loading_frames:
                return None
            return object() if label in labels else None
        def ocr_text_in_shapes(self, scene, shapes, **kwargs):
            assert scene == 47
            assert shapes == ['提示正文']
            return '是否重置已获取的礼物，重置后可重新获得这些礼物'
        def cur_frame(self, **kwargs):
            return 'actual-frame'
        def click_ocr_text(self, scene, label, **kwargs):
            if scene == 47:
                assert kwargs['in_shapes'] == ['确认区域']
            clicks.append(label)
    def enter(*args, **kwargs):
        yield from ()
    process = {'pid': 21822, 'process_start_ticks': 12589765}
    person = {'npc_id': 4005, 'favor': 12000, 'favor_level': 12,
              'rewards': [{'reward_key': 3890, 'state': 2}]}
    before = {'people': [person], 'runtime_debug': process}
    after = {'runtime_complete': True, 'runtime_debug': process,
             'people': [{**person, 'reset_favor': 0, 'reset_favor_level': 0,
                         'rewards': [{'reward_key': 3890, 'state': 0}]}]}
    monkeypatch.setattr(gifts, 'open_selected_npc_detail', enter)
    monkeypatch.setattr(gifts, 'require_xianyuan_navigation_budget', lambda **kwargs: None)
    monkeypatch.setattr(gifts, 'read_xianyuan_atlas_runtime', lambda **kwargs: after)
    monkeypatch.setattr(page, 'read_xianyuan_gift_bag_snapshot', lambda: {
        'complete': True, 'npc_id': 4005, 'reset_steps': [1],
        'remaining_reset_rewards': 0, 'reset_cost_quantity': 0,
        'is_enough': True, 'captured_at': time.time(), 'evidence': process,
    })
    monkeypatch.setattr(scheduler, 'record_world_discovery',
                        lambda key, value: discoveries.append(value))
    context = Context()
    operation = gifts.reset_selected_npc_rewards(context,
        target={'npc_id': 4005, 'name': '炎帝萧炎'}, before=before)
    if expected is None:
        with pytest.raises(RuntimeError, match='按钮未唯一识别'):
            drain(operation)
        assert clicks == []
        assert discoveries == []
    else:
        assert drain(operation) is after
        assert clicks == ([expected, '确认'] if confirmation_modal else [expected])
        assert discoveries[0]['reset_cost_quantity'] == 0
    assert context.settled >= loading_frames
