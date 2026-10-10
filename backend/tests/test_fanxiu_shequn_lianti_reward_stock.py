"""Real reward token schema and actual settings/Scheduler provider contracts."""
import copy
import json
from datetime import datetime

import pytest
from sqlmodel import Session, create_engine

from backend.models import FanxiuStorageBagItemSetting
from backend.core.fanxiu.storage_bag_settings import set_storage_bag_auto_claim
from backend.core.fanxiu.activity.shequn_lianti import schedule_storage_bag_after_shequn_lianti_claim
from backend.core.fanxiu.instrumentation.shequn_lianti_task_rewards import build_shequn_lianti_claim_reward_items


def observations():
    # Actual ActiveTask 804340165 reward definitions. Only task facts vary.
    before = dict(complete=True, occurrence_key='8043401|1791579605000|1791727200000|8',
                  task_ids=[804340165], claimed_task_ids=[], claimable_task_ids=[804340165],
                  task_reward_tokens={'804340165': ['Item|3020129_1_7', 'Item|15010202_1', 'Item|9020032_3']})
    after = copy.deepcopy(before)
    after.update(claimed_task_ids=[804340165], claimable_task_ids=[])
    return before, after


def test_new_claim_items_exclude_historical_claims():
    before, after = observations()
    result = build_shequn_lianti_claim_reward_items(before, after)
    assert result['claimed_reward_item_ids'] == [3020129, 9020032, 15010202]
    assert result['declared_reward_items'][-1]['quantity'] == 3
    assert build_shequn_lianti_claim_reward_items(after, after)['claimed_reward_item_ids'] == []


@pytest.mark.parametrize('fault', ['period', 'definition', 'unsupported', 'not_claimable', 'foreign_task'])
def test_rejects_unproven_or_changed_reward_identity(fault):
    before, after = observations()
    if fault == 'period':
        after['occurrence_key'] += 'changed'
    elif fault == 'definition':
        after['task_reward_tokens']['804340165'][0] = 'Item|3020129_2_7'
    elif fault == 'unsupported':
        before['task_reward_tokens']['804340165'] = ['Item|3020129_0']
        after['task_reward_tokens']['804340165'] = ['Item|3020129_0']
    elif fault == 'not_claimable':
        before['claimable_task_ids'] = []
    else:
        after['claimed_task_ids'].append(804340160)
    with pytest.raises(ValueError):
        build_shequn_lianti_claim_reward_items(before, after)


@pytest.mark.parametrize('last_result', ['success', 'running', 'error'])
def test_real_selection_and_atomic_schedule_guards(tmp_path, last_result):
    before, after = observations()
    path = tmp_path / 'scheduler_tasks.json'
    task = dict(id='storage-bag-operation', task_type='storage_bag_operation',
                next_time='2026-10-11 00:00:00', last_result=last_result)
    path.write_text(json.dumps([task]), encoding='utf8')
    engine = create_engine('sqlite://')
    FanxiuStorageBagItemSetting.__table__.create(engine)
    with Session(engine) as session:
        # A declared but unselected reward never grants new consumption rights.
        set_storage_bag_auto_claim(session, base_id=3020129, auto_claim=True)
        set_storage_bag_auto_claim(session, base_id=15010202, auto_claim=False)
        session.commit()
        result = schedule_storage_bag_after_shequn_lianti_claim(
            session, before, after, observed_at=datetime(2026, 10, 10, 13), scheduler_state_path=path)
        assert result['matched_item_ids'] == [3020129]
        assert result['woke'] is (last_result == 'success')
        assert json.loads(path.read_text(encoding='utf8'))[0]['next_time'] == (
            '2026-10-10 13:01:00' if last_result == 'success' else task['next_time'])
        set_storage_bag_auto_claim(session, base_id=3020129, auto_claim=False)
        session.commit()
        assert schedule_storage_bag_after_shequn_lianti_claim(
            session, before, after, observed_at=datetime(2026, 10, 10, 13),
            scheduler_state_path=path)['reason'] == 'no_authorized_reward'
