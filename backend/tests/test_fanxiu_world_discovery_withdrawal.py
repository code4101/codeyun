import pytest

from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
    read_world_facts, record_world_discovery, withdraw_world_discoveries,
)


def test_exact_withdrawal_preserves_other_facts_and_audits_evidence(tmp_path):
    path = tmp_path / 'world.json'
    erroneous = {'attempt_id': 'test-attempt', 'error': None}
    record_world_discovery('test', erroneous, path=path)
    record_world_discovery('business', {'verified': True}, path=path)
    before = read_world_facts(path)
    result = withdraw_world_discoveries({'test': erroneous}, reason='isolated test', path=path)
    after = read_world_facts(path)
    assert result['withdrawn'] == ['test']
    assert after['discoveries'] == {
        key: value for key, value in before['discoveries'].items() if key != 'test'
    }
    assert {key: value for key, value in after.items()
            if key not in {'discoveries', 'events', 'updated_at'}} == {
        key: value for key, value in before.items()
        if key not in {'discoveries', 'events', 'updated_at'}
    }
    assert 'test-attempt' in str(after['events'])


@pytest.mark.parametrize('second', ['missing', 'changed'])
def test_withdrawal_rejects_entire_batch_on_compare_conflict(tmp_path, second):
    path = tmp_path / 'world.json'
    record_world_discovery('first', {'value': 1}, path=path)
    record_world_discovery('second', {'value': 2}, path=path)
    before = read_world_facts(path)
    expected = {'first': {'value': 1}, second: {'value': 3}}
    with pytest.raises(ValueError, match='拒绝整批'):
        withdraw_world_discoveries(expected, reason='conflict test', path=path)
    assert read_world_facts(path) == before
