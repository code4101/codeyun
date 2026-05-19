from __future__ import annotations

import json
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from DrissionPage import Chromium

from backend.core.ocr_preview import OcrPreviewError, run_paddle_ocr_preview

from .eastmoney_browser import build_chromium_options, ensure_eastmoney_browser_paths


TRADE_POSITION_URL = "https://jywg.18.cn/Search/Position"
HK_POSITION_URL = "https://jywg.18.cn/HKTrade/QueryPosition"
SGT_POSITION_URL = "https://jywg.18.cn/SGTTrade/QueryPosition"
TRADE_HISTORY_DEAL_URL = "https://jywg.18.cn/Search/HisDeal"
HK_HISTORY_DEAL_URL = "https://jywg.18.cn/HKTrade/QueryHistoryDeal"
TRADE_LOGIN_DURATION_TEXT = "3小时"


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
        _select_login_duration(position_tab)
        _try_fill_login_captcha(position_tab)
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


def open_trade_account_page() -> dict[str, Any]:
    browser = _get_browser()
    tab = _open_tab(browser, TRADE_POSITION_URL)
    _wait_for_table_settled(tab, timeout=6)
    status = _read_trade_status(tab)
    login_duration_preset = _select_login_duration(tab) if status["login_required"] else False
    captcha_state = _try_fill_login_captcha(tab) if status["login_required"] else _empty_captcha_state()
    return {
        "title": str(getattr(tab, "title", "") or ""),
        "url": str(getattr(tab, "url", "") or ""),
        "account_label": status["account_label"],
        "login_required": status["login_required"],
        "login_duration_preset": login_duration_preset,
        **captcha_state,
    }


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


def _select_login_duration(tab: Any, target_text: str = TRADE_LOGIN_DURATION_TEXT) -> bool:
    try:
        target_literal = json.dumps(target_text, ensure_ascii=False)
        payload = tab.run_js(
            f"""
const targetText = {target_literal};
let targetInput = null;
for (const label of Array.from(document.querySelectorAll('label'))) {{
  const text = (label.innerText || label.textContent || '').trim();
  if (!text.includes(targetText)) continue;
  targetInput = label.querySelector('input[type="radio"]');
  if (!targetInput && label.htmlFor) {{
    targetInput = document.getElementById(label.htmlFor);
  }}
  if (targetInput) break;
  label.click();
  return JSON.stringify({{ok: true, method: 'label'}});
}}

if (!targetInput) {{
  for (const input of Array.from(document.querySelectorAll('input[type="radio"]'))) {{
    const text = [
      input.value || '',
      input.closest('label')?.innerText || '',
      input.parentElement?.innerText || '',
    ].join(' ');
    if (text.includes(targetText) || /(^|[^0-9])180([^0-9]|$)/.test(text)) {{
      targetInput = input;
      break;
    }}
  }}
}}

if (!targetInput) {{
  return JSON.stringify({{ok: false}});
}}

targetInput.checked = true;
targetInput.click();
targetInput.dispatchEvent(new Event('input', {{bubbles: true}}));
targetInput.dispatchEvent(new Event('change', {{bubbles: true}}));
return JSON.stringify({{ok: true, method: 'input', checked: targetInput.checked, value: targetInput.value || ''}});
"""
        )
        return bool(json.loads(payload or "{}").get("ok"))
    except Exception:
        return False


def _empty_captcha_state() -> dict[str, Any]:
    return {
        "captcha_ocr_text": "",
        "captcha_ocr_filled": False,
        "captcha_ocr_error": "",
    }


