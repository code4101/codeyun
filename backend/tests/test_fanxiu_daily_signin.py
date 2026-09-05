from pathlib import Path
import threading

import pytest

from backend.core.fanxiu.data_annotation.tasks import daily_signin as signin_module
from backend.core.fanxiu.data_annotation.tasks.daily_signin import DailySigninTaskMixin


class _StopAfterWorldEntry(RuntimeError):
    pass


class _Runner(DailySigninTaskMixin):
    def __init__(self):
        self.context = object()

    def _behavior_tree_context(self, ctx, asset_tree_path, *, stop_event):
        assert asset_tree_path == Path("assets.json")
        assert stop_event is not None
        return self.context


def test_daily_signin_reuses_standard_world_activity_entry(monkeypatch):
    observed = {}

    def fake_open(context, target, **kwargs):
        observed["context"] = context
        observed["target"] = target
        observed.update(kwargs)
        if False:
            yield None
        raise _StopAfterWorldEntry

    monkeypatch.setattr(signin_module, "open_loaded_activity_menu_item", fake_open)
    runner = _Runner()
    task = runner._execute_daily_signin_task(
        {"asset_tree_path": Path("assets.json")},
        threading.Event(),
    )

    with pytest.raises(_StopAfterWorldEntry):
        next(task)

    assert observed["context"] is runner.context
    assert observed["target"] == "group:110001"
    assert observed["kind"] == "world_left"
    assert observed["source_scene_id"] == 34
    assert observed["ocr_shape_names"] == ("左侧菜单",)
    assert observed["expected_scene_ids"] == (403,)
    assert observed["target_gui_name"] == "特惠"
    assert observed["grid"].columns == 1
    assert observed["grid"].click_offset_heights == 0.5
