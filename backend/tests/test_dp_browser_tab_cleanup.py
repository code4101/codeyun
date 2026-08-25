from backend.core.dp_browser_tab_cleanup import (
    DP_BROWSER_TAB_CLEANUP_TASK_KEY,
    DpBrowserTabCleanupConfig,
    plan_dp_browser_tab_cleanup,
    run_dp_browser_tab_cleanup,
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
    assert second.close_reasons["old-tab"].startswith("duplicate domain retained")
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


def test_stable_tabs_on_same_domain_are_closed_even_when_urls_differ():
    tabs = [
        _tab("home", "https://pay.weixin.qq.com/index.php/core/info"),
        _tab("trade", "https://pay.weixin.qq.com/index.php/trade/index"),
    ]

    first = plan_dp_browser_tab_cleanup(tabs, {}, now=1000, config=_config())
    second = plan_dp_browser_tab_cleanup(tabs, first.state, now=1000 + 2 * 60 * 60, config=_config())

    assert first.candidate_tabs == 1
    assert second.close_ids == ["home"]


def test_changed_tab_observation_resets_candidate_age():
    first_tabs = [
        _tab("home", "https://pay.weixin.qq.com/index.php/core/info", title="加载中"),
        _tab("trade", "https://pay.weixin.qq.com/index.php/trade/index"),
    ]
    second_tabs = [
        _tab("home", "https://pay.weixin.qq.com/index.php/core/info", title="账户中心"),
        _tab("trade", "https://pay.weixin.qq.com/index.php/trade/index"),
    ]

    first = plan_dp_browser_tab_cleanup(first_tabs, {}, now=1000, config=_config())
    second = plan_dp_browser_tab_cleanup(second_tabs, first.state, now=1000 + 2 * 60 * 60, config=_config())

    assert second.close_ids == []
    assert second.state["tabs"]["home"]["candidate_since"] is None


def test_background_job_is_hidden_catalog_item_with_hourly_default_schedule():
    spec = scheduler.get_background_task_spec(DP_BROWSER_TAB_CLEANUP_TASK_KEY)
    assert spec is not None
    assert spec.default_visible is False
    assert spec.schedule_label == "每小时扫描"

    policy = scheduler._default_background_task_schedule_policy(DP_BROWSER_TAB_CLEANUP_TASK_KEY)
    assert policy is not None
    assert policy["trigger"] == {"type": "interval", "minutes": 60, "anchor": "last_finish"}


def test_rime_runtime_work_is_packaged_as_hidden_builtin_jobs():
    refresh_spec = scheduler.get_background_task_spec(scheduler.RIME_CONTEXT_REFRESH_TASK_KEY)
    lint_spec = scheduler.get_background_task_spec(scheduler.RIME_CONTEXT_LINT_TASK_KEY)

    assert refresh_spec is not None
    assert refresh_spec.default_visible is False
    assert refresh_spec.category == "输入法"
    assert lint_spec is not None
    assert lint_spec.default_visible is False
    assert lint_spec.category == "输入法"

    refresh_policy = scheduler._default_background_task_schedule_policy(scheduler.RIME_CONTEXT_REFRESH_TASK_KEY)
    lint_policy = scheduler._default_background_task_schedule_policy(scheduler.RIME_CONTEXT_LINT_TASK_KEY)
    assert refresh_policy is not None
    assert refresh_policy["trigger"] == {"type": "interval", "minutes": 360, "anchor": "last_finish"}
    assert lint_policy is not None
    assert lint_policy["trigger"] == {"type": "daily", "time": "03:20"}


def test_runner_tree_contains_hidden_cleanup_task_for_later_enable(monkeypatch):
    monkeypatch.setattr(scheduler, "_is_task_enabled", lambda task_key: False)
    monkeypatch.setattr(scheduler, "_effective_background_task_schedule_policy", lambda task_key, **kwargs: None)

    tree = scheduler.BackgroundTaskRunner().build_tree()
    labels = {getattr(node, "label", "") for node in scheduler._iter_tree_nodes(tree)}

    assert DP_BROWSER_TAB_CLEANUP_TASK_KEY in labels


def test_run_supports_dry_run_and_max_close_overrides(tmp_path, monkeypatch):
    config = DpBrowserTabCleanupConfig(
        allowed_hosts=("pay.weixin.qq.com",),
        dry_run=False,
        max_close_per_run=10,
        min_seen_count=1,
        min_candidate_age_seconds=0,
    )
    tabs = [
        _tab("old-tab", "https://pay.weixin.qq.com/index.php/core/info"),
        _tab("new-tab", "https://pay.weixin.qq.com/index.php/core/info"),
    ]
    state = {
        "tabs": {
            "old-tab": {
                "id": "old-tab",
                "url": "https://pay.weixin.qq.com/index.php/core/info",
                "title": "账户中心",
                "candidate_since": 1,
                "seen_count": 2,
                "first_seen_at": 1,
                "last_seen_at": 1,
                "last_changed_at": 1,
                "last_observed_index": 0,
            },
            "new-tab": {
                "id": "new-tab",
                "url": "https://pay.weixin.qq.com/index.php/core/info",
                "title": "账户中心",
                "candidate_since": None,
                "seen_count": 2,
                "first_seen_at": 1,
                "last_seen_at": 1,
                "last_changed_at": 1,
                "last_observed_index": 1,
            },
        }
    }
    closed: list[str] = []

    monkeypatch.setattr("backend.core.dp_browser_tab_cleanup.fetch_chrome_debug_tabs", lambda resolved_config: tabs)
    monkeypatch.setattr("backend.core.dp_browser_tab_cleanup.close_chrome_debug_tab", lambda tab_id, resolved_config: closed.append(tab_id))
    monkeypatch.setattr("backend.core.dp_browser_tab_cleanup._read_state", lambda path: state)

    result = run_dp_browser_tab_cleanup(
        config=config,
        state_path=tmp_path / "state.json",
        dry_run=True,
        max_close_per_run=0,
    )

    assert result["status"] == "completed"
    assert result["dry_run"] is True
    assert result["candidate_tabs"] == 1
    assert result["close_ids"] == []
    assert result["closed_ids"] == []
    assert result["close_errors"] == {}
    assert closed == []
