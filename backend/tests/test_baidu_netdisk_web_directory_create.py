"""Web folder create/ensure: fake tab and context only, no browser or account."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from DrissionPage.common import Keys

from backend.core.baidu_netdisk import web, web_directories as wd

WAIT = {"timeout": 0.05, "poll": 0, "settle": 2}


class Row:
    def __init__(self, row):
        self.row = row

    def __bool__(self):
        return True

    def attr(self, name):
        return self.row["id"] if name == "data-id" else self.row["name"]

    def ele(self, selector, timeout=None):
        if selector == wd.ROW_TITLE:
            return self
        if selector == wd.ROW_FOLDER:
            return self if self.row["is_dir"] else None
        raise LookupError(selector)


class Button:
    def __init__(self, tab):
        self.tab = tab

    def click(self):
        self.tab.act("click")
        self.tab.editors.append(Editor(self.tab))
        if self.tab.on_click:
            self.tab.on_click()


class Editor:
    def __init__(self, tab, cls="u-textarea__inner"):
        self.tab, self.cls, self.value = tab, cls, "新建文件夹"

    def attr(self, name):
        return self.cls if name == "class" else None

    def input(self, text, clear=False):
        self.tab.act("input")
        self.tab.inputs.append(text)
        if text != Keys.ENTER:
            self.value = self.tab.mangle(text if clear else self.value + text)
            return
        if self.tab.submit_error:
            raise RuntimeError("cdp")
        self.tab.editors.remove(self)
        if self.tab.apply_create:
            self.tab.next_id += 1
            parent = wd.url_path(self.tab.url)
            self.tab.tree[parent].append({"id": str(self.tab.next_id), "name": self.value, "is_dir": True})
            self.tab.tree[wd._join(parent, self.value)] = []


class States:
    ready_state = "complete"

    def __init__(self, tab):
        self.tab = tab

    @property
    def is_loading(self):  # a folder absent from the tree never yields a trusted listing
        return wd.url_path(self.tab.url) not in self.tab.tree


class Tab:
    """``tree`` maps a folder path to its rows."""

    def __init__(self, tree):
        self.tree = tree
        self.url = "about:blank"
        self.states = States(self)
        self.editors, self.inputs, self.visits, self.log = [], [], [], []
        self.buttons, self.next_id, self.on_click = 1, 900, None
        self.apply_create, self.submit_error, self.mangle = True, False, lambda text: text
        self.paced = False
        self.tab_id, self.open, self.closes, self.close_error = "OWN", False, 0, None

    def close(self):
        self.closes += 1
        if self.close_error:
            raise self.close_error
        self.open = False

    def sleep(self, seconds):
        assert seconds >= 3.2
        self.paced = True

    def act(self, kind):
        assert self.paced, f"{kind} without a {wd.ACTION_GAP}s gap"
        self.paced = False
        self.log.append(kind)

    def get(self, url):
        self.act("get")
        self.visits.append(wd.url_path(url))
        self.url = url

    def refresh(self):
        self.act("refresh")
        self.editors.clear()

    def eles(self, selector, timeout=None):
        if selector == wd.ROW:
            return [Row(r) for r in self.tree.get(wd.url_path(self.url), [])]
        if selector == wd.EDIT_BOX:
            return list(self.editors)
        if selector == wd.CREATE_BUTTON:
            return [Button(self) for _ in range(self.buttons)]
        raise LookupError(selector)


class Browser:
    """Pre-existing homepage and caller tabs plus the one tab an operation may open."""

    def __init__(self, tab):
        self.tab, self.opened = tab, 0
        self.existing = ["HOME", "CALLER"]

    @property
    def tab_ids(self):
        return self.existing + ([self.tab.tab_id] if self.tab.open else [])

    def new_tab(self):
        self.opened += 1
        self.tab.open, self.tab.url = True, "about:blank"
        self.tab.editors.clear()
        return self.tab


def no_main_tab():
    raise AssertionError("operations must not use the shared main tab")


def ctx(tab, browser=None):
    browser = browser or Browser(tab)

    @contextmanager
    def context():
        yield SimpleNamespace(browser=browser, main_tab=no_main_tab, facts={})
    return context


def d(id_, name):
    return {"id": id_, "name": name, "is_dir": True}


def f(id_, name):
    return {"id": id_, "name": name, "is_dir": False}


def base_tree():
    return {"/": [d("1", "数据分析"), d("2", "资料")], "/数据分析": [d("10", "已有"), f("11", "报表.txt")],
            "/数据分析/已有": [], "/资料": []}


def mkdir(tab, parent, name, parent_id=None):
    return wd.create_directory(parent, name, parent_id, context=ctx(tab), sleep=tab.sleep, **WAIT)


def ensure(tab, path, anchor, anchor_id=None):
    return wd.ensure_directory(path, anchor, anchor_id, context=ctx(tab), sleep=tab.sleep, **WAIT)


def never():
    raise AssertionError("must not attach")


def created_names(tab, parent):
    return [r["name"] for r in tab.tree[parent]]


@pytest.mark.parametrize("name", ["政务", "a b  c", "数据 分析 2026", "Ünï cødé 😀"])
def test_create_types_name_verbatim_and_verifies_after_reload(name):
    tab = Tab(base_tree())
    result = mkdir(tab, "/数据分析", name, "1")
    assert result == {"parent": "/数据分析", "path": f"/数据分析/{name}", "status": "created", "id": "901",
                      "tab_cleanup": "closed"}
    assert tab.inputs == [name, Keys.ENTER]  # one fill, Enter exactly once
    # grandparent check, parent listing, then a fresh reload as readback
    assert tab.visits == ["/", "/数据分析", "/数据分析"]
    assert tab.log == ["get", "refresh", "get", "refresh", "click", "input", "input", "get", "refresh"]


def test_create_in_drive_root_needs_no_id():
    tab = Tab(base_tree())
    assert mkdir(tab, "/", "新根目录")["status"] == "created"
    assert tab.visits == ["/", "/"]
    assert wd.create_directory("/", "x", "1", context=never)["reason"] == "root_has_no_id"


def test_existing_folder_is_already_exists_and_file_clash_fails():
    tab = Tab(base_tree())
    assert mkdir(tab, "/数据分析", "已有", "1") == {
        "parent": "/数据分析", "path": "/数据分析/已有", "status": "already_exists", "id": "10",
        "tab_cleanup": "closed"}
    clash = mkdir(tab, "/数据分析", "报表.txt", "1")
    assert clash["status"] == "target_is_file" and clash["id"] == "11"
    tab.tree["/数据分析"].append(d("12", "Case"))
    assert mkdir(tab, "/数据分析", "case", "1")["status"] == "name_conflict"
    assert tab.inputs == [] and "click" not in tab.log


def test_parent_must_be_a_fresh_folder_row_with_matching_id():
    tree = base_tree()
    tree["/幽灵"] = []  # its URL would settle, but no row proves it exists
    tab = Tab(tree)
    assert mkdir(tab, "/幽灵", "x", "5")["status"] == "parent_missing"
    assert mkdir(tab, "/数据分析", "x", "2")["status"] == "parent_id_mismatch"
    assert mkdir(tab, "/数据分析/报表.txt", "x", "11")["status"] == "parent_not_directory"
    tab.tree["/"].append(d("3", "数据分析"))
    assert mkdir(tab, "/数据分析", "x", "1")["status"] == "parent_ambiguous"
    deep = mkdir(Tab(base_tree()), "/不存在/子", "x", "7")
    assert deep["status"] == "unavailable" and deep["at"] == "/不存在"
    assert tab.inputs == []


def test_invalid_inputs_never_open_browser():
    for parent, name, pid in (("relative", "x", None), ("/a/../b", "x", "1"), ("/a", "b/c", "1"),
                              ("/a", " x", "1"), ("/a", "x.", "1"), ("/a", "x", None), ("/a", "x", "bad id")):
        assert wd.create_directory(parent, name, pid, context=never)["status"] == "invalid"


def test_duplicate_or_stale_editor_and_extra_buttons_never_input():
    tab = Tab(base_tree())
    tab.on_click = lambda: tab.editors.append(Editor(tab))
    r = mkdir(tab, "/数据分析", "政务", "1")
    assert r["status"] == "not_submitted" and r["reason"] == "editor" and r["editor_opened"]

    tab = Tab(base_tree())
    tab.buttons = 2
    r = mkdir(tab, "/数据分析", "政务", "1")
    assert (r["reason"], r["editor_opened"]) == ("create_button", False)

    tab = Tab(base_tree())
    tab.on_click = lambda: setattr(tab.editors[0], "cls", "other")
    assert mkdir(tab, "/数据分析", "政务", "1")["reason"] == "editor"

    tab = Tab(base_tree())
    real_refresh = tab.refresh
    tab.refresh = lambda: (real_refresh(), tab.editors.append(Editor(tab)))  # leftover rename editor
    r = mkdir(tab, "/数据分析", "政务", "1")
    assert r["reason"] == "stale_editor" and not r["editor_opened"]
    assert tab.inputs == []


def test_parent_changed_after_click_never_inputs():
    tab = Tab(base_tree())
    tab.on_click = lambda: setattr(tab, "url", wd.route("/资料"))
    r = mkdir(tab, "/数据分析", "政务", "1")
    assert r["status"] == "not_submitted" and r["reason"] == "parent_changed"
    assert tab.inputs == []


@pytest.mark.parametrize("mangle", [
    lambda t: t[:-1],                       # clipped, as native OCR did
    lambda t: t.replace(" ", " "),     # look-alike space
    lambda t: t.replace("分", "～"),  # different code point
    lambda t: t + " ",
])
def test_name_roundtrip_must_be_exact(mangle):
    tab = Tab(base_tree())
    tab.mangle = mangle
    r = mkdir(tab, "/数据分析", "数据 分析", "1")
    assert (r["status"], r["reason"]) == ("not_submitted", "name_roundtrip")
    assert Keys.ENTER not in tab.inputs


def test_unconfirmed_or_failed_submit_is_ambiguous_without_second_enter():
    tab = Tab(base_tree())
    tab.apply_create = False
    r = mkdir(tab, "/数据分析", "政务", "1")
    assert r["status"] == "ambiguous" and r["reason"] == "readback_unconfirmed"
    assert tab.inputs.count(Keys.ENTER) == 1 and "政务" not in created_names(tab, "/数据分析")

    tab = Tab(base_tree())
    tab.submit_error = True
    r = mkdir(tab, "/数据分析", "政务", "1")
    assert r["reason"] == "submit_error" and tab.inputs.count(Keys.ENTER) == 1


def test_reload_that_loses_the_row_is_ambiguous():
    tab = Tab(base_tree())
    real_refresh = tab.refresh

    def refresh():
        real_refresh()
        if tab.inputs.count(Keys.ENTER):
            tab.tree["/数据分析"] = [r for r in tab.tree["/数据分析"] if r["name"] != "政务"]
    tab.refresh = refresh
    r = mkdir(tab, "/数据分析", "政务", "1")
    assert r["status"] == "ambiguous" and r["reason"] == "reload_unconfirmed"
    assert tab.inputs.count(Keys.ENTER) == 1


def test_ensure_creates_missing_levels_under_verified_anchor():
    tab = Tab(base_tree())
    r = ensure(tab, "/数据分析/已有/政务/2026 年", "/数据分析", "1")
    assert r["status"] == "created" and r["id"] == "902"
    assert r["steps"] == [{"path": "/数据分析/已有", "status": "already_exists", "id": "10"},
                          {"path": "/数据分析/已有/政务", "status": "created", "id": "901"},
                          {"path": "/数据分析/已有/政务/2026 年", "status": "created", "id": "902"}]
    assert tab.inputs == ["政务", Keys.ENTER, "2026 年", Keys.ENTER]
    again = ensure(tab, "/数据分析/已有/政务/2026 年", "/数据分析", "1")
    assert again["status"] == "already_exists" and again["id"] == "902"
    assert tab.inputs.count(Keys.ENTER) == 2
    assert ensure(tab, "/数据分析", "/数据分析", "1")["status"] == "already_exists"


def test_ensure_from_root_anchor():
    tab = Tab(base_tree())
    r = ensure(tab, "/新区/子", "/")
    assert r["status"] == "created" and [s["status"] for s in r["steps"]] == ["created", "created"]


def test_ensure_anchor_guards():
    for path, anchor, aid, reason in (
            ("/资料/x", "/数据分析", "1", "outside_anchor"),
            ("/数据分析2/x", "/数据分析", "1", "outside_anchor"),
            ("/数据分析/../资料", "/数据分析", "1", "invalid_path"),
            ("/数据分析/x.", "/数据分析", "1", "invalid_path"),
            ("/数据分析/x", "/数据分析", None, "expected_anchor_id_required"),
            ("/x", "/", "1", "root_has_no_id")):
        r = wd.ensure_directory(path, anchor, aid, context=never)
        assert (r["status"], r["reason"]) == ("invalid", reason)
    tab = Tab(base_tree())
    assert ensure(tab, "/数据分析/x", "/数据分析", "2")["status"] == "parent_id_mismatch"
    assert ensure(tab, "/数据分析/报表.txt/x", "/数据分析", "1")["status"] == "target_is_file"
    assert tab.inputs == []


def test_ensure_halts_on_ambiguous_level_without_replay():
    tab = Tab(base_tree())
    tab.apply_create = False
    r = ensure(tab, "/数据分析/政务/子", "/数据分析", "1")
    assert r["status"] == "ambiguous" and r["at"] == "/数据分析/政务" and r["steps"] == []
    assert tab.inputs == ["政务", Keys.ENTER]


def test_ownership_refusal_and_lock_busy_are_blocked():
    @contextmanager
    def refused():
        raise web.OwnershipError("not_owned")
        yield

    @contextmanager
    def busy():
        raise web.WebError("browser lock busy")
        yield
    assert wd.create_directory("/", "x", context=refused)["reason"] == "not_owned"
    assert wd.ensure_directory("/a", "/", context=busy)["reason"] == "browser_lock_busy"


def test_cli_dispatches_mkdir_and_ensure(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(wd, "create_directory", lambda *a: calls.append(a) or {"status": "created"})
    monkeypatch.setattr(wd, "ensure_directory", lambda *a: calls.append(a) or {"status": "ambiguous"})
    assert wd.main(["mkdir", "/数据分析", "政务", "--expected-parent-id", "1"]) == 0
    assert wd.main(["ensure", "/数据分析/政务", "--anchor", "/数据分析", "--anchor-id", "1"]) == 3
    assert calls == [("/数据分析", "政务", "1"), ("/数据分析/政务", "/数据分析", "1")]


def test_non_canonical_paths_are_refused_not_normalised():
    for parent in ("/数据分析 ", " /数据分析", "/数据分析/", "//数据分析"):
        assert wd.create_directory(parent, "x", "1", context=never)["status"] == "invalid"
    for path, anchor in (("/数据分析/x ", "/数据分析"), ("/数据分析//x", "/数据分析"), ("/数据分析/x", "/数据分析/")):
        assert wd.ensure_directory(path, anchor, "1", context=never)["status"] == "invalid"


def _home_untouched(browser):
    assert browser.tab_ids[:2] == ["HOME", "CALLER"]


@pytest.mark.parametrize("setup, status", [
    (lambda tab: None, "created"),
    (lambda tab: setattr(tab, "buttons", 0), "not_submitted"),
    (lambda tab: setattr(tab, "apply_create", False), "ambiguous"),
    (lambda tab: setattr(tab, "submit_error", True), "ambiguous"),
])
def test_create_closes_only_its_own_tab(setup, status):
    tab = Tab(base_tree())
    setup(tab)
    browser = Browser(tab)
    r = wd.create_directory("/数据分析", "政务", "1", context=ctx(tab, browser), sleep=tab.sleep, **WAIT)
    assert r["status"] == status and r["tab_cleanup"] == "closed"
    assert (browser.opened, tab.closes, tab.open) == (1, 1, False)
    _home_untouched(browser)


def test_parent_failures_and_browser_faults_still_close_own_tab():
    tab = Tab(base_tree())
    browser = Browser(tab)
    r = wd.create_directory("/数据分析", "x", "2", context=ctx(tab, browser), sleep=tab.sleep, **WAIT)
    assert (r["status"], r["tab_cleanup"], tab.closes) == ("parent_id_mismatch", "closed", 1)

    def broken_get(url):
        raise RuntimeError("cdp")
    tab.get = broken_get
    r = wd.create_directory("/数据分析", "x", "1", context=ctx(tab, browser), sleep=tab.sleep, **WAIT)
    assert (r["status"], r["reason"], r["error"]) == ("unavailable", "browser_error", "RuntimeError")
    assert (r["tab_cleanup"], tab.closes, tab.inputs) == ("closed", 2, [])
    _home_untouched(browser)


def test_cleanup_failure_does_not_mask_outcome():
    tab = Tab(base_tree())
    tab.close_error = RuntimeError("cdp")
    r = mkdir(tab, "/数据分析", "政务", "1")
    assert (r["status"], r["id"], r["tab_cleanup"]) == ("created", "901", "failed:RuntimeError")

    tab = Tab(base_tree())
    tab.apply_create = False
    tab.close_error = RuntimeError("cdp")
    r = mkdir(tab, "/数据分析", "政务", "1")
    assert (r["status"], r["tab_cleanup"]) == ("ambiguous", "failed:RuntimeError")
    assert tab.inputs.count(Keys.ENTER) == 1

    tab = Tab(base_tree())
    tab.close = lambda: None  # close "succeeds" but the tab is still listed
    assert mkdir(tab, "/数据分析", "政务", "1")["tab_cleanup"] == "still_open"


def test_ensure_uses_one_own_tab_and_keeps_steps_on_fault():
    tab = Tab(base_tree())
    browser = Browser(tab)
    real_get = tab.get

    def get(url):  # fault while walking the second missing level
        if wd.url_path(url) == "/数据分析/政务/子":
            raise RuntimeError("cdp")
        real_get(url)
    tab.get = get
    r = wd.ensure_directory("/数据分析/政务/子/孙", "/数据分析", "1", context=ctx(tab, browser),
                            sleep=tab.sleep, **WAIT)
    assert (r["status"], r["reason"], r["at"]) == ("unavailable", "browser_error", "/数据分析/政务/子/孙")
    assert [s["path"] for s in r["steps"]] == ["/数据分析/政务", "/数据分析/政务/子"]
    assert (browser.opened, tab.closes, r["tab_cleanup"]) == (1, 1, "closed")
    _home_untouched(browser)


def test_never_uses_or_closes_a_tab_it_cannot_prove_it_opened():
    tab = Tab(base_tree())
    browser = Browser(tab)
    browser.existing.append(tab.tab_id)  # new_tab handed back an already-open tab
    r = wd.create_directory("/数据分析", "政务", "1", context=ctx(tab, browser), sleep=tab.sleep, **WAIT)
    assert (r["status"], r["reason"]) == ("blocked", "new_tab_not_distinct")
    assert (tab.closes, tab.visits, "tab_cleanup" in r) == (0, [], False)

    @contextmanager
    def main_tab_only():  # legacy context shape: no browser, so no dedicated tab
        yield SimpleNamespace(main_tab=no_main_tab, facts={})
    assert wd.create_directory("/", "x", context=main_tab_only)["reason"] == "no_dedicated_tab"

    browser = Browser(tab)
    browser.new_tab = no_main_tab
    r = wd.ensure_directory("/a", "/", context=ctx(tab, browser), sleep=tab.sleep)
    assert (r["status"], r["reason"]) == ("blocked", "new_tab_failed")
