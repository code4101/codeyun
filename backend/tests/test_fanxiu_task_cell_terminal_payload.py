"""Pure terminal projection, independent of game scenes or a live Kernel."""
from backend.core.fanxiu.behavior_tree.jupyter_kernel import build_task_cell_terminal


def test_partial_business_fields_survive_both_wrappers_without_status_override():
    business = {'result':'hold','outcome':'partial','remaining_current_items':2,
                'unresolved_items':[], 'yield_for_due_job':{'retry_at':'2026-10-10T17:47:19+08:00'}}
    inner = build_task_cell_terminal('success', 'returned normally', business)
    outer = build_task_cell_terminal(inner['result'], inner['message'], inner['business_result'])
    assert outer['result'] == 'success'
    assert outer['business_result'] == business
    assert business['result'] == 'hold'


def test_nonfinite_business_evidence_is_explicit_without_retrying_success():
    terminal = build_task_cell_terminal('success', 'completed', {'score':float('nan')})
    assert terminal['result'] == 'success'
    assert 'business_result' not in terminal
    assert terminal['business_result_error']
