"""Pure window/participation policy, without simulating game interaction."""
import pytest

from backend.core.fanxiu.data_annotation.festival_rain_state import classify_festival_rain_state


@pytest.mark.parametrize('now,answer,state,expected', [
    (99, 0, 2, False), (100, 0, 2, True), (199, 0, 2, True),
    (200, 0, 2, False), (150, 1, 2, False), (150, None, 2, False),
    (150, False, 2, False), (150, 0, 1, False), (150, 0, 3, False),
])
def test_window_and_server_participation(now, answer, state, expected):
    snapshot = {'ok': True, 'complete': True, 'answer': answer, 'activity': {
        'baseId': 190000, 'state': state, 'startTime': 100, 'endTime': 200,
    }}
    assert classify_festival_rain_state(snapshot, now_ms=now)['due_task_ids'] == (
        ['daily-redpacket'] if expected else []
    )


@pytest.mark.parametrize('snapshot', [{}, {'ok': True, 'complete': True, 'activity': None},
    {'ok': False, 'complete': False, 'answer': 0},
    {'ok': True, 'complete': True, 'answer': 0, 'activity': {'baseId': 190000, 'state': 2}},
])
def test_absent_or_incomplete_activity_never_triggers(snapshot):
    assert classify_festival_rain_state(snapshot, now_ms=150)['due_task_ids'] == []
