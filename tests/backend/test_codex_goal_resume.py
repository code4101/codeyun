import pytest

from backend.core.codex import app_server


def test_explicit_resume_preserves_goal_and_uses_status_only_contract(monkeypatch):
    before = {'objective': 'existing work', 'status': 'blocked', 'tokensUsed': 123}
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        return {'goal': before if kwargs['method'] == 'thread/goal/get'
                else {**before, 'status': 'active'}}

    monkeypatch.setattr(app_server, '_read_codex_app_server', request)
    result = app_server.resume_codex_thread_goal(
        'same-thread', owner_resume_authorization='owner group seq1391 explicitly resume',
    )
    assert result['goal']['tokensUsed'] == 123
    assert calls[1]['params'] == {'threadId': 'same-thread', 'status': 'active'}


def test_resume_requires_owner_and_never_recreates_missing_or_terminal_goal(monkeypatch):
    with pytest.raises(ValueError, match='授权'):
        app_server.resume_codex_thread_goal('thread', owner_resume_authorization='')
    for goal in (None, {'status': 'complete'}, {'status': 'budgetLimited'}):
        calls = []

        def request(**kwargs):
            calls.append(kwargs)
            return {'goal': goal}

        monkeypatch.setattr(app_server, '_read_codex_app_server', request)
        with pytest.raises(app_server.CodexAppServerError):
            app_server.resume_codex_thread_goal('thread', owner_resume_authorization='explicit')
        assert len(calls) == 1
