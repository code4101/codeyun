"""只验证锁账本的确定性状态转换，不模拟 Runtime、场景或点击。"""

import pytest

from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_lock_state import SpiritArtifactLockState


def state():
    return SpiritArtifactLockState({
        'is_wash': True, 'pid': 10, 'process_start_ticks': 20, 'item_id': 'part-1-4',
        'effects': [
            {'row': 0, 'cleanse_id': 101, 'value': 7, 'quality': 2, 'locked': True},
            {'row': 1, 'cleanse_id': 202, 'value': 8, 'quality': 3, 'locked': False},
        ],
    }, attempt_id='attempt-1', kernel_generation=1)


def test_initial_locks_and_idempotent_set_are_preserved():
    ledger = state()
    assert ledger.begin_change(101, True) is None
    assert ledger.begin_change(202, False) is None
    assert ledger.changes({101}) == ()
    changes = ledger.changes({202})
    assert [(change.effect.cleanse_id, change.locked) for change in changes] == [(101, False), (202, True)]


def test_only_confirmed_toggle_changes_one_lock():
    ledger = state()
    before = ledger.rows
    change = ledger.begin_change(202, True)
    with pytest.raises(ValueError, match='失效'):
        _ = ledger.rows
    ledger.confirm_change(change, observed_locked=True)
    assert ledger.rows[0] == before[0]
    assert (ledger.rows[1].cleanse_id, ledger.rows[1].value, ledger.rows[1].quality) == (202, 8, 3)
    assert ledger.rows[1].locked is True
    assert ledger.begin_change(202, True) is None
    unlock = ledger.begin_change(202, False)
    ledger.confirm_change(unlock, observed_locked=False)
    assert ledger.rows == before


@pytest.mark.parametrize('observed', [None, False, 1])
def test_ambiguous_or_mismatched_ack_requires_new_runtime(observed):
    ledger = state()
    change = ledger.begin_change(202, True)
    with pytest.raises(ValueError, match='不明确'):
        ledger.confirm_change(change, observed_locked=observed)
    with pytest.raises(ValueError, match='失效'):
        ledger.begin_change(101, False)


def test_external_invalidation_or_exit_rejects_late_ack():
    ledger = state()
    change = ledger.begin_change(202, True)
    ledger.invalidate()
    with pytest.raises(ValueError, match='不明确'):
        ledger.confirm_change(change, observed_locked=True)


def test_other_session_ack_cannot_be_reused():
    left, right = state(), state()
    foreign = left.begin_change(202, True)
    right.begin_change(202, True)
    with pytest.raises(ValueError, match='不明确'):
        right.confirm_change(foreign, observed_locked=True)


def test_unknown_attribute_does_not_invalidate_valid_ledger():
    ledger = state()
    with pytest.raises(ValueError, match='不存在'):
        ledger.changes({999})
    assert ledger.rows[0].locked is True
