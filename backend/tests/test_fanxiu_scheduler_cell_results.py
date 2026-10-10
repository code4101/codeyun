"""Real isolated persistence and exact-attempt identity; no simulated game."""
import pytest
from backend.core.fanxiu.data_annotation.kernel_scheduler_results import (
    record_scheduler_cell_result, read_scheduler_cell_result,
)


def test_exact_attempt_retains_native_cell_and_distinct_business_result(tmp_path):
    for attempt, remaining in (("a" * 32, 2), ("b" * 32, 0)):
        record_scheduler_cell_result(
            "bag", attempt, cell_result={"msg_id": attempt + "_0", "status": "success"},
            business_result={"outcome": "partial" if remaining else "complete",
                             "remaining_current_items": remaining},
            started_at="start", finished_at="finish", scheduler_result="success",
            directory=tmp_path)
    first = read_scheduler_cell_result("bag", "a" * 32, directory=tmp_path)
    assert first['business_result']['remaining_current_items'] == 2
    assert first['cell_result']['msg_id'] == "a" * 32 + "_0"
    assert read_scheduler_cell_result("bag", "b" * 32, directory=tmp_path)['business_result']['outcome'] == 'complete'
    with pytest.raises(ValueError, match="身份"):
        read_scheduler_cell_result("other", "a" * 32, directory=tmp_path)


def test_missing_receipt_does_not_create_directory_or_use_newer_attempt(tmp_path):
    absent = tmp_path / "absent"
    value = read_scheduler_cell_result("bag", "c" * 32, directory=absent)
    assert value['found'] is False
    assert not absent.exists()
    with pytest.raises(ValueError):
        read_scheduler_cell_result("bag", "../escape", directory=absent)


@pytest.mark.parametrize('field,value', [('scheduler_result','error'), ('started_at','other'), ('finished_at','later')])
def test_terminal_status_and_time_conflicts_do_not_replace_receipt(tmp_path, field, value):
    arguments = dict(cell_result={'status':'success','msg_id':'native-cell'},
                     business_result={'outcome':'partial'}, started_at='start',
                     finished_at='finish', scheduler_result='success', directory=tmp_path)
    record_scheduler_cell_result('bag', 'd'*32, **arguments)
    original = (tmp_path / ('d'*32 + '.json')).read_bytes()
    assert record_scheduler_cell_result('bag', 'd'*32, **arguments)['scheduler_result'] == 'success'
    with pytest.raises(ValueError, match='冲突'):
        record_scheduler_cell_result('bag', 'd'*32, **{**arguments, field:value})
    assert (tmp_path / ('d'*32 + '.json')).read_bytes() == original


def test_real_storage_failure_preserves_committed_native_outcome(tmp_path):
    from backend.core.fanxiu.data_annotation.kernel_scheduler_results import preserve_scheduler_cell_result
    invalid_directory = tmp_path / 'file'
    invalid_directory.write_text('existing file', encoding='utf-8')
    native = {'status':'success','msg_id':'original-cell','result_text':"{'outcome':'partial'}"}
    returned = preserve_scheduler_cell_result(
        'bag', 'e'*32, cell_result=native, business_result={'outcome':'partial'},
        started_at='start', finished_at='finish', scheduler_result='success',
        directory=invalid_directory)
    assert returned['status'] == native['status'] == 'success'
    assert returned['msg_id'] == 'original-cell'
    assert returned['result_text'] == native['result_text']
    assert returned['terminal_receipt_error']
    assert 'terminal_receipt_error' not in native
    assert invalid_directory.read_text(encoding='utf-8') == 'existing file'
