from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.fanxiu.gift_code_crawler import (
    GiftCodeCrawlerError,
    crawl_weekly_gift_codes,
    extract_gift_codes,
    gift_code_thread_url,
)


CURRENT_WEEKLY_POST = """
兑换码合集，超全福利，持续更新中
💙近期礼包码合集：（每周更新）
限时礼包码：
◆ 礼包码：秋风初至
◆ 礼包内容：神元玄露*1、锐攻丹·珍*1、补天灵珠*5
◆ 有效期：8月3日——8月9日
每周礼包码：
晚风知秋（有效期至2026.8.9）
伏尽待秋（有效期至2026.8.9）
荷晚留香（有效期至2026.8.9）
夏阑蝉息（有效期至2026.8.9）
暮色纳凉（有效期至2026.8.9）
👉指路：游戏主界面右下角展开-设置-兑换礼包，输入礼包码兑换即可
"""


def test_extract_gift_codes_from_current_weekly_post() -> None:
    assert extract_gift_codes(CURRENT_WEEKLY_POST) == [
        "秋风初至",
        "晚风知秋",
        "伏尽待秋",
        "荷晚留香",
        "夏阑蝉息",
        "暮色纳凉",
    ]


def test_extract_gift_codes_tolerates_spacing_case_punctuation_and_invisible_chars() -> None:
    text = """
    礼 包 码 ： 秋 风 初 至
    GIFT CODE: AbC-123
    redeem code ＝ xy_Z.9
    周末好礼 ( 有 效 期 至 2026-08-09 )
    礼包码：A\u200bBC123
    ABC123（有效期至2026.8.9）
    """

    assert extract_gift_codes(text) == ["秋风初至", "AbC-123", "xy_Z.9", "周末好礼", "ABC123"]


def test_gift_code_thread_url_contains_no_account_token_or_device_data() -> None:
    url = gift_code_thread_url()

    assert "id=12289" in url
    assert "backUrl=" in url
    assert "token=" not in url.lower()
    assert "dev=" not in url.lower()


class _Element:
    def __init__(self, text: str) -> None:
        self.text = text


class _Tab:
    title = "凡人修仙传"
    url = gift_code_thread_url()
    html = ""

    def __init__(self, text: str) -> None:
        self._text = text

    def ele(self, _locator: str, timeout: float):
        assert timeout == 1
        return _Element(self._text)


class _Browser:
    def __init__(self, text: str) -> None:
        self.tabs_count = 2
        self.tab = _Tab(text)
        self.closed = []

    def new_tab(self, url: str, background: bool):
        assert url == gift_code_thread_url()
        assert background is True
        return self.tab

    def close_tabs(self, tab) -> None:
        self.closed.append(tab)


def test_crawler_returns_codes_and_reclaims_its_work_tab() -> None:
    browser = _Browser(CURRENT_WEEKLY_POST)

    result = crawl_weekly_gift_codes(browser_factory=lambda: browser)

    assert list(result.codes) == extract_gift_codes(CURRENT_WEEKLY_POST)
    assert result.text_length == len(CURRENT_WEEKLY_POST)
    assert browser.closed == [browser.tab]


def test_crawler_fails_closed_and_still_reclaims_tab() -> None:
    browser = _Browser("来自")

    with pytest.raises(GiftCodeCrawlerError, match="未解析到兑换码"):
        crawl_weekly_gift_codes(timeout_seconds=0.01, browser_factory=lambda: browser)

    assert browser.closed == [browser.tab]


def test_codeyun_crawler_has_no_xlsln_dependency() -> None:
    source = Path(__file__).parents[1] / "core" / "fanxiu" / "gift_code_crawler.py"
    assert "xlsln" not in source.read_text(encoding="utf-8")
