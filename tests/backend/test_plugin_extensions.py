from types import SimpleNamespace

import pytest

from backend.plugins import extensions


def test_no_plugins_have_no_policies(monkeypatch):
    monkeypatch.setattr(extensions, "_extensions", lambda: ())
    assert list(extensions.plugin_values("permanent_media_root_names")) == []


def test_hook_values_are_composed_and_policy_errors_propagate(monkeypatch):
    def broken():
        raise RuntimeError("policy unavailable")
    monkeypatch.setattr(extensions, "_extensions", lambda: (
        SimpleNamespace(roots=lambda: {"archive"}), SimpleNamespace(roots=lambda: {"library"}),
    ))
    assert list(extensions.plugin_values("roots")) == [{"archive"}, {"library"}]
    monkeypatch.setattr(extensions, "_extensions", lambda: (SimpleNamespace(roots=broken),))
    with pytest.raises(RuntimeError, match="policy unavailable"):
        list(extensions.plugin_values("roots"))
