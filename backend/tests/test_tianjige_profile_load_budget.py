"""Retry ownership/budget contract; no forum DOM or game is simulated."""
from types import SimpleNamespace

import pytest

from backend.core.fanxiu import tianjige_forum_quiz as forum


def test_empty_load_refreshes_same_tab_once_and_returns_loaded_facts(monkeypatch):
    calls, refreshed = [], []
    tab = SimpleNamespace(refresh=lambda: refreshed.append(True))
    target = object()

    def read(current_tab, day, timeout, **kwargs):
        calls.append((current_tab, day, timeout))
        if len(calls) == 1:
            raise forum.TianjigeForumQuizProfileLoadError("empty first load")
        return target, "today", 10

    monkeypatch.setattr(forum, "_wait_profile_quiz_thread", read)
    assert forum._read_profile_with_reload(tab, "2026-10-22", 15) == (target, "today", 10)
    assert refreshed == [True]
    assert [call[0] for call in calls] == [tab, tab]


def test_second_empty_load_is_error_not_post_absence(monkeypatch):
    refreshed, calls = [], []
    tab = SimpleNamespace(refresh=lambda: refreshed.append(True))

    def empty(*args, **kwargs):
        calls.append(True)
        raise forum.TianjigeForumQuizProfileLoadError("still empty")

    monkeypatch.setattr(forum, "_wait_profile_quiz_thread", empty)
    with pytest.raises(forum.TianjigeForumQuizProfileLoadError):
        forum._read_profile_with_reload(tab, "2026-10-22", 15)
    assert len(calls) == 2
    assert refreshed == [True]


@pytest.mark.parametrize("error", [forum.TianjigeForumQuizError("login required"),
                                   PermissionError("denied"), InterruptedError("cancelled")])
def test_other_errors_never_refresh(monkeypatch, error):
    refreshed = []
    monkeypatch.setattr(forum, "_wait_profile_quiz_thread", lambda *a, **k: (_ for _ in ()).throw(error))
    with pytest.raises(type(error)):
        forum._read_profile_with_reload(SimpleNamespace(refresh=lambda: refreshed.append(True)), "2026-10-22", 15)
    assert refreshed == []


def test_loaded_absence_returns_without_refresh(monkeypatch):
    refreshed = []
    monkeypatch.setattr(forum, "_wait_profile_quiz_thread", lambda *a, **k: (None, "", 10))
    assert forum._read_profile_with_reload(SimpleNamespace(refresh=lambda: refreshed.append(True)), "2026-10-22", 15) == (None, "", 10)
    assert refreshed == []
