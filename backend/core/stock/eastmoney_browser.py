from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from DrissionPage import Chromium, ChromiumOptions

from backend.core.settings import get_settings


EASTMONEY_HOME_URL = "https://www.eastmoney.com/"
LOGIN_HINT_KEYWORDS = (
    "登录",
    "登入",
    "扫码",
    "验证码",
    "账号",
    "密码",
    "通行证",
)


@dataclass(frozen=True)
class EastmoneyBrowserPaths:
    base_dir: Path
    user_data_dir: Path
    download_dir: Path
    diagnostics_dir: Path


@dataclass(frozen=True)
class PageSnapshot:
    title: str
    url: str
    captured_at: float
    login_hint: bool
    html_sample: str


def get_eastmoney_browser_paths(data_dir: Path | None = None) -> EastmoneyBrowserPaths:
    base_dir = (data_dir or get_settings().data_dir) / "eastmoney"
    return EastmoneyBrowserPaths(
        base_dir=base_dir,
        user_data_dir=base_dir / "chromium-user-data",
        download_dir=base_dir / "downloads",
        diagnostics_dir=base_dir / "diagnostics",
    )


def ensure_eastmoney_browser_paths(paths: EastmoneyBrowserPaths | None = None) -> EastmoneyBrowserPaths:
    resolved = paths or get_eastmoney_browser_paths()
    resolved.user_data_dir.mkdir(parents=True, exist_ok=True)
    resolved.download_dir.mkdir(parents=True, exist_ok=True)
    resolved.diagnostics_dir.mkdir(parents=True, exist_ok=True)
    return resolved


def build_chromium_options(
    paths: EastmoneyBrowserPaths | None = None,
    *,
    headless: bool = False,
    start_maximized: bool = True,
) -> ChromiumOptions:
    resolved = ensure_eastmoney_browser_paths(paths)
    options = ChromiumOptions(read_file=False)
    options.set_paths(
        user_data_path=str(resolved.user_data_dir),
        download_path=str(resolved.download_dir),
    )
    if headless:
        options.headless(True)
    if start_maximized and not headless:
        options.set_argument("--start-maximized")
    return options


def open_eastmoney_browser(
    url: str = EASTMONEY_HOME_URL,
    *,
    headless: bool = False,
    load_mode: str = "eager",
    base_timeout: int = 10,
    page_load_timeout: int = 30,
) -> tuple[Chromium, Any, EastmoneyBrowserPaths]:
    paths = ensure_eastmoney_browser_paths()
    browser = Chromium(build_chromium_options(paths, headless=headless))
    tab = browser.latest_tab
    tab.set.timeouts(base=base_timeout, page_load=page_load_timeout)
    tab.set.load_mode(load_mode)
    tab.get(url)
    return browser, tab, paths


def detect_login_hint(*, title: str, url: str, html_sample: str) -> bool:
    text = "\n".join([title or "", url or "", html_sample or ""])
    return any(keyword in text for keyword in LOGIN_HINT_KEYWORDS)


def capture_tab_snapshot(tab: Any, *, html_limit: int = 1200) -> PageSnapshot:
    html_sample = ""
    try:
        html_sample = str(tab.html or "")[:html_limit]
    except Exception:
        html_sample = ""

    title = str(getattr(tab, "title", "") or "")
    url = str(getattr(tab, "url", "") or "")
    return PageSnapshot(
        title=title,
        url=url,
        captured_at=time.time(),
        login_hint=detect_login_hint(title=title, url=url, html_sample=html_sample),
        html_sample=html_sample,
    )
