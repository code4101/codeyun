from copy import deepcopy

import pytest

from backend.core.fanxiu.activity import xianyuan_gift_receipts as receipts


def inventory_evidence(observed=100):
    return {'pid': 1, 'process_start_ticks': 2, 'requested_item_ids': [37092001],
            'source': 'BackpackMgr.Model.BackpackData.ItemVoDic', 'read_only': True,
            'observed_at': observed, 'completed_at': observed + .1}


def facts(monkeypatch, tmp_path):
    monkeypatch.setattr(receipts, '_path', lambda: tmp_path / 'receipts.json')
    batch = {'action': 'gift', 'npc_id': 4005, 'runtime_index': 350, 'base_id': 37092001,
             'quantity': 80, 'progress_track': 'reset_favor', 'favor_before': 4000, 'expected_favor_after': 12000}
    before = {'runtime_complete': True, 'runtime_updated_at': 100,
              'runtime_debug': {'pid': 1, 'process_start_ticks': 2},
              'people': [{'npc_id': 4005, 'runtime_index': 350, 'reset_favor': 4000}]}
    after = deepcopy(before)
    after['runtime_updated_at'] = 101
    after['people'][0]['reset_favor'] = 12000
    return batch, before, after


def test_unresolved_action_blocks_replay_and_verified_effect_is_idempotent(monkeypatch, tmp_path):
    batch, before, after = facts(monkeypatch, tmp_path)
    row = receipts.begin_xianyuan_gift_action(batch, before, inventory_before=1111, inventory_before_evidence=inventory_evidence())
    with pytest.raises(RuntimeError, match='禁止重复'):
        receipts.begin_xianyuan_gift_action(batch, before, inventory_before=1111, inventory_before_evidence=inventory_evidence())
    settled = receipts.settle_xianyuan_gift_action(row['action_id'], after, inventory_after=1031, inventory_after_evidence=inventory_evidence(101))
    assert settled['effect']['favor_delta'] == 8000
    assert settled['status'] == 'verified'
    assert receipts.settle_xianyuan_gift_action(row['action_id'], {}, inventory_after=0, inventory_after_evidence=inventory_evidence(101)) == settled
    assert len(receipts.read_xianyuan_gift_actions()) == 1


def test_failed_observation_keeps_original_action_pending(monkeypatch, tmp_path):
    batch, before, after = facts(monkeypatch, tmp_path)
    row = receipts.begin_xianyuan_gift_action(batch, before, inventory_before=1111, inventory_before_evidence=inventory_evidence())
    after['people'][0]['reset_favor'] = 4000
    with pytest.raises(RuntimeError, match='禁止重放'):
        receipts.settle_xianyuan_gift_action(row['action_id'], after, inventory_after=1031, inventory_after_evidence=inventory_evidence(101))
    assert receipts.read_xianyuan_gift_actions()[0]['status'] == 'pending'
    with pytest.raises(RuntimeError, match='禁止重复'):
        receipts.begin_xianyuan_gift_action(batch, before, inventory_before=1031, inventory_before_evidence=inventory_evidence())


def test_stale_snapshot_cannot_settle_consumed_stock(monkeypatch, tmp_path):
    batch, before, after = facts(monkeypatch, tmp_path)
    row = receipts.begin_xianyuan_gift_action(batch, before, inventory_before=1111, inventory_before_evidence=inventory_evidence())
    after['runtime_updated_at'] = before['runtime_updated_at']
    with pytest.raises(RuntimeError, match='时间'):
        receipts.settle_xianyuan_gift_action(row['action_id'], after, inventory_after=1031, inventory_after_evidence=inventory_evidence(101))
    assert receipts.read_xianyuan_gift_actions()[0]['status'] == 'pending'
