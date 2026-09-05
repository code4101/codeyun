from backend.core.fanxiu.data_annotation.tasks.daily_boss_scan import (
    daily_boss_list_page_fingerprint,
    daily_boss_scan_bound_reason,
)


def test_daily_boss_page_fingerprint_ignores_countdown_digits() -> None:
    assert daily_boss_list_page_fingerprint("玄龟 刷新时间 00:01:09") == (
        daily_boss_list_page_fingerprint("玄龟 刷新时间 00:01:08")
    )
    assert daily_boss_list_page_fingerprint("玄龟 101级 刷新时间 9秒") != (
        daily_boss_list_page_fingerprint("玄龟 102级 刷新时间 8秒")
    )


def test_daily_boss_scan_bounds_fail_closed() -> None:
    seen = {"玄龟刷新时间<倒计时>"}

    assert daily_boss_scan_bound_reason(
        now=11.0,
        deadline=10.0,
        scroll_count=1,
        max_scrolls=16,
    ) == "deadline"
    assert daily_boss_scan_bound_reason(
        now=1.0,
        deadline=10.0,
        scroll_count=2,
        max_scrolls=16,
        page_fingerprint="玄龟刷新时间<倒计时>",
        seen_fingerprints=seen,
    ) == "repeated_page"
    assert daily_boss_scan_bound_reason(
        now=1.0,
        deadline=10.0,
        scroll_count=16,
        max_scrolls=16,
        before_scroll=True,
    ) == "max_scrolls"
