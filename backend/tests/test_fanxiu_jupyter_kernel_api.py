from __future__ import annotations

import importlib
from pathlib import Path

from backend.core.fanxiu.data_annotation import runtime_framework
from backend.core.fanxiu.runtime.kernel import FanxiuKernel


def test_task_is_only_an_ordinary_cell_constructor() -> None:
    cell = FanxiuKernel(entry_id="entry").task("detect_scene", {"probe": True})

    assert cell.code == "run_task_cell('detect_scene', {'probe': True})"
    assert not hasattr(cell, "submit")


def test_runtime_framework_task_uses_kernel_cell_path(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class Cell:
        def run(self, *, timeout_seconds):
            calls.append(("run", timeout_seconds))
            return {"status": "success"}

    class Kernel:
        def __init__(self, *, entry_id):
            calls.append(("kernel", entry_id))

        def task(self, task_type, payload, *, timeout_seconds):
            calls.append(("task", (task_type, payload, timeout_seconds)))
            return Cell()

    kernel_module = importlib.import_module("backend.core.fanxiu.runtime.kernel")
    monkeypatch.setattr(kernel_module, "FanxiuKernel", Kernel)
    result = runtime_framework.submit_task_cell(
        entry=object(),
        entry_id="entry",
        task_type="detect_scene",
        payload={"max_runtime_seconds": 40},
    )

    assert result == {"status": "success"}
    assert calls == [
        ("kernel", "entry"),
        ("task", ("detect_scene", {"max_runtime_seconds": 40}, 70.0)),
        ("run", 70.0),
    ]


def test_active_kernel_path_has_no_manual_queue_or_kernel_lock() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = [
        root / "backend/core/fanxiu/runtime/kernel.py",
        root / "backend/core/fanxiu/runtime/jupyter_kernel.py",
        root / "backend/core/fanxiu/data_annotation/runtime_framework.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    for forbidden in ("manual_jobs", "claim", "requeue", "dedupe", "job_group_isolation"):
        assert forbidden not in source


def test_kernel_status_keeps_kernel_and_business_runtime_orthogonal(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.runtime.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle"},
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.runtime.behavior_tree.fanxiu_data_annotation_runtime_status",
        lambda: {"status": "success", "current_task": "demo"},
    )

    assert FanxiuKernel().status() == {
        "kernel": {"alive": True, "execution_state": "idle"},
        "runtime": {"status": "success", "current_task": "demo"},
    }
