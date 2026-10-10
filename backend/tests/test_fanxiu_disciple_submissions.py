from pathlib import Path

import pytest

from backend.core.fanxiu.storage_bag_disciple_submissions import (
    arm_disciple_letter_submission,
    complete_disciple_letter_submission,
    list_pending_disciple_letter_submissions,
    mark_disciple_letter_result_observed,
)


def arm(directory: Path):
    return arm_disciple_letter_submission(base_id=20300003, instance_id='native-instance',
                                         before_count=75, submitted_count=1, process_identity=(2688,5832), directory=directory)


def test_read_missing_storage_has_no_side_effect(tmp_path):
    directory = tmp_path / 'absent'
    assert list_pending_disciple_letter_submissions(directory=directory) == []
    assert not directory.exists()


def test_armed_click_blocks_replay_and_unchanged_inventory_does_not_resolve(tmp_path):
    record = arm(tmp_path)
    assert record['phase'] == 'armed'
    with pytest.raises(RuntimeError, match='禁止重新使用'):
        arm(tmp_path)
    with pytest.raises(RuntimeError, match='禁止重放'):
        complete_disciple_letter_submission(record['action_id'], remaining_count=75,
                                           travel_closed=True, process_identity=(2688,5832), directory=tmp_path)
    assert list_pending_disciple_letter_submissions(directory=tmp_path) == [record]


def test_exact_delta_requires_native_closure_and_completion_is_idempotent(tmp_path):
    record = arm(tmp_path)
    with pytest.raises(ValueError, match='同游已关闭'):
        complete_disciple_letter_submission(record['action_id'], remaining_count=74,
                                           travel_closed=False, process_identity=(2688,5832), directory=tmp_path)
    assert list_pending_disciple_letter_submissions(directory=tmp_path) == [record]
    with pytest.raises(RuntimeError, match='尚无原生完成观察'):
        complete_disciple_letter_submission(record['action_id'], remaining_count=74,
                                           travel_closed=True, process_identity=(2688,5832), directory=tmp_path)
    mark_disciple_letter_result_observed(record['action_id'], directory=tmp_path)
    verified = complete_disciple_letter_submission(record['action_id'], remaining_count=74,
                                                  travel_closed=True, process_identity=(2688,5832), directory=tmp_path)
    assert verified['phase'] == 'verified'
    assert list_pending_disciple_letter_submissions(directory=tmp_path) == []
    assert complete_disciple_letter_submission(record['action_id'], remaining_count=74,
                                              travel_closed=True, process_identity=(2688,5832), directory=tmp_path) == verified
    with pytest.raises(RuntimeError):
        complete_disciple_letter_submission(record['action_id'], remaining_count=73,
                                           travel_closed=True, process_identity=(2688,5832), directory=tmp_path)
    assert list_pending_disciple_letter_submissions(directory=tmp_path) == []
    assert arm(tmp_path)['action_id'] != record['action_id']


@pytest.mark.parametrize('quantity', [0, -1, True, 76])
def test_invalid_quantities_never_create_submission(tmp_path, quantity):
    with pytest.raises(ValueError):
        arm_disciple_letter_submission(base_id=20300003, instance_id='native-instance',
                                      before_count=75, submitted_count=quantity, process_identity=(2688,5832), directory=tmp_path)
    assert list_pending_disciple_letter_submissions(directory=tmp_path) == []


def test_result_observation_survives_reload_before_accounting(tmp_path):
    record = arm(tmp_path)
    observed = mark_disciple_letter_result_observed(record['action_id'], directory=tmp_path)
    assert observed['phase'] == 'result_observed'
    assert list_pending_disciple_letter_submissions(directory=tmp_path) == [observed]
    with pytest.raises(RuntimeError, match='游戏进程已变化'):
        complete_disciple_letter_submission(record['action_id'], remaining_count=74,
                                           travel_closed=True, process_identity=(2689,5832), directory=tmp_path)
    assert list_pending_disciple_letter_submissions(directory=tmp_path) == [observed]
    completed = complete_disciple_letter_submission(record['action_id'], remaining_count=74,
                                                    travel_closed=True, process_identity=(2688,5832), directory=tmp_path)
    assert completed['phase'] == 'verified'


@pytest.mark.parametrize('landing', [525, 34])
@pytest.mark.parametrize('failure', [None, 'incomplete', 'process_changed'])
def test_reconcile_observed_result_after_close_uses_ledger_and_inventory(
    tmp_path, monkeypatch, landing, failure,
):
    """Exercise the uncertainty barrier at API boundaries, with no game model."""
    from backend.core.fanxiu.data_annotation.tasks import storage_bag_disciple_letter as letters
    record = arm(tmp_path)
    mark_disciple_letter_result_observed(record['action_id'], directory=tmp_path)
    monkeypatch.setattr(letters, 'list_pending_disciple_letter_submissions',
                        lambda: list_pending_disciple_letter_submissions(directory=tmp_path))
    monkeypatch.setattr(letters, 'complete_disciple_letter_submission',
                        lambda *args, **kwargs: complete_disciple_letter_submission(*args, directory=tmp_path, **kwargs))
    def forbidden(*args, **kwargs):
        pytest.fail('已观察且关闭的结果不得重新等待同游或提交使用')
    monkeypatch.setattr(letters, 'finish_existing_disciple_travel', forbidden)
    class Boundary:
        navigations = []
        def wait_scene_exact(self, targets, **kwargs):
            assert targets == [936, 525, 34]
            return landing
            yield
        def go_scene(self, target):
            self.navigations.append(target)
            yield
        wait_click = forbidden
    context = Boundary()
    snapshot = {'complete': failure != 'incomplete',
                'evidence': {'pid': 2689 if failure == 'process_changed' else 2688,
                             'process_start_ticks': 5832},
                'items': [{'instance_id': 'native-instance', 'base_id': 20300003, 'num': 74}]}
    action = letters.reconcile_pending_disciple_letters(context, lambda: snapshot)
    if failure:
        with pytest.raises(RuntimeError):
            list(action)
        assert list_pending_disciple_letter_submissions(directory=tmp_path)[0]['phase'] == 'result_observed'
    else:
        list(action)
        assert list_pending_disciple_letter_submissions(directory=tmp_path) == []
    assert context.navigations == ([525] if landing == 34 else [])
