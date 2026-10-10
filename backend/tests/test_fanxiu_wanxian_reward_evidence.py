"""Real temporary receipt files and formal ownership, no game simulation."""
import json
from pathlib import Path

import pytest

from backend.core.fanxiu.activity.wanxian_reward_evidence import (
    record_wanxian_reward_observation, read_wanxian_reward_observations,
)
from backend.core.fanxiu.data_annotation.subtask_execution import job_log_context

ATTEMPT = 'a' * 32


def write(directory, phase):
    with job_log_context('weekly-wanxian', ATTEMPT):
        return record_wanxian_reward_observation(phase, runtime={},
            frame_path=None, directory=directory)


def test_before_only_keeps_partial_receipt(tmp_path):
    write(tmp_path, 'before')
    value = read_wanxian_reward_observations('weekly-wanxian', ATTEMPT, directory=tmp_path)
    assert value['status'] == 'partial' and set(value['observations']) == {'before'}
    assert value['native_claimed_tier_count'] is None and not value['business_complete']


def test_two_phases_are_observations_not_native_claim_acceptance(tmp_path):
    write(tmp_path, 'before'); write(tmp_path, 'after')
    value = read_wanxian_reward_observations('weekly-wanxian', ATTEMPT, directory=tmp_path)
    assert value['status'] == 'recorded'
    assert value['native_claimed_tier_count'] is None and not value['business_complete']


@pytest.mark.parametrize('field,value', [('attempt_id', 'b' * 32), ('task_id', 'daily-boss'),
    ('phase', 'after'), ('version', 2), ('native_claimed_tier_count', 11),
    ('native_activity_identity_verified', True)])
def test_foreign_or_overclaimed_receipt_invalidates_pair(tmp_path, field, value):
    path = Path(write(tmp_path, 'before'))
    row = json.loads(path.read_text(encoding='utf-8')); row[field] = value
    path.write_text(json.dumps(row), encoding='utf-8')
    result = read_wanxian_reward_observations('weekly-wanxian', ATTEMPT, directory=tmp_path)
    assert result['status'] == 'invalid' and result['invalid_phases'] == ['before']


def test_missing_reader_does_not_create_or_fall_back_to_another_attempt(tmp_path):
    write(tmp_path, 'before')
    assert read_wanxian_reward_observations('weekly-wanxian', 'b' * 32, directory=tmp_path)['status'] == 'missing'
    missing = tmp_path / 'absent'
    assert read_wanxian_reward_observations('weekly-wanxian', ATTEMPT, directory=missing)['status'] == 'missing'
    assert not missing.exists()


def test_no_formal_owner_creates_no_artifact(tmp_path):
    assert record_wanxian_reward_observation('before', runtime={}, frame_path=None, directory=tmp_path) is None
    assert list(tmp_path.iterdir()) == []
