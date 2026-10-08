from copy import deepcopy

import pytest

from backend.core.fanxiu.data_annotation.tasks import lingchong_round_rewards as rewards
from backend.core.fanxiu.data_annotation import kernel_scheduler_control as scheduler


@pytest.mark.parametrize(('field', 'changed'), [
    ('box_config_id', 604290104),
    ('box_state_type', 1),
    ('current_round', 4),
])
def test_box_changed_during_provider_wait_cannot_be_clicked(monkeypatch, field, changed):
    page = {'complete': True, 'activity_id': 16042901, 'tab_index': 2,
            'sub_tab_index': 999, 'current_round': 3, 'total_rounds': 4,
            'box_config_id': 604290103, 'box_state_type': 2}
    facts = {'all_claimed': False, 'current_round': 3, 'current_tasks': [],
             'rounds': [{}, {}, {'box_id': 604290103, 'tasks_claimed': True, 'box_claimable': True}, {}]}
    clicks = []

    class Context:
        def wait_scene(self, *_args, **_kwargs):
            if False:
                yield
            return 940

        def wait_click(self, scene, shape, *, before_click=None):
            yield 'provider_wait'
            page[field] = changed
            assert callable(before_click)
            before_click()
            clicks.append((scene, shape))

    monkeypatch.setattr(rewards, 'read_lingchong_round_rewards', lambda _: deepcopy(facts))
    monkeypatch.setattr(rewards, 'read_activity_rank_task_page_snapshot', lambda: deepcopy(page))
    monkeypatch.setattr(scheduler, 'read_scheduler_research_batch_admission', lambda **_: {'admitted': True})
    task = rewards.claim_loaded_round_rewards(Context(), activity_id=16042901)
    assert next(task) == 'provider_wait'
    with pytest.raises(RuntimeError, match='轮次|目标'):
        next(task)
    assert clicks == []
