from pathlib import Path

from scripts.download_xiaoe_text import (
    _close_task_tab_without_stopping_browser,
    _get_or_open_text_list_tab,
    _new_full_state,
    _next_cursor,
)


def test_text_cursor_advances_across_pages() -> None:
    assert _next_cursor(2, 8) == {"page": 2, "item_index": 9}
    assert _next_cursor(2, 9) == {"page": 3, "item_index": 0}


def test_text_full_state_resumes_failed_cursor(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        '{"status":"failed","cursor":{"page":4,"item_index":6},"archived_count":12}',
        encoding="utf-8",
    )
    state = _new_full_state(path)
    assert state["cursor"] == {"page": 4, "item_index": 6}
    assert state["archived_count"] == 12


def test_text_cleanup_keeps_shared_browser_last_tab() -> None:
    class Tab:
        closed = False

        def close(self):
            self.closed = True

    class Browser:
        def __init__(self, tabs):
            self.tabs = tabs

        def get_tabs(self):
            return self.tabs

    only = Tab()
    _close_task_tab_without_stopping_browser(Browser([only]), only)
    assert only.closed is False

    extra = Tab()
    _close_task_tab_without_stopping_browser(Browser([only, extra]), extra)
    assert extra.closed is True


def test_text_list_reuses_existing_user_tab() -> None:
    class Tab:
        def __init__(self, url):
            self.url = url

    class Browser:
        def __init__(self):
            self.existing = Tab("https://admin.xiaoe-tech.com/t/course/text/list")

        def get_tabs(self):
            return [self.existing]

        def new_tab(self, url):
            raise AssertionError("不应新开重复图文标签页")

    browser = Browser()
    tab, created = _get_or_open_text_list_tab(browser)
    assert tab is browser.existing
    assert created is False
