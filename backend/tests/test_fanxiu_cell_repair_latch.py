"""Cell ownership and terminal contracts; no game or scene simulation."""

import threading
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.behavior_tree.jupyter_kernel import FanxiuJupyterBinding
from backend.core.fanxiu.data_annotation.scene_escalation import SceneRepairRequired


def binding_for_terminal():
    binding = object.__new__(FanxiuJupyterBinding)
    binding.runner = SimpleNamespace(
        _lock=threading.RLock(), _status={}, _stop_event=None,
        _persist_status=lambda: None, _clear_current_task_locked=lambda: None,
        _normalize_task_result=lambda value: ("success", "done"),
    )
    binding.stop_event = threading.Event()
    binding.execution_lock = threading.RLock()
    binding._cell_lock_acquired = False
    binding._managed_task_cell = False
    return binding


@pytest.mark.parametrize("previous_failure", [False, True])
def test_repair_latch_prevents_success_and_same_cell_reentry(previous_failure):
    binding = binding_for_terminal()
    failure = SceneRepairRequired("needs repair", scene_id=63, evidence_frame_path=None)
    calls = []

    def task(*args):
        calls.append(args)
        try:
            binding.runner._scene_repair_error = failure
            raise failure
        except Exception:
            return "success"

    binding.run_task = task
    if previous_failure:
        binding.runner._scene_repair_error = failure
    with pytest.raises(SceneRepairRequired) as caught:
        binding.run_task_cell("test", {
            "__scheduler_task_id": "job", "__scheduler_attempt_id": "attempt",
        })
    assert caught.value is failure
    assert len(calls) == (0 if previous_failure else 1)
    assert binding.runner._status["scheduler_terminal_result"] == "error"
    assert binding.runner._status["scheduler_attempt_id"] == "attempt"


def test_only_new_cell_clears_repair_latch():
    binding = binding_for_terminal()
    failure = SceneRepairRequired("needs repair", scene_id=63, evidence_frame_path=None)
    binding.runner._scene_repair_error = failure
    result = SimpleNamespace(error_in_exec=None, error_before_exec=None)
    binding.end_cell(result)
    assert binding.runner._status["status"] == "error"
    assert binding.runner._scene_repair_error is failure

    binding._refresh_binding = lambda **kwargs: None
    binding.namespace = lambda: {}
    binding.begin_cell(
        SimpleNamespace(raw_cell="# fanxiu:managed-task-cell\npass"),
        SimpleNamespace(user_ns={}),
    )
    assert binding.runner._scene_repair_error is None
    binding.end_cell(result)
    assert not binding._cell_lock_acquired
