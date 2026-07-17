from backend.core.dp_browser_tab_cleanup import (
    DP_BROWSER_TAB_CLEANUP_TASK_KEY,
    DpBrowserTabCleanupConfig,
    plan_dp_browser_tab_cleanup,
)
from backend.core.jobs import scheduler


def _tab(tab_id: str, url: str, *, title: str = "账户中心", attached: bool = False) -> dict:
    return {
        "id": tab_id,
        "type": "page",
        "title": title,
        "url": url,
        "attached": attached,
    }


def _config() -> DpBrowserTabCleanupConfig:
    return DpBrowserTabCleanupConfig(
        allowed_hosts=("pay.weixin.qq.com",),
        min_candidate_age_seconds=60 * 60,
        min_seen_count=2,
        max_close_per_run=10,
    )


def test_duplicate_tab_is_only_closed_after_second_hourly_observation():
    tabs = [
        _tab("old-tab", "https://pay.weixin.qq.com/index.php/core/info"),
        _tab("new-tab", "https://pay.weixin.qq.com/index.php/core/info"),
    ]

    first = plan_dp_browser_tab_cleanup(tabs, {}, now=1000, config=_config())
    assert first.close_ids == []
    assert first.candidate_tabs == 1
    assert first.kept_domain_tabs == {"pay.weixin.qq.com": "new-tab"}

    second = plan_dp_browser_tab_cleanup(tabs, first.state, now=1000 + 60 * 60 + 1, config=_config())
    assert second.close_ids == ["old-tab"]
    assert second.close_reasons["old-tab"].startswith("duplicate url retained")
    assert second.kept_domain_tabs == {"pay.weixin.qq.com": "new-tab"}


def test_attached_duplicate_tab_is_never_closed():
    tabs = [
        _tab("active-tab", "https://pay.weixin.qq.com/index.php/core/info", attached=True),
        _tab("spare-tab", "https://pay.weixin.qq.com/index.php/core/info"),
    ]

    first = plan_dp_browser_tab_cleanup(tabs, {}, now=1000, config=_config())
    second = plan_dp_browser_tab_cleanup(tabs, first.state, now=1000 + 2 * 60 * 60, config=_config())

    assert first.close_ids == []
    assert second.close_ids == []
    assert second.skipped["attached"] == 1


def test_protected_login_or_verify_tab_is_never_closed():
    tabs = [
        _tab("login-a", "https://pay.weixin.qq.com/login", title="微信支付 登录"),
        _tab("login-b", "https://pay.weixin.qq.com/login", title="微信支付 登录"),
    ]

    first = plan_dp_browser_tab_cleanup(tabs, {}, now=1000, config=_config())
    second = plan_dp_browser_tab_cleanup(tabs, first.state, now=1000 + 2 * 60 * 60, config=_config())

    assert first.close_ids == []
    assert second.close_ids == []
    assert second.skipped["protected_keyword"] == 2


def test_unique_urls_on_same_domain_are_not_closed():
    tabs = [
        _tab("home", "https://pay.weixin.qq.com/index.php/core/info"),
        _tab("trade", "https://pay.weixin.qq.com/index.php/trade/index"),
    ]

    first = plan_dp_browser_tab_cleanup(tabs, {}, now=1000, config=_config())
    second = plan_dp_browser_tab_cleanup(tabs, first.state, now=1000 + 2 * 60 * 60, config=_config())

    assert first.candidate_tabs == 0
    assert second.close_ids == []


def test_background_job_is_hidden_catalog_item_with_hourly_default_schedule():
    spec = scheduler.get_background_task_spec(DP_BROWSER_TAB_CLEANUP_TASK_KEY)
    assert spec is not None
    assert spec.default_visible is False
    assert spec.schedule_label == "每小时扫描"

    policy = scheduler._default_background_task_schedule_policy(DP_BROWSER_TAB_CLEANUP_TASK_KEY)
    assert policy is not None
    assert policy["trigger"] == {"type": "interval", "minutes": 60, "anchor": "last_finish"}
