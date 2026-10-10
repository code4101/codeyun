"""Secondary artifact/error contracts only; no simulated game or navigation."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.core.fanxiu.data_annotation.scene_failure_runtime import capture_scene_failure_runtime
from backend.core.fanxiu.instrumentation import magic_invasion_auto_settings as provider


def test_unrelated_scene_performs_no_read(tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError('unrelated scene must not read Runtime')
    monkeypatch.setattr(provider, 'read_magic_invasion_auto_exit_snapshot', forbidden)
    frame = tmp_path / 'original.png'
    frame.write_bytes(b'opaque original frame')
    assert capture_scene_failure_runtime(scene_id=308, evidence_frame_path=str(frame),
                                         task_id='job', attempt_id='attempt') is None
    assert not frame.with_suffix('.runtime.json').exists()


def test_failed_read_preserves_frame_and_identity(tmp_path, monkeypatch):
    def failed_read():
        raise RuntimeError('secondary decoder failure')
    monkeypatch.setattr(provider, 'read_magic_invasion_auto_exit_snapshot', failed_read)
    frame = tmp_path / 'original.png'
    original = b'opaque original frame'
    frame.write_bytes(original)
    result = capture_scene_failure_runtime(scene_id=698, evidence_frame_path=str(frame),
                                          task_id='job', attempt_id='attempt')
    saved = json.loads(frame.with_suffix('.runtime.json').read_text(encoding='utf-8'))
    assert frame.read_bytes() == original
    assert saved['attempt_id'] == 'attempt' and saved['task_id'] == 'job'
    assert saved['diagnostic_error']['type'] == 'RuntimeError'
    assert saved['same_instant_as_frame'] is False
    assert saved['exit_repair_verified'] is False
    assert result['artifact_path'].endswith('.runtime.json')


def test_missing_frame_does_not_read(tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError('missing original evidence must not read Runtime')
    monkeypatch.setattr(provider, 'read_magic_invasion_auto_exit_snapshot', forbidden)
    assert capture_scene_failure_runtime(scene_id=698, evidence_frame_path=str(tmp_path/'absent.png'),
                                         task_id='job', attempt_id='attempt') is None


@pytest.mark.parametrize('publication_fails', [False, True])
def test_repair_entry_preserves_original_error_and_dispatch(tmp_path, monkeypatch, publication_fails):
    # Invoke the real exception handoff, not recognition/navigation/gameplay.
    from backend.core.fanxiu.data_annotation import game_context as context_module
    from backend.core.fanxiu.data_annotation.scene_escalation import SceneRepairRequired
    frame = tmp_path / 'original.png'
    frame.write_bytes(b'original evidence')
    monkeypatch.setattr(context_module, 'save_scene_diagnostic_frame', lambda *a, **k: str(frame))
    dispatch = SimpleNamespace(dispatch_id='dispatch-original', request_path='request-original')
    monkeypatch.setattr(context_module, 'escalate_scene_repair_required', lambda **k: dispatch)
    def failed_read():
        raise RuntimeError('secondary read failure')
    monkeypatch.setattr(provider, 'read_magic_invasion_auto_exit_snapshot', failed_read)
    if publication_fails:
        original_write = Path.write_text
        def write(path, *a, **k):
            if path.name.endswith('.runtime.json'):
                raise OSError('sidecar publication failure')
            return original_write(path, *a, **k)
        monkeypatch.setattr(Path, 'write_text', write)
    runner = SimpleNamespace(_log=Mock())
    context = context_module.BehaviorTreeContext(runner, {'attrs': {'payload': {
        '__scheduler_task_id': 'job', '__scheduler_attempt_id': 'attempt'}}})
    with pytest.raises(SceneRepairRequired) as raised:
        context.require_scene_repair(698, b'original evidence', reason='original navigation failure',
                                     expected_scene_ids=[34])
    error = raised.value
    assert error.scene_id == 698 and error.evidence_frame_path == str(frame)
    assert error.codex_dispatch_id == 'dispatch-original'
    assert error.codex_request_path == 'request-original'
    assert 'original navigation failure' in str(error)
    assert runner._scene_repair_error is error
    assert error.runtime_observation['diagnostic_error']['type'] == 'RuntimeError'
    assert ('publication_error' in error.runtime_observation) is publication_fails
    assert frame.read_bytes() == b'original evidence'


def test_next_time_entry_never_collects_scene_fault(monkeypatch):
    from backend.core.fanxiu.data_annotation import game_context as context_module
    from backend.core.fanxiu.data_annotation import scene_failure_runtime
    collector = Mock(side_effect=AssertionError('scheduling must not collect a scene fault'))
    monkeypatch.setattr(scene_failure_runtime, 'capture_scene_failure_runtime', collector)
    mark = Mock()
    monkeypatch.setattr(context_module, 'mark_scheduler_next_time_written', mark)
    persist = Mock()
    runner = SimpleNamespace(_persist_scheduler_task_next_time=persist)
    context = context_module.BehaviorTreeContext(runner, {'attrs': {'payload': {'__scheduler_task_id': 'job'}}})
    context.set_next_time('2026-10-12 05:00:00')
    persist.assert_called_once_with('job', '2026-10-12 05:00:00')
    mark.assert_called_once_with('job')
    collector.assert_not_called()
