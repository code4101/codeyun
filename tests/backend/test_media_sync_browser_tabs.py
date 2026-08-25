from __future__ import annotations

import ast
import inspect

import pytest

from backend.plugins.modules.media_sync import sources


@pytest.mark.parametrize(
    ("platform", "expected_domain"),
    [
        ("pinterest", "pinterest.com"),
        ("pixiv", "pixiv.net"),
    ],
)
def test_keep_one_platform_tab_uses_its_own_domain(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    expected_domain: str,
) -> None:
    calls: list[tuple[object, str, object]] = []
    browser = object()
    preferred_tab = object()

    monkeypatch.setattr(
        sources,
        "keep_one_domain_tab",
        lambda actual_browser, domain, *, preferred_tab=None: calls.append(
            (actual_browser, domain, preferred_tab)
        ),
    )

    sources.keep_one_platform_tab(browser, platform, preferred_tab=preferred_tab)

    assert calls == [(browser, expected_domain, preferred_tab)]


def test_keep_one_platform_tab_rejects_unknown_platform() -> None:
    with pytest.raises(ValueError, match="不支持的平台标签清理"):
        sources.keep_one_platform_tab(object(), "unknown")


@pytest.mark.parametrize(
    ("sync_function", "expected_platform"),
    [
        (sources.run_pinterest_membership_sync, "pinterest"),
        (sources.run_pixiv_membership_sync, "pixiv"),
    ],
)
def test_membership_sync_reclaims_its_own_platform_tabs(
    sync_function: object,
    expected_platform: str,
) -> None:
    tree = ast.parse(inspect.getsource(sync_function))
    cleanup_platforms = [
        call.args[1].value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "keep_one_platform_tab"
        and len(call.args) >= 2
        and isinstance(call.args[1], ast.Constant)
    ]

    assert cleanup_platforms == [expected_platform, expected_platform]
