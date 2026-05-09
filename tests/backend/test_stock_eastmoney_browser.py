from pathlib import Path
from types import SimpleNamespace

from backend.core.stock.eastmoney_browser import (
    build_chromium_options,
    capture_tab_snapshot,
    detect_login_hint,
    ensure_eastmoney_browser_paths,
    get_eastmoney_browser_paths,
)


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
