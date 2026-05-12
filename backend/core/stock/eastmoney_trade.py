from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from DrissionPage import Chromium

from .eastmoney_browser import build_chromium_options, ensure_eastmoney_browser_paths


TRADE_POSITION_URL = "https://jywg.18.cn/Search/Position"
HK_POSITION_URL = "https://jywg.18.cn/HKTrade/QueryPosition"
SGT_POSITION_URL = "https://jywg.18.cn/SGTTrade/QueryPosition"
TRADE_HISTORY_DEAL_URL = "https://jywg.18.cn/Search/HisDeal"
HK_HISTORY_DEAL_URL = "https://jywg.18.cn/HKTrade/QueryHistoryDeal"


@dataclass(frozen=True)
class EastmoneyTable:
    title: str
    columns: list[str]
    rows: list[dict[str, str]]


@dataclass(frozen=True)
class EastmoneyTradeSnapshot:
    captured_at: float
    start_date: str
    end_date: str
    account_label: str
    login_required: bool
    page_title: str
    page_url: str
    summary: dict[str, str]
    positions: EastmoneyTable
    hk_positions: EastmoneyTable
    sgt_positions: EastmoneyTable
    history_deals: EastmoneyTable
    hk_history_deals: EastmoneyTable


class EastmoneyTradeError(RuntimeError):
    pass


def default_history_range() -> tuple[str, str]:
    today = date.today()
    return (today - timedelta(days=7)).isoformat(), today.isoformat()


def read_trade_snapshot(start_date: str | None = None, end_date: str | None = None) -> EastmoneyTradeSnapshot:
    default_start, default_end = default_history_range()
    resolved_start = _normalize_date(start_date) or default_start
    resolved_end = _normalize_date(end_date) or default_end
    _validate_history_range(resolved_start, resolved_end)

    browser = _get_browser()
    position_tab = _open_tab(browser, TRADE_POSITION_URL)
    _wait_for_table_settled(position_tab)
    status = _read_trade_status(position_tab)

    if status["login_required"]:
        empty = EastmoneyTable(title="", columns=[], rows=[])
        return EastmoneyTradeSnapshot(
            captured_at=time.time(),
            start_date=resolved_start,
            end_date=resolved_end,
            account_label=status["account_label"],
            login_required=True,
            page_title=str(getattr(position_tab, "title", "") or ""),
            page_url=str(getattr(position_tab, "url", "") or ""),
            summary={},
            positions=empty,
            hk_positions=empty,
            sgt_positions=empty,
            history_deals=empty,
            hk_history_deals=empty,
        )

    position_tables = _extract_tables(position_tab)
    hk_position_tab = _open_tab(browser, HK_POSITION_URL)
    _wait_for_table_settled(hk_position_tab)
    sgt_position_tab = _open_tab(browser, SGT_POSITION_URL)
    _wait_for_table_settled(sgt_position_tab)

    history_tab = _open_tab(browser, TRADE_HISTORY_DEAL_URL)
    _apply_date_query(history_tab, "#iptStart", "#iptEnd", ".btn_search", resolved_start, resolved_end)
    hk_history_tab = _open_tab(browser, HK_HISTORY_DEAL_URL)
    _apply_date_query(
        hk_history_tab,
        "#qhd_dateStart",
        "#qhd_dateEnd",
        "#qhd_btnConfirm",
        resolved_start,
        resolved_end,
    )

    return EastmoneyTradeSnapshot(
        captured_at=time.time(),
        start_date=resolved_start,
        end_date=resolved_end,
        account_label=status["account_label"],
        login_required=False,
        page_title=str(getattr(position_tab, "title", "") or ""),
        page_url=str(getattr(position_tab, "url", "") or ""),
        summary=_parse_summary_table(position_tables[0] if position_tables else []),
        positions=_table_from_rows("资金持仓", position_tables[1] if len(position_tables) > 1 else []),
        hk_positions=_table_from_rows("沪港通持仓", _table_rows_by_index(hk_position_tab, 1)),
        sgt_positions=_table_from_rows("深港通持仓", _table_rows_by_index(sgt_position_tab, 1)),
        history_deals=_table_from_rows("历史成交", _table_rows_by_index(history_tab, 0)),
        hk_history_deals=_table_from_rows("港股通历史成交", _table_rows_by_index(hk_history_tab, 0)),
    )


def snapshot_to_dict(snapshot: EastmoneyTradeSnapshot) -> dict[str, Any]:
    return {
        "captured_at": snapshot.captured_at,
        "start_date": snapshot.start_date,
        "end_date": snapshot.end_date,
        "account_label": snapshot.account_label,
        "login_required": snapshot.login_required,
        "page_title": snapshot.page_title,
        "page_url": snapshot.page_url,
        "summary": snapshot.summary,
        "positions": _table_to_dict(snapshot.positions),
        "hk_positions": _table_to_dict(snapshot.hk_positions),
        "sgt_positions": _table_to_dict(snapshot.sgt_positions),
        "history_deals": _table_to_dict(snapshot.history_deals),
        "hk_history_deals": _table_to_dict(snapshot.hk_history_deals),
    }


def _get_browser() -> Chromium:
    paths = ensure_eastmoney_browser_paths()
    return Chromium(build_chromium_options(paths, headless=False))


