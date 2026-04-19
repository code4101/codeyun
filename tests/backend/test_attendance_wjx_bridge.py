import pytest

from backend.core import attendance_wjx
from pyxllib.ext import wjxlib


def test_attendance_wjx_bridge_reexports_shared_symbols():
    assert attendance_wjx.execute_wjx_template_action is wjxlib.execute_wjx_template_action
    assert attendance_wjx.ensure_logged_in is wjxlib.ensure_logged_in
    assert attendance_wjx.read_course_options is wjxlib.read_course_options
    assert attendance_wjx.WjxAutomationError is wjxlib.WjxAutomationError


def test_resolve_wjx_credentials_prefers_explicit_values(monkeypatch):
    monkeypatch.setenv("WJX_USERNAME", "env-user")
    monkeypatch.setenv("WJX_PASSWORD", "env-pass")

    assert wjxlib.resolve_wjx_credentials("arg-user", "arg-pass") == ("arg-user", "arg-pass")


def test_resolve_wjx_credentials_uses_env_fallback(monkeypatch):
    monkeypatch.setenv("WJX_USERNAME", "env-user")
    monkeypatch.setenv("WJX_PASSWORD", "env-pass")

    assert wjxlib.resolve_wjx_credentials() == ("env-user", "env-pass")


def test_resolve_wjx_credentials_requires_values(monkeypatch):
    monkeypatch.delenv("WJX_USERNAME", raising=False)
    monkeypatch.delenv("WJX_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="WJX_USERNAME / WJX_PASSWORD"):
        wjxlib.resolve_wjx_credentials()
