"""Pure native evidence admission and real TEMP completion-write guard."""
import copy
from datetime import datetime
import json

import pytest

from backend.core.fanxiu.activity.xianyuan_result import project_daily_xianyuan_result_evidence as project
from backend.core.fanxiu.data_annotation.tasks.xianyuan_execution import DailyXianyuanTaskMixin


@pytest.fixture
def proof():
    before = {'ok': True, 'available': True, 'complete': True, 'source': 'runtime_memory',
        'protocol': 'QuestMgr.Model.QuestData.taskInfoMap[1]', 'captured_at': '2026-10-11T05:14:40+08:00',
        'task_id': 1008, 'status': 3, 'turn': 0, 'target_turn': 3, 'done': False,
        'evidence': {'pid': 2688, 'process_start_ticks': 5832}}
    after = {**copy.deepcopy(before), 'captured_at': '2026-10-11T05:20:46+08:00',
        'status': 5, 'turn': 3, 'done': True}
    return {'before': before, 'after': after,
        'read_started_at': datetime.fromisoformat('2026-10-11T05:20:46+08:00').timestamp() + 0.4}


@pytest.mark.parametrize('text', ['星辰漫天 焚天剑指 和平 离开 状态232 药品99', '离 开',
    '友好度', '减少', '友好度减少', '友好度减少300 离开', '受到伤害减少30%', '友好度提升', ''])
def test_ocr_never_proves_completion_or_authorizes_popup_click(proof, text):
    proof['after']['done'] = False
    result = project(text, **proof)
    assert not result['business_completion_verified']
    assert not result['result_popup_click_authorized']


def test_fresh_same_process_native_done_proves_daily_effect_without_ocr(proof):
    result = project('', **proof)
    assert result['business_completion_verified'] and result['criterion'] == 'native_task_1008_done'
    assert result['turn'] == 3 and result['target_turn'] == 3
    assert result['process_identity'] == [2688, 5832]
    assert not result['result_popup_click_authorized']


@pytest.mark.parametrize('fault', ['partial', 'missing', 'wrong_task', 'wrong_source', 'wrong_protocol',
    'unknown_identity', 'changed_pid', 'changed_start', 'stale', 'timezone_missing', 'time_missing',
    'reset_day', 'not_done', 'read_time_missing'])
def test_unknown_foreign_stale_or_partial_snapshot_rejects(proof, fault):
    after = proof['after']
    if fault == 'partial': after['complete'] = False
    elif fault == 'missing': proof['after'] = None
    elif fault == 'wrong_task': after['task_id'] = 1009
    elif fault == 'wrong_source': after['source'] = 'supplied_override'
    elif fault == 'wrong_protocol': after['protocol'] = 'other manager'
    elif fault == 'unknown_identity': after['evidence'] = {}
    elif fault == 'changed_pid': after['evidence']['pid'] += 1
    elif fault == 'changed_start': after['evidence']['process_start_ticks'] += 1
    elif fault == 'stale': after['captured_at'] = '2026-10-11T05:20:45+08:00'
    elif fault == 'timezone_missing': after['captured_at'] = '2026-10-11T05:20:46'
    elif fault == 'time_missing': after['captured_at'] = None
    elif fault == 'reset_day': proof['before']['captured_at'] = '2026-10-11T04:59:59+08:00'
    elif fault == 'not_done': after['done'] = False
    else: proof['read_started_at'] = None
    assert not project('友好度减少300 离开', **proof)['business_completion_verified']


def test_unproven_completion_cannot_modify_next_time_even_with_forged_flag(tmp_path, proof):
    path = tmp_path / 'task.json'
    path.write_text('{"next_time":"2026-10-12 05:00"}')
    original = path.read_bytes()
    class Recorder(DailyXianyuanTaskMixin):
        def _xianyuan_evidence(self, *a, **kw): pass
        def _next_daily_boss_reset_time_text(self): return '2026-10-12 05:00'
        def _persist_scheduler_task_next_time(self, task_id, next_time):
            path.write_text(json.dumps({'task_id': task_id, 'next_time': next_time}))
        def _log(self, *a): pass
    for candidate in [None, {'business_completion_verified': True},
        {**proof, 'after': {**proof['after'], 'done': False}}]:
        with pytest.raises(RuntimeError, match='禁止写 next_time'):
            Recorder()._record_daily_xianyuan_done({}, message='挑战流程已完成', completion_evidence=candidate)
        assert path.read_bytes() == original
    Recorder()._record_daily_xianyuan_done({}, message='挑战流程已完成', completion_evidence=proof)
    assert json.loads(path.read_text())['next_time'] == '2026-10-12 05:00'
