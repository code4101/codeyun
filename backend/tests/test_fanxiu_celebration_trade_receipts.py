"""Pure atomic intent/receipt contracts; no simulated game or GUI."""
import pytest
from backend.core.fanxiu.activity import celebration_trade_receipts as receipts


def facts():
    identity = dict(pid=5598, process_start_ticks=39960)
    before = dict(complete=True, license_count=14, money_count=50000,
                  total_sell_count=1, finish_window_open=False, evidence=identity)
    settings = dict(complete=True, default_plan_selected=True,
                    selected_trade_count=13, selected_plan_uid=24082878061086673)
    after = dict(complete=True, finish_window_open=True, completed_count=13,
                 requested_count=13, selected_plan_uid=settings['selected_plan_uid'],
                 total_sell_count=14, evidence=identity)
    return before, settings, after


def test_unresolved_intent_blocks_replay_until_exact_settlement(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, 'fanxiu_data_annotation_dir', lambda: tmp_path)
    before, settings, after = facts()
    action = receipts.arm_celebration_trade_batch(8044501400004, before, settings)
    with pytest.raises(RuntimeError):
        receipts.arm_celebration_trade_batch(8044501400004, before, settings)
    with pytest.raises(RuntimeError):
        receipts.settle_celebration_trade_batch(8044501400004, action,
                                               {**after, 'total_sell_count': 13})
    assert receipts.read_celebration_trade_receipts(8044501400004)[0]['status'] == 'armed'
    assert receipts.settle_celebration_trade_batch(8044501400004, action, after)['status'] == 'verified'


def test_process_change_and_unverified_input_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, 'fanxiu_data_annotation_dir', lambda: tmp_path)
    before, settings, after = facts()
    with pytest.raises(ValueError):
        receipts.arm_celebration_trade_batch(8044501400004, before, {**settings, 'complete': False})
    action = receipts.arm_celebration_trade_batch(8044501400004, before, settings)
    with pytest.raises(RuntimeError):
        receipts.settle_celebration_trade_batch(8044501400004, action,
            {**after, 'evidence': dict(pid=5598, process_start_ticks=40000)})
