"""list_directory pacing: every real navigation (get, refresh) has its own pace boundary.

Fake transport and context only, no browser, network or account.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from backend.core.baidu_netdisk import web_directories as wd

PARENT = "/迁移测试"
WAIT = {"timeout": 0.05, "poll": 0, "settle": 2}
ROWS = [{"id": "11", "name": "a", "is_dir": True}, {"id": "12", "name": "b.txt", "is_dir": False}]


class Row:
    def __init__(self, row):
        self.row = row

    def __bool__(self):
        return True

    def attr(self, name):
        return self.row["id"] if name == "data-id" else self.row["name"]

    def ele(self, selector, timeout=None):
        if selector == wd.ROW_TITLE:
            return Row(self.row)
        if selector == wd.ROW_FOLDER:
            return Row(self.row) if self.row["is_dir"] else None
        raise LookupError(selector)


class Transport:
    """Fake tab: ``events`` is the ordered log of pace boundaries and real navigations."""

    def __init__(self, rows=ROWS, fail_get=False, fail_refresh=False):
        self.rows, self.fail_get, self.fail_refresh = rows, fail_get, fail_refresh
        self.url = "about:blank"
        self.states = SimpleNamespace(is_loading=False, ready_state="complete")
        self.events = []

    def pace(self):
        self.events.append("pace")

    def get(self, url):
        self.events.append(("get", url))
        if self.fail_get:
            raise RuntimeError("get failed")
        self.url = url

    def refresh(self):
        self.events.append("refresh")
        if self.fail_refresh:
            raise RuntimeError("refresh failed")

    def eles(self, selector, timeout=None):
        if selector == wd.ROW:
            return [Row(r) for r in self.rows]
        raise LookupError(selector)

    def navigations(self):
        return [e for e in self.events if e != "pace"]


class Owned:
    """Injected caller context: hands out its own tab and must never be asked to open/close one."""

    def __init__(self, tab):
        self.tab, self.entered, self.exited = tab, 0, 0
        self.browser = SimpleNamespace(new_tab=self._forbidden, tab_ids=["caller"])
        tab.close = self._forbidden

    def _forbidden(self, *a, **k):
        raise AssertionError("listing must not open or close tabs")

    def main_tab(self):
        return self.tab

    @contextmanager
    def __call__(self):
        self.entered += 1
        try:
            yield self
        finally:
            self.exited += 1


def listing(tab, **kw):
    owned = Owned(tab)
    kw.setdefault("pace", tab.pace)
    return owned, wd.list_directory(PARENT, context=owned, **WAIT, **kw)


def test_each_navigation_has_its_own_pace_boundary_and_listing_is_exact():
    tab = Transport()
    owned, result = listing(tab)
    assert tab.events == ["pace", ("get", wd.route(PARENT)), "pace", "refresh"]
    assert result == {"status": "listed", "path": PARENT, "count": 2, "entries": [
        {"id": "11", "name": "a", "path": f"{PARENT}/a", "is_dir": True},
        {"id": "12", "name": "b.txt", "path": f"{PARENT}/b.txt", "is_dir": False},
    ]}
    assert (owned.entered, owned.exited) == (1, 1)
    assert tab.url == wd.route(PARENT)  # caller tab used as is, not replaced


def test_pace_runs_inside_the_context():
    tab = Transport()
    owned = Owned(tab)
    seen = []
    wd.list_directory(PARENT, context=owned, pace=lambda: seen.append(owned.entered - owned.exited), **WAIT)
    assert seen == [1, 1]  # both boundaries while the context (lock) is held


def test_invalid_path_makes_no_pace_and_no_navigation():
    tab = Transport()
    owned = Owned(tab)
    for bad in ("relative", "", "/a/../b", "/a/./b", "/a*b"):
        assert wd.list_directory(bad, context=owned, pace=tab.pace, **WAIT)["status"] == "invalid"
    assert tab.events == [] and owned.entered == 0


def test_failed_get_prevents_refresh():
    tab = Transport(fail_get=True)
    with pytest.raises(RuntimeError, match="get failed"):
        listing(tab)
    assert tab.events == ["pace", ("get", wd.route(PARENT))]


def test_failed_refresh_is_not_retried():
    tab = Transport(fail_refresh=True)
    with pytest.raises(RuntimeError, match="refresh failed"):
        listing(tab)
    assert tab.events == ["pace", ("get", wd.route(PARENT)), "pace", "refresh"]
    assert tab.navigations().count("refresh") == 1


def test_pace_failure_blocks_the_guarded_navigation():
    tab = Transport()
    calls = []

    def pace():
        calls.append(1)
        if len(calls) == 2:
            raise TimeoutError("shared pacer unavailable")
        tab.pace()

    with pytest.raises(TimeoutError):
        listing(tab, pace=pace)
    assert tab.navigations() == [("get", wd.route(PARENT))]  # no refresh without its pace


def test_default_pace_sleeps_action_gap_before_each_navigation(monkeypatch):
    tab = Transport()
    real = wd.time
    monkeypatch.setattr(wd, "time", SimpleNamespace(
        sleep=lambda s: tab.events.append(("sleep", s)) if s else None,
        monotonic=real.monotonic))
    owned = Owned(tab)
    result = wd.list_directory(PARENT, context=owned, **WAIT)
    assert result["status"] == "listed"
    assert wd.ACTION_GAP >= 3.2
    assert tab.events == [("sleep", wd.ACTION_GAP), ("get", wd.route(PARENT)),
                          ("sleep", wd.ACTION_GAP), "refresh"]


def test_shared_pacer_spaces_navigations_across_listings():
    """An injected shared pacer (stand-in for a cross-process FilePacer) sees every navigation."""
    clock = {"now": 0.0, "last": None}
    stamps = []
    tab = Transport()

    def shared_pace():
        if clock["last"] is not None:
            clock["now"] = max(clock["now"], clock["last"] + wd.ACTION_GAP)
        clock["last"] = clock["now"]

    tab.get = lambda url, _g=tab.get: (stamps.append(clock["now"]), _g(url))
    tab.refresh = lambda _r=tab.refresh: (stamps.append(clock["now"]), _r())
    for _ in range(2):
        assert wd.list_directory(PARENT, context=Owned(tab), pace=shared_pace, **WAIT)["status"] == "listed"
    assert len(stamps) == 4
    assert all(b - a >= wd.ACTION_GAP for a, b in zip(stamps, stamps[1:]))
