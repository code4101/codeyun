"""Compatibility bridge for shared order automation helpers."""

from typing import Any, Sequence

from kq5034.order_ops import (
    ORDER_SHEET_COLUMNS,
    ORDER_NUMERIC_COLUMNS,
    OrderAutomationError,
    execute_order_action as _execute_order_action,
    find_order_in_db,
    lookup_order,
    process_order_rows,
    query_order_refund_details as _query_order_refund_details,
    sync_kqbook_order_sheet,
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
    except Exception:
        pass

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


def execute_order_action(
    *,
    action: str,
    rows: Any,
    weipay: Any = None,
    weipay_login_users: Sequence[str] | None = None,
    kqdb: Any = None,
    lookup_mode: Any = "hybrid",
) -> dict[str, Any]:
    managed_weipay, owned_weipay = _ensure_managed_weipay(
        weipay,
        weipay_login_users=weipay_login_users,
    )
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
        if owned_weipay:
            _close_extra_weipay_tabs(managed_weipay, min_tabs_to_keep=1)


def query_order_refund_details(
    order_id: Any,
    *,
    query_type: Any = "auto",
    weipay=None,
    weipay_login_users: Sequence[str] | None = None,
) -> dict[str, Any]:
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
        if owned_weipay:
            _close_extra_weipay_tabs(managed_weipay, min_tabs_to_keep=1)


def cleanup_weipay_tabs(weipay: Any, *, min_tabs_to_keep: int = 1) -> None:
    _close_extra_weipay_tabs(weipay, min_tabs_to_keep=min_tabs_to_keep)

__all__ = [
    "ORDER_NUMERIC_COLUMNS",
    "ORDER_SHEET_COLUMNS",
    "OrderAutomationError",
    "cleanup_weipay_tabs",
    "execute_order_action",
    "find_order_in_db",
    "lookup_order",
    "process_order_rows",
    "query_order_refund_details",
    "sync_kqbook_order_sheet",
]
