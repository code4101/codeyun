import pytest
from backend.core.runtime.shared_json import read_json, write_json, retry_shared_io


def test_transient_sharing_failure_recovers():
    calls = []
    def action():
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError('sharing')
        return 7
    assert retry_shared_io(action, interval=0) == 7
    assert len(calls) == 3


def test_persistent_failure_does_not_become_empty_queue():
    def action():
        raise PermissionError('access')
    with pytest.raises(PermissionError):
        retry_shared_io(action, attempts=2, interval=0)


def test_atomic_snapshot(tmp_path):
    path = tmp_path / 'queue.json'
    write_json(path, {'jobs': [1]})
    write_json(path, {'jobs': [2]})
    assert read_json(path) == {'jobs': [2]}
    assert not list(tmp_path.glob('*.tmp'))