def _open_tab(browser: Chromium, url: str) -> Any:
    for tab in browser.get_tabs():
        if str(getattr(tab, "url", "") or "").startswith(url):
            tab.get(url)
            return tab
    login_tab = _find_trade_login_tab(browser)
    if login_tab is not None:
        return login_tab
    tab = browser.new_tab(url)
    tab.set.timeouts(base=10, page_load=30)
    tab.wait.doc_loaded(timeout=20, raise_err=False)
    return tab


def _find_trade_login_tab(browser: Chromium) -> Any | None:
    for tab in browser.get_tabs():
        tab_url = str(getattr(tab, "url", "") or "")
        if "jywg.18.cn/Login" in tab_url:
            return tab
    return None


def _normalize_date(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise EastmoneyTradeError("日期格式必须是 YYYY-MM-DD")
    return text


def _validate_history_range(start_date: str, end_date: str) -> None:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > end:
        raise EastmoneyTradeError("开始日期不能晚于结束日期")
    if (end - start).days > 100:
        raise EastmoneyTradeError("东方财富历史成交单次查询区间不能超过100天")


def _read_trade_status(tab: Any) -> dict[str, Any]:
    body_text = _body_text(tab)
    account_match = re.search(r"账户：([^\[]+)\s*\[退出\]", body_text)
    return {
        "account_label": account_match.group(1).strip() if account_match else "",
        "login_required": _is_trade_login_page(tab, body_text),
    }


def _is_trade_login_page(tab: Any, body_text: str | None = None) -> bool:
    url = str(getattr(tab, "url", "") or "")
    text = body_text if body_text is not None else _body_text(tab)
    if "/Login" in url and ("交易登录" in text or "请输入资金账号" in text):
        return True
    return "交易登录" in text and "请输入资金账号" in text


def _body_text(tab: Any) -> str:
    try:
        return str(tab.ele("tag:body").text or "")
    except Exception:
        return ""


def _table_rows_by_index(tab: Any, index: int) -> list[list[str]]:
    tables = _extract_tables(tab)
    return tables[index] if 0 <= index < len(tables) else []


def _extract_tables(tab: Any) -> list[list[list[str]]]:
    payload = tab.run_js(
        r"""
return JSON.stringify([...document.querySelectorAll('table')].map((table) => (
  [...table.querySelectorAll('tr')].map((tr) => (
    [...tr.children].map((cell) => cell.innerText.trim()).filter(Boolean)
  )).filter((row) => row.length)
)));
"""
    )
    try:
        return json.loads(payload or "[]")
    except json.JSONDecodeError as exc:
        raise EastmoneyTradeError("解析东方财富表格失败") from exc


def _wait_for_table_settled(tab: Any, timeout: float = 12.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = _body_text(tab)
        if "加载中" not in text:
            return
        time.sleep(0.4)


def _apply_date_query(
    tab: Any,
    start_selector: str,
    end_selector: str,
    button_selector: str,
    start_date: str,
    end_date: str,
) -> None:
    tab.wait.doc_loaded(timeout=20, raise_err=False)
    _set_input_value(tab, start_selector, start_date)
    _set_input_value(tab, end_selector, end_date)
    try:
        button = tab.ele(f"css:{button_selector}", timeout=3)
        button.click()
    except Exception:
        tab.run_js(
            f"document.querySelector({json.dumps(button_selector)})"
            f"?.dispatchEvent(new MouseEvent('click', {{bubbles: true}}));"
        )
    _wait_for_table_settled(tab, timeout=18)


def _set_input_value(tab: Any, selector: str, value: str) -> None:
    tab.run_js(
        f"""
const input = document.querySelector({json.dumps(selector)});
if (input) {{
  input.value = {json.dumps(value)};
  input.dispatchEvent(new Event('input', {{bubbles: true}}));
  input.dispatchEvent(new Event('change', {{bubbles: true}}));
}}
"""
    )


def _parse_summary_table(rows: list[list[str]]) -> dict[str, str]:
    summary: dict[str, str] = {}
    for row in rows:
        for cell in row:
            parts = [part.strip() for part in cell.splitlines() if part.strip()]
            if len(parts) >= 2:
                summary[parts[0].rstrip("：:")] = parts[1]
    return summary


def _table_from_rows(title: str, rows: list[list[str]]) -> EastmoneyTable:
    if not rows:
        return EastmoneyTable(title=title, columns=[], rows=[])

    columns = [_normalize_column_name(item) for item in rows[0]]
    data_rows: list[dict[str, str]] = []
    for row in rows[1:]:
        if not row or any(cell in {"暂无数据...", "加载中..."} for cell in row):
            continue
        data_rows.append(
            {
                columns[index] if index < len(columns) else f"列{index + 1}": cell
                for index, cell in enumerate(row)
            }
        )
    return EastmoneyTable(title=title, columns=columns, rows=data_rows)


def _normalize_column_name(value: str) -> str:
    return re.sub(r"\s+", "", value.strip())


def _table_to_dict(table: EastmoneyTable) -> dict[str, Any]:
    return {
        "title": table.title,
        "columns": table.columns,
        "rows": table.rows,
    }
