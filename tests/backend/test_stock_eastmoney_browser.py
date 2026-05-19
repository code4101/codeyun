from pathlib import Path
from types import SimpleNamespace

from backend.core.stock.eastmoney_browser import (
    build_chromium_options,
    capture_tab_snapshot,
    detect_login_hint,
    ensure_eastmoney_browser_paths,
    get_eastmoney_browser_paths,
)
from backend.core.stock.eastmoney_trade import _open_tab
from backend.core.stock import eastmoney_trade


def test_get_eastmoney_browser_paths_uses_data_dir():
    paths = get_eastmoney_browser_paths(Path("data-root"))

    assert paths.base_dir == Path("data-root") / "eastmoney"
    assert paths.user_data_dir == Path("data-root") / "eastmoney" / "chromium-user-data"
    assert paths.download_dir == Path("data-root") / "eastmoney" / "downloads"
    assert paths.diagnostics_dir == Path("data-root") / "eastmoney" / "diagnostics"


def test_ensure_eastmoney_browser_paths_creates_runtime_dirs(tmp_path):
    paths = get_eastmoney_browser_paths(tmp_path)

    ensured = ensure_eastmoney_browser_paths(paths)

    assert ensured == paths
    assert paths.user_data_dir.is_dir()
    assert paths.download_dir.is_dir()
    assert paths.diagnostics_dir.is_dir()


def test_build_chromium_options_uses_dedicated_profile(tmp_path):
    paths = ensure_eastmoney_browser_paths(get_eastmoney_browser_paths(tmp_path))

    options = build_chromium_options(paths)

    assert Path(options.user_data_path) == paths.user_data_dir
    assert Path(options.download_path) == paths.download_dir


def test_detect_login_hint_from_common_login_text():
    assert detect_login_hint(title="东方财富通行证登录", url="https://example.com", html_sample="") is True
    assert detect_login_hint(title="行情中心", url="https://quote.eastmoney.com", html_sample="最新行情") is False


def test_capture_tab_snapshot_reads_basic_state():
    tab = SimpleNamespace(title="东方财富", url="https://www.eastmoney.com/", html="<html>登录</html>")

    snapshot = capture_tab_snapshot(tab, html_limit=20)

    assert snapshot.title == "东方财富"
    assert snapshot.url == "https://www.eastmoney.com/"
    assert snapshot.login_hint is True
    assert snapshot.html_sample == "<html>登录</html>"


class FakeTradeTab:
    def __init__(self, url: str, body_text: str = ""):
        self.url = url
        self.body_text = body_text
        self.get_calls: list[str] = []
        self.js_calls: list[str] = []

    def get(self, url: str):
        self.get_calls.append(url)
        self.url = url

    def ele(self, selector: str):
        if selector == "tag:body":
            return SimpleNamespace(text=self.body_text)
        raise LookupError(selector)

    def run_js(self, script: str):
        self.js_calls.append(script)
        return '{"ok": true}'


class FakeTradeBrowser:
    def __init__(self, tabs: list[FakeTradeTab]):
        self.tabs = tabs
        self.new_tab_calls: list[str] = []

    def get_tabs(self):
        return self.tabs

    def new_tab(self, url: str):
        self.new_tab_calls.append(url)
        tab = FakeTradeTab(url)
        tab.set = SimpleNamespace(timeouts=lambda **_kwargs: None)
        tab.wait = SimpleNamespace(doc_loaded=lambda **_kwargs: None)
        self.tabs.append(tab)
        return tab


def test_open_tab_reuses_existing_trade_login_without_reopening_target():
    login_tab = FakeTradeTab("https://jywg.18.cn/Login?returl=%2fSearch%2fPosition")
    browser = FakeTradeBrowser([login_tab])

    tab = _open_tab(browser, "https://jywg.18.cn/Search/Position")

    assert tab is login_tab
    assert login_tab.get_calls == []
    assert browser.new_tab_calls == []


def test_open_trade_account_page_opens_position_page(monkeypatch):
    browser = FakeTradeBrowser([])
    monkeypatch.setattr(eastmoney_trade, "_get_browser", lambda: browser)

    state = eastmoney_trade.open_trade_account_page()

    assert browser.new_tab_calls == ["https://jywg.18.cn/Search/Position"]
    assert state["url"] == "https://jywg.18.cn/Search/Position"
    assert state["login_required"] is False
    assert state["login_duration_preset"] is False
    assert state["captcha_ocr_filled"] is False


def test_open_trade_account_page_presets_three_hour_duration(monkeypatch):
    login_tab = FakeTradeTab(
        "https://jywg.18.cn/Login?returl=%2fSearch%2fPosition",
        body_text="交易登录 请输入资金账号 在线时间 15分钟 30分钟 3小时",
    )
    browser = FakeTradeBrowser([login_tab])
    monkeypatch.setattr(eastmoney_trade, "_get_browser", lambda: browser)
    monkeypatch.setattr(eastmoney_trade, "_try_fill_login_captcha", lambda _tab: eastmoney_trade._empty_captcha_state())

    state = eastmoney_trade.open_trade_account_page()

    assert state["login_required"] is True
    assert state["login_duration_preset"] is True
    assert len(login_tab.js_calls) == 1
    assert "3小时" in login_tab.js_calls[0]


def test_open_trade_account_page_reports_captcha_ocr_state(monkeypatch):
    login_tab = FakeTradeTab(
        "https://jywg.18.cn/Login?returl=%2fSearch%2fPosition",
        body_text="交易登录 请输入资金账号 在线时间 15分钟 30分钟 3小时",
    )
    browser = FakeTradeBrowser([login_tab])
    monkeypatch.setattr(eastmoney_trade, "_get_browser", lambda: browser)
    monkeypatch.setattr(
        eastmoney_trade,
        "_try_fill_login_captcha",
        lambda _tab: {
            "captcha_ocr_text": "9129",
            "captcha_ocr_filled": True,
            "captcha_ocr_error": "",
        },
    )

    state = eastmoney_trade.open_trade_account_page()

    assert state["login_required"] is True
    assert state["captcha_ocr_text"] == "9129"
    assert state["captcha_ocr_filled"] is True
    assert state["captcha_ocr_error"] == ""


def test_extract_captcha_text_from_ocr_document_normalizes_labels():
    document = {
        "shapes": [
            {"label": '{"text":"验"}', "points": [[0, 0], [8, 8]]},
            {"label": '{"text":"9"}', "points": [[10, 0], [18, 8]]},
            {"label": '{"text":"q 2"}', "points": [[20, 0], [36, 8]]},
        ]
    }

    assert eastmoney_trade._extract_captcha_text_from_ocr_document(document) == "9Q2"
