"""Web folder listing/rename: fake tab and context only, no browser."""

from contextlib import contextmanager
from types import SimpleNamespace

from backend.core.baidu_netdisk import web, web_directories as wd

PARENT = "/迁移测试"
WAIT = {"timeout": 0.05, "poll": 0, "settle": 2}
NO_PACE = lambda: None  # noqa: E731 - listing tests do not wait the real ACTION_GAP


class Ele:
    def __init__(self, tab=None, row=None, kind="row"):
        self.tab, self.row, self.kind = tab, row, kind

    def __bool__(self):
        return True

    def attr(self, name):
        return self.row["id"] if name == "data-id" else self.row["name"]

    def ele(self, selector, timeout=None):
        if selector == wd.ROW_TITLE:
            return Ele(row=self.row, kind="title")
        if selector == wd.ROW_FOLDER:
            return Ele(kind="folder") if self.row["is_dir"] else None
        if selector == wd.MORE_BUTTON and self.tab.hovered is self.row:
            return Ele(self.tab, self.row, "more")
        if selector == wd.RENAME_BUTTON and self.tab.menu is self.row:
            return Ele(self.tab, self.row, "rename")
        raise LookupError(selector)

    def click(self):
        if self.kind == "more":
            self.tab.menu = self.row
        else:
            self.tab.editing = self.row

    @property
    def value(self):
        return self.row["name"]

    def input(self, text, clear=False):
        self.tab.inputs.append(text)
        if text == "":
            if self.tab.submit_error:
                raise RuntimeError("cdp")
            if self.tab.apply_rename:
                self.row["name"] = self.tab.inputs[-2]


class Tab:
    def __init__(self, rows):
        self.rows = rows
        self.url = "about:blank"
        self.states = SimpleNamespace(is_loading=False, ready_state="complete")
        self.actions = SimpleNamespace(move_to=self._hover)
        self.hovered = self.menu = self.editing = None
        self.inputs, self.visits = [], []
        self.apply_rename, self.submit_error = True, False

    def _hover(self, row_ele):
        self.hovered = row_ele.row

    def get(self, url):
        self.visits.append(url)
        self.url = url

    def refresh(self):
        pass

    def eles(self, selector, timeout=None):
        if selector == wd.ROW:
            return [Ele(self, r) for r in self.rows]
        if selector == wd.EDIT_BOX:
            return [Ele(self, self.editing, "box")] if self.editing else []
        raise LookupError(selector)

    def ele(self, selector, timeout=None):
        return next((Ele(self, r) for r in self.rows if f'data-id="{r["id"]}"' in selector), None)


def ctx(tab):
    @contextmanager
    def context():
        yield SimpleNamespace(main_tab=lambda: tab, facts={})
    return context


def folder(id_, name, is_dir=True):
    return {"id": id_, "name": name, "is_dir": is_dir}


def rename(tab, path, new_name, expected_id=None):
    return wd.rename_directory(path, new_name, expected_id, context=ctx(tab), **WAIT)


def test_route_roundtrip_and_listing():
    assert wd.url_path(wd.route("/a b/中文&x")) == "/a b/中文&x"
    assert wd.url_path("https://pan.baidu.com/disk/main#/index?category=all") is None
    tab = Tab([folder("11", "old"), folder("12", "f.txt", is_dir=False)])
    result = wd.list_directory(PARENT, context=ctx(tab), pace=NO_PACE, **WAIT)
    assert result["status"] == "listed" and result["count"] == 2
    assert result["entries"][0] == {"id": "11", "name": "old", "path": f"{PARENT}/old", "is_dir": True}
    assert tab.visits == [wd.route(PARENT)]


def test_listing_requires_exact_path_and_idle_page():
    tab = Tab([])
    tab.get = lambda url: setattr(tab, "url", wd.route("/elsewhere"))
    assert wd.list_directory(PARENT, context=ctx(tab), pace=NO_PACE, **WAIT)["reason"] == "path_mismatch"
    tab = Tab([])
    tab.states.is_loading = True
    assert wd.list_directory(PARENT, context=ctx(tab), pace=NO_PACE, **WAIT)["reason"] == "listing_not_settled"
    tab.states.is_loading = False
    assert wd.list_directory(PARENT, context=ctx(tab), pace=NO_PACE, **WAIT)["count"] == 0  # empty folder allowed


def test_rename_success_verifies_same_row_after_reload():
    rows = [folder("11", "old")]
    tab = Tab(rows)
    result = rename(tab, f"{PARENT}/old", "新名称")
    assert result == {"path": f"{PARENT}/old", "new_path": f"{PARENT}/新名称", "status": "renamed", "id": "11"}
    assert tab.inputs == ["新名称", ""]
    assert len(tab.visits) == 2  # initial listing + independent reload


def test_invalid_inputs_never_open_browser():
    def boom():
        raise AssertionError("must not attach")
    for path, name in (("/", "x"), ("relative", "x"), (f"{PARENT}/old", "a/b"), (f"{PARENT}/old", "old")):
        assert wd.rename_directory(path, name, context=boom)["status"] == "invalid"


def test_preconditions_block_mutation():
    tab = Tab([folder("11", "old"), folder("12", "新名称"), folder("13", "file", is_dir=False)])
    assert rename(tab, f"{PARENT}/old", "新名称")["status"] == "target_exists"
    assert rename(tab, f"{PARENT}/file", "文件")["status"] == "not_directory"
    assert rename(tab, f"{PARENT}/old", "别名", expected_id="99")["status"] == "id_mismatch"
    assert rename(tab, f"{PARENT}/missing", "别名")["status"] == "source_missing"
    assert tab.inputs == []


def test_replay_needs_matching_expected_id():
    tab = Tab([folder("11", "新名称")])
    assert rename(tab, f"{PARENT}/old", "新名称", "11")["status"] == "already_renamed"
    no_id = rename(tab, f"{PARENT}/old", "新名称")
    assert no_id["status"] == "source_missing" and no_id["reason"] == "target_present_expected_id_required"
    wrong = rename(tab, f"{PARENT}/old", "新名称", "12")
    assert wrong["status"] == "source_missing" and wrong["reason"] == "target_id_mismatch"
    assert tab.inputs == []


def test_unconfirmed_or_failed_submit_is_ambiguous_and_not_retried():
    tab = Tab([folder("11", "old")])
    tab.apply_rename = False
    result = rename(tab, f"{PARENT}/old", "新名称")
    assert result["status"] == "ambiguous" and result["reason"] == "readback_unconfirmed"
    assert tab.inputs.count("") == 1

    tab = Tab([folder("11", "old")])
    tab.submit_error = True
    assert rename(tab, f"{PARENT}/old", "新名称")["reason"] == "submit_error"
    assert tab.inputs.count("") == 1


def test_ui_failure_before_enter_is_not_submitted():
    tab = Tab([folder("11", "old")])
    tab.actions.move_to = lambda row: None  # more button stays hidden without hover
    result = rename(tab, f"{PARENT}/old", "新名称")
    assert result["status"] == "not_submitted" and result["reason"] == "more_menu"
    assert tab.inputs == []


def test_ownership_refusal_is_blocked():
    @contextmanager
    def refused():
        raise web.OwnershipError("not_owned")
        yield
    assert wd.rename_directory(f"{PARENT}/old", "新名称", context=refused)["reason"] == "not_owned"
