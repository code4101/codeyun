from backend.core.fanxiu.data_annotation.tasks.daily_boss_result import daily_boss_world_return_status
from backend.core.fanxiu.data_annotation.tasks.daily_boss_result import (
    read_daily_boss_target_observations, record_daily_boss_target_observation,
)
from backend.core.fanxiu.data_annotation.subtask_execution import job_log_context
from backend.core.messaging.wechat_agent_store import AgentStore


def test_successful_navigation_preserves_cd_reason_and_confirmed_scene():
    status = {'message': '确认世界', 'current_scene': 34, 'phase': 'world_confirmed', 'status': 'running'}
    value = daily_boss_world_return_status(status, business_message='注视目标刷新CD，剩余1次，下次07:20', navigation_result='success')
    assert value['message'] == value['business_message'] == '注视目标刷新CD，剩余1次，下次07:20'
    assert value['current_scene'] == 34 and value['phase'] == 'world_confirmed'
    assert value['status'] == 'running'  # no conversion to all-rewards complete
    assert status['message'] == '确认世界'


def test_failed_or_skipped_navigation_does_not_hide_its_failure():
    status = {'message': '未确认地图出口', 'current_scene': None, 'status': 'error'}
    for result in ('error', 'skipped'):
        assert daily_boss_world_return_status(status, business_message='尚有1次奖励', navigation_result=result) == status


def test_empty_reason_and_nested_cleanup_preserve_actual_projection():
    status = {'message': '原生奖励剩1次，稍后复查', 'current_scene': 34}
    assert daily_boss_world_return_status(status, business_message='', navigation_result='success') == status
    first = daily_boss_world_return_status(status, business_message=status['message'], navigation_result='success')
    second = daily_boss_world_return_status(dict(first, message='确认世界'), business_message=first['message'], navigation_result='success')
    assert second['business_message'] == first['business_message']


def test_real_store_target_receipts_are_exact_owned_and_do_not_claim_rewards(tmp_path):
    store = AgentStore(tmp_path / 'receipts.sqlite')
    attempt = 'a' * 32
    assert record_daily_boss_target_observation({}, store=store) is None
    with job_log_context('daily-boss', attempt):
        record_daily_boss_target_observation({}, store=store)
        record_daily_boss_target_observation({}, store=store)
    value = read_daily_boss_target_observations('daily-boss', attempt, store=store)
    assert value['status'] == 'recorded' and len(value['observations']) == 2
    assert not value['reward_credit_verified'] and not value['business_complete']
    assert read_daily_boss_target_observations('daily-boss', 'b' * 32, store=store)['status'] == 'missing'


def test_foreign_target_receipt_is_invalid_without_game_read(tmp_path):
    store = AgentStore(tmp_path / 'receipts.sqlite')
    with job_log_context('daily-boss', 'a' * 32):
        key = record_daily_boss_target_observation({}, store=store)
    row = store.get_meta(key); row['attempt_id'] = 'b' * 32; store.set_meta(key, row)
    assert read_daily_boss_target_observations('daily-boss', 'a' * 32, store=store)['status'] == 'invalid'


def test_malformed_target_observation_is_not_read_as_native_evidence(tmp_path):
    store = AgentStore(tmp_path / 'receipts.sqlite')
    with job_log_context('daily-boss', 'a' * 32):
        key = record_daily_boss_target_observation({}, store=store)
    row = store.get_meta(key); row['observations'] = ['not-a-native-observation']; store.set_meta(key, row)
    assert read_daily_boss_target_observations('daily-boss', 'a' * 32, store=store)['status'] == 'invalid'
