from __future__ import annotations

import threading

import pytest

from backend.core.fanxiu.data_annotation.tasks import login_game


class _UnknownContext:
    def current_scene(self, _scene_ids, *, update=True):
        return None, 0.0, "frame"

    def ocr_text(self, _frame):
        return "完全无关的未知画面"

    def match_view(self, _scene_id, *, frame_data_url):
        return False, 0.0, frame_data_url

    def shape_matches(self, _scene_id, _shape_name, *, frame_data_url):
        return None


class _LoginHarness(login_game.LoginGameTaskMixin):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.context = _UnknownContext()

    def _raise_if_stopped(self, _stop_event):
        return None

    def _behavior_tree_context(self, *_args, **_kwargs):
        return self.context

    def _set_status_locked(self, *_args, **_kwargs):
        return None


def test_login_unknown_frame_fails_closed_without_restarting_mumu(monkeypatch):
    recoveries: list[dict] = []
    monkeypatch.setattr(
        login_game,
        "mumu_device_health_check",
        lambda **_kwargs: {"status": "healthy"},
    )
    monkeypatch.setattr(
        login_game,
        "recover_mumu_device",
        lambda **kwargs: recoveries.append(kwargs) or {"status": "healthy"},
    )

    execution = _LoginHarness()._execute_login_game_task(
        {},
        threading.Event(),
        {"loading_timeout_seconds": 1},
    )
    with pytest.raises(RuntimeError, match="拒绝点击或重启模拟器"):
        next(execution)

    assert recoveries == []