def _try_fill_login_captcha(tab: Any) -> dict[str, Any]:
    state = _empty_captcha_state()
    temp_path: Path | None = None
    try:
        temp_path = _capture_login_captcha_image(tab)
        if temp_path is None:
            state["captcha_ocr_error"] = "未找到验证码图片"
            return state

        _prepare_captcha_ocr_image(temp_path)
        preview = run_paddle_ocr_preview(
            temp_path,
            shape_type="rectangle",
            options={
                "use_doc_orientation_classify": False,
                "use_doc_unwarping": False,
                "use_textline_orientation": False,
            },
        )
        captcha_text = _extract_captcha_text_from_ocr_document(preview.get("document") or {})
        if not captcha_text:
            state["captcha_ocr_error"] = "OCR 未识别出验证码"
            return state

        state["captcha_ocr_text"] = captcha_text
        state["captcha_ocr_filled"] = _fill_login_captcha_input(tab, captcha_text)
        if not state["captcha_ocr_filled"]:
            state["captcha_ocr_error"] = "未定位到验证码输入框"
        return state
    except OcrPreviewError as exc:
        state["captcha_ocr_error"] = str(exc)
        return state
    except Exception as exc:
        state["captcha_ocr_error"] = f"验证码自动识别失败：{exc}"
        return state
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _capture_login_captcha_image(tab: Any) -> Path | None:
    selector = _mark_login_captcha_image(tab)
    if not selector:
        return None

    captcha_element = tab.ele(f"css:{selector}", timeout=2)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
        temp_path = Path(temp_file.name)
    screenshot_path = captcha_element.get_screenshot(path=str(temp_path))
    return Path(screenshot_path) if screenshot_path else temp_path


def _prepare_captcha_ocr_image(image_path: Path) -> None:
    try:
        from PIL import Image, ImageEnhance, ImageOps

        with Image.open(image_path) as image:
            prepared = image.convert("RGB")
            prepared = ImageOps.expand(prepared, border=8, fill="white")
            width, height = prepared.size
            scale = 4 if max(width, height) < 240 else 2
            prepared = prepared.resize((width * scale, height * scale), Image.Resampling.LANCZOS)
            prepared = ImageEnhance.Contrast(prepared).enhance(1.6)
            prepared.save(image_path)
    except Exception:
        return


def _mark_login_captcha_image(tab: Any) -> str:
    token = f"codeyun-captcha-{int(time.time() * 1000)}"
    payload = tab.run_js(
        f"""
const token = {json.dumps(token)};
for (const node of document.querySelectorAll('[data-codeyun-captcha-target]')) {{
  node.removeAttribute('data-codeyun-captcha-target');
}}

function visible(node) {{
  const rect = node.getBoundingClientRect();
  const style = window.getComputedStyle(node);
  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
}}

function distance(a, b) {{
  const ax = a.left + a.width / 2;
  const ay = a.top + a.height / 2;
  const bx = b.left + b.width / 2;
  const by = b.top + b.height / 2;
  return Math.hypot(ax - bx, ay - by);
}}

const inputs = Array.from(document.querySelectorAll('input')).filter((input) => {{
  if (!visible(input) || input.disabled || input.readOnly) return false;
  const type = (input.getAttribute('type') || 'text').toLowerCase();
  if (type === 'password' || type === 'hidden' || type === 'radio' || type === 'checkbox') return false;
  const meta = [
    input.id || '',
    input.name || '',
    input.className || '',
    input.placeholder || '',
    input.getAttribute('aria-label') || '',
  ].join(' ').toLowerCase();
  return (input.value || '').length <= 8 || /code|captcha|verify|valid|check|yzm|验证码/.test(meta);
}}).map((input) => ({{node: input, rect: input.getBoundingClientRect()}}));

const candidates = Array.from(document.querySelectorAll('img, canvas')).map((node) => {{
  const rect = node.getBoundingClientRect();
  const meta = [
    node.id || '',
    node.className || '',
    node.getAttribute('alt') || '',
    node.getAttribute('title') || '',
    node.getAttribute('src') || '',
  ].join(' ').toLowerCase();
  if (!visible(node) || rect.width < 30 || rect.width > 180 || rect.height < 15 || rect.height > 80) {{
    return null;
  }}

  let score = /code|captcha|verify|valid|check|yzm|rand|验证码/.test(meta) ? 12 : 0;
  for (const input of inputs) {{
    const dist = distance(rect, input.rect);
    if (dist < 180) score += Math.max(0, 12 - dist / 18);
    if (Math.abs((rect.top + rect.height / 2) - (input.rect.top + input.rect.height / 2)) < 24) score += 8;
    if (rect.left > input.rect.left) score += 2;
  }}
  return {{node, rect, score}};
}}).filter(Boolean).sort((a, b) => b.score - a.score);

const best = candidates[0];
if (!best || best.score < 4) {{
  return JSON.stringify({{ok: false}});
}}

best.node.setAttribute('data-codeyun-captcha-target', token);
return JSON.stringify({{
  ok: true,
  selector: `[data-codeyun-captcha-target="${{token}}"]`,
  width: Math.round(best.rect.width),
  height: Math.round(best.rect.height),
  score: best.score,
}});
"""
    )
    try:
        data = json.loads(payload or "{}")
    except json.JSONDecodeError:
        return ""
    return str(data.get("selector") or "") if data.get("ok") else ""


