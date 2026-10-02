from backend.core.fanxiu.instrumentation import tiandi_yiju_task_rewards as rewards
import pytest

@pytest.fixture
def stage_rows(monkeypatch):
    rows = tuple({'id': task_id, 'activityId': stage_id, 'type': 3,
                  'subType': subtype, 'sort': sort}
                 for stage_id, first in [(16090002, 400901), (16090003, 401001)]
                 for task_id, subtype, sort in [(first, 6, 20010002), (first + 9, 7, 20010011)])
    monkeypatch.setattr(rewards, '_active_task_rows', lambda: rows)
    rewards._static_task_index.cache_clear()
    yield
    rewards._static_task_index.cache_clear()

def entries(first):
    return [{'taskId': i, 'status': 4, 'turn': 1, 'rewardTime': 0,
             'progressList': [{'finish': True, 'progress': 200, 'target': 200}]}
            for i in (first, first + 9)]

@pytest.mark.parametrize('first,stage', [(400901,16090002),(401001,16090003)])
def test_parent_selects_the_single_complete_live_stage(stage_rows, first, stage):
    result = rewards.build_tiandi_yiju_task_reward_snapshot(
        activity_id=16090004, task_entries=entries(first), finished_task_ids=[])
    assert result['complete']
    assert result['task_activity_id'] == stage
    assert result['authorized_claim_task_ids'] == [first, first + 9]

def test_parent_rejects_two_complete_sibling_stages(stage_rows):
    result = rewards.build_tiandi_yiju_task_reward_snapshot(
        activity_id=16090004, task_entries=entries(400901)+entries(401001), finished_task_ids=[])
    assert result['state'] == 'ambiguous'
    assert result['authorized_claim_task_ids'] == []

def test_parent_never_combines_partial_sibling_ladders(stage_rows):
    result = rewards.build_tiandi_yiju_task_reward_snapshot(
        activity_id=16090004, task_entries=entries(400901)[:1]+entries(401001)[1:], finished_task_ids=[])
    assert not result['complete']
    assert result['authorized_claim_task_ids'] == []
