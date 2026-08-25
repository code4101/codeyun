from scripts.download_xiaoe_video_daemon import _cleanup_xiaoe_incremental_tabs


class _Tab:
    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Browser:
    def __init__(self, *tabs: _Tab) -> None:
        self.tabs = list(tabs)

    def get_tabs(self) -> list[_Tab]:
        return self.tabs


def test_cleanup_closes_all_tabs_when_browser_only_contains_xiaoe() -> None:
    video = _Tab("https://admin.xiaoe-tech.com/t/course/video/list")
    text = _Tab("https://admin.xiaoe-tech.com/t/course/text/list")

    assert _cleanup_xiaoe_incremental_tabs(_Browser(video, text)) is True
    assert video.closed is True
    assert text.closed is True


def test_cleanup_preserves_other_business_tabs() -> None:
    xiaoe = _Tab("https://admin.xiaoe-tech.com/t/course/text/list")
    pinterest = _Tab("https://www.pinterest.com/pin/123")

    assert _cleanup_xiaoe_incremental_tabs(_Browser(xiaoe, pinterest)) is False
    assert xiaoe.closed is True
    assert pinterest.closed is False
