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


def test_dead_target_refresh_requires_verified_dead_target_with_future_end():
    from backend.core.fanxiu.data_annotation.tasks.daily_boss_result import daily_boss_dead_target_refresh_seconds as refresh
    # 08:15:12 detail of attempt 6e67d253: killed target, native endTime 08:27:06.85.
    dead = {'available': True, 'panel_target_identity_verified': True,
            'target': {'isDead': True, 'currHp': 0.0, 'endTime': 1792110426850}}
    assert refresh(dead, now_epoch=1792109712.33) == 715
    alive = {**dead, 'target': {**dead['target'], 'isDead': False}}
    unverified = {**dead, 'panel_target_identity_verified': False}
    for observation in (None, alive, unverified, {**dead, 'target': None},
                        {**dead, 'target': {'isDead': True, 'endTime': None}}):
        assert refresh(observation, now_epoch=1792109712.33) is None
    assert refresh(dead, now_epoch=1792110427.0) is None  # already refreshed
    assert refresh(dead, now_epoch=1792110426.85 - 7200) is None  # outside known refresh bound


def test_challenged_target_alive_matches_exact_unit_in_same_process():
    from backend.core.fanxiu.data_annotation.tasks.daily_boss_result import daily_boss_challenged_target_alive as alive
    evidence = {'pid': 3987, 'process_start_ticks': 18066843}
    unit = {'unitId': 281474975925662, 'bossGroupId': 5006, 'mapId': 500021}
    observation = {'panel_target_identity_verified': True, 'evidence': evidence, 'target': dict(unit, isDead=False)}

    def snapshot(*rows, process=evidence):
        return {'complete': True, 'evidence': dict(process), 'bosses': list(rows)}
    assert alive(observation, snapshot(dict(unit, isDead=False))) is True
    assert alive(observation, snapshot(dict(unit, isDead=True))) is False
    # A respawned unit, another process or an incomplete cache is unknown, never alive.
    assert alive(observation, snapshot(dict(unit, unitId=1, isDead=False))) is None
    assert alive(observation, snapshot(dict(unit, isDead=False), process={'pid': 1, 'process_start_ticks': 2})) is None
    assert alive(observation, dict(snapshot(dict(unit, isDead=False)), complete=False)) is None
    assert alive(dict(observation, panel_target_identity_verified=False), snapshot(dict(unit, isDead=False))) is None
