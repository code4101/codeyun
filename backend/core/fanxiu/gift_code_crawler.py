"""凡修每周礼包码的 CodeYun 自有解析与 DrissionPage 取码实现。"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode


GIFT_CODE_THREAD_ID = "12289"
GIFT_CODE_THREAD_GROUP_ID = "5636"
GIFT_CODE_THREAD_BASE_URL = "https://forum.odchqpto.com/pages/thread/index"
GIFT_CODE_FORUM_HOME_URL = "https://forum.odchqpto.com/?tgid=5636"
GIFT_CODE_CONTENT_LOCATOR = "t:uni-view@@class=thread-content"

_INVISIBLE_TRANSLATION = dict.fromkeys(
    map(ord, "\u00ad\u034f\u061c\u180e\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2060\ufeff"),
    None,
)
_LABEL_PATTERN = re.compile(
    r"(?i)^[◆◇●○▪▫•·*#\-—]*(?:礼\s*包\s*码|兑\s*换\s*码|gift\s*code|redeem\s*code)\s*[:：=＝\-—]*\s*(?P<code>.+)$",
)
_EXPIRY_PATTERN = re.compile(
    r"^[◆◇●○▪▫•·*#\-—\s]*(?P<code>[\u3400-\u9fffA-Za-z0-9._\-\s]{2,48})\s*[（(].*(?:有效期|截止).*[）)]\s*$",
    re.IGNORECASE,
)
_TRAILING_NOTE_PATTERN = re.compile(r"\s*[（(【\[].*$")
_CODE_PATTERN = re.compile(r"^[\u3400-\u9fffA-Za-z0-9._\-]{2,32}$")
_NON_CODE_PHRASES = (
    "合集",
    "礼包内容",
    "礼包码合集",
    "近期礼包码",
    "限时礼包码",
    "每周礼包码",
    "兑换礼包",
    "兑换须知",
    "点击输入",
    "输入礼包码",
    "有效期",
    "截止",
)


class GiftCodeCrawlerError(RuntimeError):
    """礼包码论坛页面无法形成可靠结果。"""


@dataclass(frozen=True)
class GiftCodeCrawlResult:
    """一次论坛取码结果及最小诊断信息。"""

    codes: tuple[str, ...]
    url: str
    title: str
    text_length: int


def gift_code_thread_url() -> str:
    """构造不含账号 token 和设备信息的稳定帖子链接。"""

    return GIFT_CODE_THREAD_BASE_URL + "?" + urlencode(
        {
            "tgid": GIFT_CODE_THREAD_GROUP_ID,
            "backUrl": GIFT_CODE_FORUM_HOME_URL,
            "id": GIFT_CODE_THREAD_ID,
        },
    )


def _normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).translate(_INVISIBLE_TRANSLATION)


def _normalize_candidate(value: str) -> str:
    candidate = _TRAILING_NOTE_PATTERN.sub("", _normalize_text(value)).strip(" \t:：=,，。;；|｜◆◇●○▪▫•·*#-—")
    candidate = re.sub(r"\s+", "", candidate)
    return candidate


def _valid_candidate(candidate: str) -> bool:
    if not candidate or not _CODE_PATTERN.fullmatch(candidate):
        return False
    return not any(phrase in candidate for phrase in _NON_CODE_PHRASES)


def extract_gift_codes(text: str) -> list[str]:
    """从论坛正文中按出现顺序提取并去重礼包码。"""

    codes: list[str] = []
    seen: set[str] = set()
    for raw_line in _normalize_text(text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        compact_line = re.sub(r"\s+", "", line)
        match = _LABEL_PATTERN.search(compact_line)
        if match:
            candidate = _normalize_candidate(match.group("code"))
        else:
            match = _EXPIRY_PATTERN.match(compact_line)
            candidate = _normalize_candidate(match.group("code")) if match else ""
        if _valid_candidate(candidate) and candidate not in seen:
            codes.append(candidate)
            seen.add(candidate)
    return codes


def crawl_weekly_gift_codes(
    *,
    timeout_seconds: float = 20.0,
    browser_factory: Callable[[], Any] | None = None,
    check_cancel: Callable[[], None] | None = None,
) -> GiftCodeCrawlResult:
    """使用统一 DrissionPage Chrome 读取每周礼包码帖子。

    本函数始终只创建一个后台工作 tab，并在不关闭共享浏览器最后一个 tab 的
    前提下回收它。登录墙、正文超时和空解析结果都失败关闭。
    """

    if browser_factory is None:
        from pyxllib.ext.drissionlib import Chromium

        browser_factory = Chromium

    browser = browser_factory()
    url = gift_code_thread_url()
    tab = browser.new_tab(url, background=True)
    text = ""
    title = ""
    try:
        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        while time.monotonic() < deadline:
            if check_cancel is not None:
                check_cancel()
            title = str(getattr(tab, "title", "") or "")
            current_url = str(getattr(tab, "url", "") or "")
            html = str(getattr(tab, "html", "") or "")
            if "/vip/login/" in current_url or ("短信登录" in html and "获取验证码" in html):
                raise GiftCodeCrawlerError("统一 DP Chrome 的凡修论坛登录态已失效，需要人工重新登录")
            content = tab.ele(GIFT_CODE_CONTENT_LOCATOR, timeout=1)
            text = str(getattr(content, "text", "") or "") if content else ""
            codes = extract_gift_codes(text)
            if codes:
                return GiftCodeCrawlResult(tuple(codes), current_url or url, title, len(text))
            time.sleep(0.25)
        raise GiftCodeCrawlerError(
            f"凡修礼包码正文等待超时或未解析到兑换码：title={title!r}, url={getattr(tab, 'url', '')!s}, text_length={len(text)}",
        )
    finally:
        try:
            if int(getattr(browser, "tabs_count", 0) or 0) > 1:
                browser.close_tabs(tab)
        except Exception:
            # tab 回收失败不能掩盖更重要的业务取码结果或原始异常。
            pass