def _extract_captcha_text_from_ocr_document(document: dict[str, Any]) -> str:
    shapes = document.get("shapes") if isinstance(document, dict) else None
    if not isinstance(shapes, list):
        return ""

    items: list[tuple[float, float, str]] = []
    for shape in shapes:
        if not isinstance(shape, dict):
            continue
        text = _read_ocr_shape_text(shape.get("label"))
        if not text:
            continue
        points = shape.get("points") or []
        xs = [float(point[0]) for point in points if isinstance(point, list | tuple) and len(point) >= 2]
        ys = [float(point[1]) for point in points if isinstance(point, list | tuple) and len(point) >= 2]
        items.append((min(ys, default=0.0), min(xs, default=0.0), text))

    raw_text = "".join(item[2] for item in sorted(items))
    normalized = re.sub(r"[^0-9A-Za-z]", "", raw_text).upper()
    return normalized[:8]


def _read_ocr_shape_text(label: Any) -> str:
    if isinstance(label, dict):
        return str(label.get("text") or "")
    if not isinstance(label, str):
        return ""
    try:
        parsed = json.loads(label)
    except json.JSONDecodeError:
        return label
    if isinstance(parsed, dict):
        return str(parsed.get("text") or "")
    return label


def _fill_login_captcha_input(tab: Any, captcha_text: str) -> bool:
    if not captcha_text:
        return False
    payload = tab.run_js(
        f"""
const captchaText = {json.dumps(captcha_text)};
function visible(node) {{
  const rect = node.getBoundingClientRect();
  const style = window.getComputedStyle(node);
  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
}}

function distance(a, b) {{
  const ax = a.left + a.width / 2;
  const ay = a.top + a.height / 2;
  const bx = b.left + b.width / 2;
  const by = b.top + b.height / 2;
  return Math.hypot(ax - bx, ay - by);
}}

const captchaNode = document.querySelector('[data-codeyun-captcha-target]');
const captchaRect = captchaNode ? captchaNode.getBoundingClientRect() : null;
const candidates = Array.from(document.querySelectorAll('input')).map((input) => {{
  if (!visible(input) || input.disabled || input.readOnly) return null;
  const type = (input.getAttribute('type') || 'text').toLowerCase();
  if (type === 'password' || type === 'hidden' || type === 'radio' || type === 'checkbox') return null;
  const rect = input.getBoundingClientRect();
  const meta = [
    input.id || '',
    input.name || '',
    input.className || '',
    input.placeholder || '',
    input.getAttribute('aria-label') || '',
  ].join(' ').toLowerCase();

  let score = /code|captcha|verify|valid|check|yzm|验证码/.test(meta) ? 30 : 0;
  if ((input.value || '').length <= 8) score += 8;
  if (input.maxLength > 0 && input.maxLength <= 8) score += 8;
  if (captchaRect) {{
    const dist = distance(rect, captchaRect);
    if (dist < 180) score += Math.max(0, 24 - dist / 8);
    if (Math.abs((rect.top + rect.height / 2) - (captchaRect.top + captchaRect.height / 2)) < 24) score += 18;
    if (rect.left < captchaRect.left) score += 4;
  }}
  return {{input, rect, score}};
}}).filter(Boolean).sort((a, b) => b.score - a.score);

const best = candidates[0];
if (!best || best.score < 10) {{
  return JSON.stringify({{ok: false}});
}}

best.input.focus();
best.input.value = captchaText;
best.input.dispatchEvent(new Event('input', {{bubbles: true}}));
best.input.dispatchEvent(new Event('change', {{bubbles: true}}));
best.input.dispatchEvent(new KeyboardEvent('keyup', {{bubbles: true}}));
return JSON.stringify({{ok: true, value: best.input.value || ''}});
"""
    )
    try:
        return bool(json.loads(payload or "{}").get("ok"))
    except json.JSONDecodeError:
        return False


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
