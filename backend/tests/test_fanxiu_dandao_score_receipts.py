import pytest
from backend.core.fanxiu.activity import dandao_score_receipts as receipts


def facts(score=812400, a=11323, b=13893):
    return dict(score=score, materials={9031021:a, 9031022:b},
                recipe_id=711070, formula='AnyItem|12_9031021_9031022',
                evidence=dict(pid=5598, process_start_ticks=39960))


def test_pending_intent_blocks_replay_and_exact_material_score_settles(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, 'fanxiu_data_annotation_dir', lambda:tmp_path)
    intent = receipts.arm_dandao_score_batch(16043101, facts(), 12)
    with pytest.raises(RuntimeError, match='未结'):
        receipts.arm_dandao_score_batch(16043101, facts(), 12)
    result = receipts.settle_dandao_score_batch(16043101, intent, facts(1172400, 11179))
    assert result['status'] == 'verified'
    assert receipts.read_dandao_score_receipts(16043101)[0]['after']['score'] == 1172400


def test_score_alone_cannot_settle_wrong_material_loss(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, 'fanxiu_data_annotation_dir', lambda:tmp_path)
    intent = receipts.arm_dandao_score_batch(16043101, facts(), 12)
    with pytest.raises(RuntimeError, match='禁止重放'):
        receipts.settle_dandao_score_batch(16043101, intent, facts(1172400, 11322))
    assert receipts.read_dandao_score_receipts(16043101)[0]['status'] == 'armed'


def test_final_capped_progress_needs_all_frozen_milestones(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, 'fanxiu_data_annotation_dir', lambda:tmp_path)
    ids = list(range(614310101, 614310141))
    before = facts(35612400, 10000, 0)
    before.update(final_threshold=48225400, task_ids=ids)
    intent = receipts.arm_dandao_score_batch(16043101, before, 421)
    after = facts(None, 4948, 0)
    after['final_threshold_proof'] = dict(complete=True, threshold=48225400,
                                         task_ids=ids, satisfied_task_ids=ids[:-1])
    with pytest.raises(RuntimeError, match='禁止重放'):
        receipts.settle_dandao_score_batch(16043101, intent, after)
    after['final_threshold_proof']['satisfied_task_ids'] = ids
    result = receipts.settle_dandao_score_batch(16043101, intent, after)
    assert result['score_verification'] == 'final_threshold_lower_bound'
    assert result['after']['score'] is None
