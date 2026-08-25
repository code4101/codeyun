"""Compatibility bridge for shared order automation helpers."""

import os
from pathlib import Path
from typing import Any, Sequence

from kq5034.order_ops import (
    ORDER_SHEET_COLUMNS,
    ORDER_NUMERIC_COLUMNS,
    OrderAutomationError,
    execute_order_action as _execute_order_action,
    query_order_refund_details as _query_order_refund_details,
)


def _normalize_order_id(value: Any) -> str:
    return str(value or "").lstrip("`'").strip()


def _precise_refund_query_type(order_id: str) -> str:
    if order_id.isdigit():
        return "pay_order" if order_id.startswith("42") else "refund_id"
    return "merchant_order"


def _close_extra_weipay_tabs(weipay: Any, *, min_tabs_to_keep: int = 1) -> None:
    """Close duplicate WeChat Pay tabs created by short-lived automation runs."""

    try:
        close_if_exceeds = getattr(weipay, "close_if_exceeds_min_tabs", None)
        if callable(close_if_exceeds):
            close_if_exceeds(min_tabs_to_keep)
            return
    except Exception:
        return

    try:
        browser = getattr(weipay, "browser", None)
        if browser is None:
            return
        tabs = list(browser.get_tabs(url="https://pay.weixin.qq.com") or [])
    except Exception:
        return

    if len(tabs) <= min_tabs_to_keep:
        return

    preferred_tab = getattr(weipay, "tab", None)
    keep_tabs: list[Any] = []
    if preferred_tab in tabs:
        keep_tabs.append(preferred_tab)
    for tab in tabs:
        if len(keep_tabs) >= min_tabs_to_keep:
            break
        if tab not in keep_tabs:
            keep_tabs.append(tab)

    for tab in tabs:
        if tab in keep_tabs:
            continue
        try:
            tab.close()
        except Exception:
            pass


def _auto_weipay_tab_cleanup_enabled() -> bool:
    value = os.getenv("CODEYUN_AUTO_CLEANUP_WEIPAY_TABS")
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _ensure_managed_weipay(
    weipay: Any = None,
    *,
    weipay_login_users: Sequence[str] | None = None,
) -> tuple[Any, bool]:
    if weipay is not None:
        return weipay, False

    from kq5034.weipay import Weipay

    users = [str(user).strip() for user in (weipay_login_users or []) if str(user).strip()]
    return Weipay(users or None), True


def _ensure_legacy_xl_env_loaded() -> None:
    if os.getenv("XL_LINKS"):
        return

    env_file = Path(__file__).resolve().parents[4] / "xlproject" / ".env"
    if not env_file.exists():
        return

    try:
        from dotenv import dotenv_values
    except Exception:
        dotenv_values = None

    if dotenv_values is not None:
        values = dotenv_values(env_file)
    else:
        values = {}
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")

    for key, value in values.items():
        if key and key.startswith("XL_") and value is not None and key not in os.environ:
            os.environ[key] = value


def execute_order_action(
    *,
    action: str,
    rows: Any,
    weipay: Any = None,
    weipay_login_users: Sequence[str] | None = None,
    kqdb: Any = None,
    lookup_mode: Any = "hybrid",
) -> dict[str, Any]:
    _ensure_legacy_xl_env_loaded()
    normalized_action = str(action or "").strip().lower()
    normalized_lookup_mode = str(lookup_mode or "").strip().lower()
    should_preload_weipay = (
        weipay is not None
        or normalized_action == "refund"
        or normalized_lookup_mode in {"browser_only", "hybrid"}
    )
    if should_preload_weipay:
        managed_weipay, owned_weipay = _ensure_managed_weipay(
            weipay,
            weipay_login_users=weipay_login_users,
        )
    else:
        managed_weipay, owned_weipay = None, False
    try:
        return _execute_order_action(
            action=action,
            rows=rows,
            weipay=managed_weipay,
            weipay_login_users=weipay_login_users,
            kqdb=kqdb,
            lookup_mode=lookup_mode,
        )
    finally:
        if owned_weipay and _auto_weipay_tab_cleanup_enabled():
            _close_extra_weipay_tabs(managed_weipay, min_tabs_to_keep=1)


def query_order_refund_details(
    order_id: Any,
    *,
    query_type: Any = "auto",
    weipay=None,
    weipay_login_users: Sequence[str] | None = None,
) -> dict[str, Any]:
    _ensure_legacy_xl_env_loaded()
    managed_weipay, owned_weipay = _ensure_managed_weipay(
        weipay,
        weipay_login_users=weipay_login_users,
    )
    try:
        result = _query_order_refund_details(
            order_id,
            query_type=query_type,
            weipay=managed_weipay,
            weipay_login_users=weipay_login_users,
        )
        if result.get("rows") or str(query_type or "auto").strip().lower() != "auto":
            return result

        normalized_order_id = _normalize_order_id(order_id)
        if not normalized_order_id:
            return result

        precise_type = _precise_refund_query_type(normalized_order_id)
        retry_result = _query_order_refund_details(
            normalized_order_id,
            query_type=precise_type,
            weipay=managed_weipay,
            weipay_login_users=weipay_login_users,
        )
        return retry_result if retry_result.get("rows") else result
    finally:
        if owned_weipay and _auto_weipay_tab_cleanup_enabled():
            _close_extra_weipay_tabs(managed_weipay, min_tabs_to_keep=1)


def cleanup_weipay_tabs(weipay: Any, *, min_tabs_to_keep: int = 1) -> None:
    _close_extra_weipay_tabs(weipay, min_tabs_to_keep=min_tabs_to_keep)

__all__ = [
    "ORDER_NUMERIC_COLUMNS",
    "ORDER_SHEET_COLUMNS",
    "OrderAutomationError",
    "cleanup_weipay_tabs",
    "execute_order_action",
    "query_order_refund_details",
]
