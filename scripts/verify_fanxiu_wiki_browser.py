from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import json
import os
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path
from typing import Any

import requests
import websockets

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.fanxiu_resources import resolve_fanxiu_export_root


DEFAULT_FRONTEND_BASE = "http://127.0.0.1:5173"
DEFAULT_API_BASE = "http://127.0.0.1:8000/api"
DEFAULT_CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
AUTH_TOKEN_ENV = "CODEYUN_AUTH_TOKEN"
REFRESH_TOKEN_ENV = "CODEYUN_REFRESH_TOKEN"
CORE_WIKI_TABS = {"gongfa", "activity", "lingjie", "digitdoor", "doupotd"}
CORE_WIKI_TABS_REQUIRING_VISIBLE_IMAGES = {"gongfa", "lingjie", "digitdoor", "doupotd"}


def _create_local_access_token(username: str) -> str:
    """Create a short-lived token for local browser verification."""
    from sqlmodel import Session, select

    from backend.core.auth import create_access_token
    from backend.db import engine
    from backend.models import User

    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == username)).first()
        if user is None:
            raise RuntimeError(f"local auth user not found: {username}")
        if not user.is_active:
            raise RuntimeError(f"local auth user is inactive: {username}")
    return create_access_token({"sub": username}, expires_delta=timedelta(minutes=30))


async def _cdp_call(ws: Any, seq: int, method: str, params: dict[str, Any] | None = None) -> Any:
    await ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
    while True:
        message = json.loads(await ws.recv())
        if message.get("id") == seq:
            if "error" in message:
                raise RuntimeError(message["error"])
            return message.get("result") or {}


def _write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _check_image_url(src: str, timeout: int = 20) -> dict[str, Any]:
    try:
        response = requests.get(src, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "status_code": "", "content_type": "", "size": 0, "detail": str(exc)}
    content_type = response.headers.get("content-type", "")
    ok = response.status_code == 200 and content_type.startswith("image/")
    detail = ""
    if not ok:
        try:
            detail = response.json().get("detail", "")
        except Exception:
            detail = response.text[:240]
    return {
        "ok": ok,
        "status_code": response.status_code,
        "content_type": content_type,
        "size": len(response.content),
        "detail": detail,
    }


def _fetch_item_type_filter(api_base: str, item_type_label: str) -> dict[str, str]:
    label = str(item_type_label or "").strip()
    if not label:
        return {}
    base = api_base.rstrip("/")
    response = requests.get(
        f"{base}/fanxiu/resources/items/cards",
        params={"limit": 1, "offset": 0, "include_facets": "true"},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    type_options = data.get("type_options") or []
    type_key = ""
    for option in type_options:
        option_label = str(option.get("label") or "").strip()
        if option_label == label or option_label.startswith(label):
            type_key = str(option.get("value") or "").strip()
            break
    if not type_key:
        raise RuntimeError(f"item type label not found through API: {label}")
    sub_type_key = ""
    sub_type_options = data.get("sub_type_options") or []
    for option in sub_type_options:
        option_label = str(option.get("label") or "").strip()
        option_value = str(option.get("value") or "").strip()
        if option_value.startswith(f"{type_key}:") and (option_label == f"{label} · 通用" or option_label.startswith(label)):
            sub_type_key = option_value
            break
    return {"label": label, "type_key": type_key, "sub_type_key": sub_type_key}


def _fetch_expected_item_icons(api_base: str, item_type_label: str) -> list[dict[str, str]]:
    item_filter = _fetch_item_type_filter(api_base, item_type_label)
    type_key = item_filter.get("type_key", "")
    if not type_key:
        return []
    base = api_base.rstrip("/")
    response = requests.get(
        f"{base}/fanxiu/resources/items/cards",
        params={"limit": 200, "offset": 0, "include_facets": "false", "type_key": type_key},
        timeout=30,
    )
    response.raise_for_status()
    rows = response.json().get("items") or []
    result: list[dict[str, str]] = []
    for row in rows:
        item_id = str(row.get("id") or "").strip()
        title = str(row.get("name") or "").strip()
        icon = str(row.get("icon") or row.get("small_icon") or "").strip()
        if title and icon:
            result.append({"id": item_id, "title": title, "icon": icon})
    return result


def _annotate_expected_item_icons(
    rows: list[dict[str, Any]],
    expected_icons: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if not expected_icons:
        return rows
    expected_by_id = {str(item.get("id") or "").strip(): item for item in expected_icons if str(item.get("id") or "").strip()}
    annotated: list[dict[str, Any]] = []
    for row in rows:
        row_index = int(row.get("index") or 0)
        row_id = str(row.get("item_id") or "").strip()
        row_title = str(row.get("title") or "").strip()
        expected_icon = ""
        expected_id = ""
        if row_id and row_id in expected_by_id:
            expected = expected_by_id[row_id]
            expected_id = str(expected.get("id") or "").strip()
            expected_icon = expected.get("icon", "")
        if not expected_icon and 0 <= row_index < len(expected_icons) and expected_icons[row_index].get("title") == row_title:
            expected = expected_icons[row_index]
            expected_id = str(expected.get("id") or "").strip()
            expected_icon = expected.get("icon", "")
        if not expected_icon:
            expected = next((item for item in expected_icons if item.get("title") == row_title), {})
            expected_id = str(expected.get("id") or "").strip()
            expected_icon = expected.get("icon", "")
        src = str(row.get("src") or "")
        annotated.append(
            {
                **row,
                "expected_item_id": expected_id,
                "expected_icon": expected_icon,
                "expected_icon_match": bool(expected_icon and expected_icon in src),
            }
        )
    return annotated


def _safe_name(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value.strip())
    return text.strip("._") or "page"


def _chrome_port() -> int:
    return 9400 + int(time.time() * 1000) % 500


async def _open_chrome(chrome_path: str, frontend_base: str, width: int, height: int) -> tuple[subprocess.Popen[bytes], str]:
    port = _chrome_port()
    profile = Path(tempfile.gettempdir()) / "codeyun" / f"chrome-fanxiu-wiki-browser-audit-{port}"
    profile.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            chrome_path,
            "--headless=new",
            "--disable-gpu",
            "--disable-extensions",
            "--no-first-run",
            "--no-default-browser-check",
            f"--window-size={width},{height}",
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            frontend_base.rstrip("/") + "/",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    list_url = f"http://127.0.0.1:{port}/json/list"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(list_url, timeout=1) as response:
                targets = json.loads(response.read().decode("utf-8"))
                pages = [target for target in targets if target.get("type") == "page"]
                if pages:
                    return process, pages[0]["webSocketDebuggerUrl"]
        except Exception:
            await asyncio.sleep(0.1)
    process.terminate()
    raise RuntimeError("Chrome DevTools endpoint did not start")


async def _evaluate_json(ws: Any, seq_ref: list[int], expression: str) -> Any:
    seq = seq_ref[0]
    seq_ref[0] += 1
    result = await _cdp_call(ws, seq, "Runtime.evaluate", {"expression": expression, "returnByValue": True})
    return ((result or {}).get("result") or {}).get("value")


async def _evaluate_json_await(ws: Any, seq_ref: list[int], expression: str) -> Any:
    seq = seq_ref[0]
    seq_ref[0] += 1
    result = await _cdp_call(
        ws,
        seq,
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
    )
    return ((result or {}).get("result") or {}).get("value")


async def _wait_after_navigation(ws: Any, seq_ref: list[int], wait_ms: int) -> None:
    await asyncio.sleep(wait_ms / 1000)
    for _ in range(20):
        state = await _evaluate_json(
            ws,
            seq_ref,
            """
(() => {
  const loading = document.querySelector('.el-loading-mask:not([style*="display: none"])');
  const rows = document.querySelectorAll('.object-row, .mail-table tbody tr').length;
  const path = location.pathname + location.search;
  return { loading: Boolean(loading), rows, path };
})()
""",
        )
        if state and not state.get("loading") and (state.get("rows") or 0) > 0:
            return
        await asyncio.sleep(0.5)


async def _apply_item_type_filter(
    ws: Any,
    seq_ref: list[int],
    *,
    label: str,
    wait_ms: int,
) -> dict[str, Any]:
    label_text = str(label or "").strip()
    if not label_text:
        return {"requested": "", "clicked": False, "skipped": True}
    return await _evaluate_json_await(
        ws,
        seq_ref,
        f"""
(async () => {{
  const requested = {json.dumps(label_text)};
  const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  const facetRows = () => Array.from(document.querySelectorAll('.facet-row'));
  const typeRow = () => facetRows().find(row => (row.querySelector('.facet-label')?.innerText || '').trim() === '类型');
  const visibleTitles = () => Array.from(document.querySelectorAll('.object-row-title')).slice(0, 20).map(el => el.innerText || '');
  const row = typeRow();
  const buttons = row ? Array.from(row.querySelectorAll('button')) : [];
  const target = buttons.find(button => (button.innerText || '').includes(requested));
  if (!target) {{
    return {{
      requested,
      clicked: false,
      reason: 'target_not_found',
      facet_text: row ? row.innerText.slice(0, 1200) : '',
      titles: visibleTitles(),
    }};
  }}
  const before = visibleTitles().join('\\n');
  target.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, view: window }}));
  const deadline = Date.now() + {max(1000, wait_ms)};
  let state = {{}};
  while (Date.now() < deadline) {{
    await sleep(250);
    const loading = Boolean(document.querySelector('.el-loading-mask:not([style*="display: none"])'));
    const active = Boolean(target.classList.contains('active'));
    const titles = visibleTitles();
    const after = titles.join('\\n');
    state = {{ loading, active, titles }};
    if (active && !loading && titles.length > 0 && after !== before) break;
  }}
  return {{
    requested,
    clicked: true,
    active: Boolean(target.classList.contains('active')),
    loading: Boolean(document.querySelector('.el-loading-mask:not([style*="display: none"])')),
    titles: visibleTitles(),
    state,
  }};
}})()
""",
    ) or {}


async def _collect_item_row_icon_rows(ws: Any, seq_ref: list[int], tab: str, sample_step: int) -> list[dict[str, Any]]:
    rows = await _evaluate_json(
        ws,
        seq_ref,
        """
(() => Array.from(document.querySelectorAll('.object-row')).slice(0, 80).map((row, index) => {
  const itemId = row.getAttribute('data-item-id') || '';
  const itemIcon = row.getAttribute('data-item-icon') || '';
  const title = row.querySelector('.object-row-title')?.innerText || '';
  const fallbackEl = row.querySelector('.icon-fallback');
  const fallback = fallbackEl?.innerText || '';
  const img = row.querySelector('.object-row-icon img');
  const rowRect = row.getBoundingClientRect();
  const rect = img ? img.getBoundingClientRect() : null;
  const fallbackRect = fallbackEl ? fallbackEl.getBoundingClientRect() : null;
  const scroller = row.closest('.object-list-scroll');
  const scrollerRect = scroller ? scroller.getBoundingClientRect() : { left: 0, top: 0, right: innerWidth, bottom: innerHeight };
  const intersects = (a, b) => Boolean(a && a.width > 0 && a.height > 0 && a.right > b.left && a.left < b.right && a.bottom > b.top && a.top < b.bottom);
  const rowVisible = intersects(rowRect, scrollerRect) && rowRect.bottom > 0 && rowRect.top < innerHeight && rowRect.right > 0 && rowRect.left < innerWidth;
  const imgVisible = intersects(rect, scrollerRect) && rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth;
  const fallbackStyle = fallbackEl ? getComputedStyle(fallbackEl) : null;
  const fallbackVisible = Boolean(fallbackEl && intersects(fallbackRect, scrollerRect) && fallbackStyle && fallbackStyle.display !== 'none' && fallbackStyle.visibility !== 'hidden' && Number(fallbackStyle.opacity || '1') > 0);
  return {
    index,
    item_id: itemId,
    item_icon: itemIcon,
    title,
    fallback,
    fallback_visible: fallbackVisible,
    row_visible: rowVisible,
    src: img ? (img.currentSrc || img.src || '') : '',
    complete: img ? Boolean(img.complete) : false,
    naturalWidth: img ? (img.naturalWidth || 0) : 0,
    naturalHeight: img ? (img.naturalHeight || 0) : 0,
    display: img ? getComputedStyle(img).display : '',
    visible: Boolean(img && imgVisible),
  };
}))()
""",
    )
    return [{"tab": tab, "sample_step": sample_step, **row} for row in (rows or [])]


async def _collect_mail_rows(ws: Any, seq_ref: list[int], tab: str) -> list[dict[str, Any]]:
    rows = await _evaluate_json(
        ws,
        seq_ref,
        """
(() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && rect.y >= 0 && rect.y <= innerHeight && rect.x >= 0 && rect.x <= innerWidth;
  };
  const tableRows = Array.from(document.querySelectorAll('.mail-table tbody tr'));
  const rewardSlots = Array.from(document.querySelectorAll('.mail-reward-slot'));
  const allRewardImages = Array.from(document.querySelectorAll('.mail-reward-slot img'));
  const visibleRewardImages = allRewardImages.filter(visible);
  const brokenVisibleRewardImages = visibleRewardImages.filter(img => !img.complete || !img.naturalWidth || !img.naturalHeight || getComputedStyle(img).display === 'none');
  const links = Array.from(document.querySelectorAll('a.mail-reward-slot'));
  const contentButtons = Array.from(document.querySelectorAll('.mail-content-cell button'));
  const missingIconSlots = rewardSlots.filter(slot => !slot.querySelector('img'));
  const emptyAltImages = allRewardImages.filter(img => !(img.alt || '').trim());
  const invalidItemLinks = links.filter(link => !(link.getAttribute('href') || '').startsWith('/fanxiu-resource/item/'));
  const rowSamples = tableRows.slice(0, 12).map((row, index) => {
    const cells = Array.from(row.querySelectorAll('td')).map(cell => (cell.innerText || '').trim().replace(/\\s+/g, ' '));
    const imgs = Array.from(row.querySelectorAll('.mail-reward-slot img')).map(img => ({
      src: img.currentSrc || img.src || '',
      alt: img.alt || '',
      complete: Boolean(img.complete),
      naturalWidth: img.naturalWidth || 0,
      naturalHeight: img.naturalHeight || 0,
      display: getComputedStyle(img).display,
    }));
    const hrefs = Array.from(row.querySelectorAll('a.mail-reward-slot')).map(link => link.getAttribute('href') || '');
    return {
      index,
      title: cells[1] || '',
      time: cells[2] || '',
      rewards_text: cells[3] || '',
      content: cells[4] || '',
      status: cells[5] || '',
      image_count: imgs.length,
      broken_image_count: imgs.filter(img => !img.complete || !img.naturalWidth || !img.naturalHeight || img.display === 'none').length,
      hrefs: hrefs.join(' | '),
      first_image_src: imgs[0]?.src || '',
      first_image_alt: imgs[0]?.alt || '',
      first_image_size: imgs[0] ? `${imgs[0].naturalWidth}x${imgs[0].naturalHeight}` : '',
    };
  });
  return [{
    page_status: (document.querySelector('.mail-pagination .pager-status')?.innerText || '').trim().replace(/\\s+/g, ' '),
    next_disabled: Boolean(document.querySelector('.mail-pagination button[aria-label="下一页"]')?.disabled),
    row_count: tableRows.length,
    reward_image_count: allRewardImages.length,
    visible_reward_image_count: visibleRewardImages.length,
    broken_visible_reward_image_count: brokenVisibleRewardImages.length,
    reward_slot_count: rewardSlots.length,
    missing_icon_slot_count: missingIconSlots.length,
    empty_alt_image_count: emptyAltImages.length,
    invalid_item_link_count: invalidItemLinks.length,
    reward_link_count: links.length,
    item_link_count: links.filter(link => (link.getAttribute('href') || '').startsWith('/fanxiu-resource/item/')).length,
    content_button_count: contentButtons.length,
    page_size_text: Array.from(document.querySelectorAll('.mail-pagination, .page-size-select')).map(el => el.innerText || '').join(' ').trim(),
    samples_json: JSON.stringify(rowSamples),
  }];
})()
""",
    )
    return [{"tab": tab, **row} for row in (rows or [])]


async def _advance_mail_page(ws: Any, seq_ref: list[int], wait_ms: int) -> dict[str, Any]:
    return await _evaluate_json_await(
        ws,
        seq_ref,
        f"""
(async () => {{
  const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  const statusText = () => (document.querySelector('.mail-pagination .pager-status')?.innerText || '').trim().replace(/\\s+/g, ' ');
  const bodyText = () => Array.from(document.querySelectorAll('.mail-table tbody tr')).map(row => row.innerText || '').join('\\n').slice(0, 2000);
  const beforeStatus = statusText();
  const beforeBody = bodyText();
  const button = document.querySelector('.mail-pagination button[aria-label="下一页"]');
  if (!button) return {{ clicked: false, reason: 'next_button_missing', before_status: beforeStatus }};
  if (button.disabled) return {{ clicked: false, reason: 'next_button_disabled', before_status: beforeStatus }};
  button.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, view: window }}));
  const deadline = Date.now() + {max(1000, wait_ms)};
  let afterStatus = beforeStatus;
  while (Date.now() < deadline) {{
    await sleep(200);
    afterStatus = statusText();
    const afterBody = bodyText();
    const loading = Boolean(document.querySelector('.el-loading-mask:not([style*="display: none"])'));
    if (!loading && (afterStatus !== beforeStatus || afterBody !== beforeBody)) {{
      return {{ clicked: true, before_status: beforeStatus, after_status: afterStatus }};
    }}
  }}
  return {{ clicked: true, timed_out: true, before_status: beforeStatus, after_status: afterStatus }};
}})()
""",
    ) or {}


async def _wait_mail_reward_images(ws: Any, seq_ref: list[int], wait_ms: int) -> dict[str, Any]:
    return await _evaluate_json_await(
        ws,
        seq_ref,
        f"""
(async () => {{
  const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  const state = () => {{
    const images = Array.from(document.querySelectorAll('.mail-reward-slot img'));
    const pending = images.filter(img => !img.complete || !img.naturalWidth || !img.naturalHeight || getComputedStyle(img).display === 'none');
    return {{ image_count: images.length, pending_count: pending.length }};
  }};
  const deadline = Date.now() + {max(1000, wait_ms)};
  let current = state();
  while (Date.now() < deadline) {{
    current = state();
    if (!current.pending_count) return {{ ...current, ok: true }};
    await sleep(150);
  }}
  return {{ ...current, ok: false, timed_out: true }};
}})()
""",
    ) or {}


async def _wait_item_row_images(ws: Any, seq_ref: list[int], wait_ms: int) -> dict[str, Any]:
    return await _evaluate_json_await(
        ws,
        seq_ref,
        f"""
(async () => {{
  const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  const intersects = (a, b) => Boolean(a && a.width > 0 && a.height > 0 && a.right > b.left && a.left < b.right && a.bottom > b.top && a.top < b.bottom);
  const state = () => {{
    const scroller = document.querySelector('.object-list-scroll');
    const scrollerRect = scroller ? scroller.getBoundingClientRect() : {{ left: 0, top: 0, right: innerWidth, bottom: innerHeight }};
    const visibleRows = Array.from(document.querySelectorAll('.object-row')).filter(row => {{
      const rect = row.getBoundingClientRect();
      return intersects(rect, scrollerRect) && rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth;
    }});
    const images = visibleRows.map(row => row.querySelector('.object-row-icon img')).filter(Boolean);
    const pending = images.filter(img => !img.complete || !img.naturalWidth || !img.naturalHeight || getComputedStyle(img).display === 'none');
    const rowsMissingId = visibleRows.filter(row => !(row.getAttribute('data-item-id') || '').trim());
    return {{ row_count: visibleRows.length, image_count: images.length, pending_count: pending.length, rows_missing_id_count: rowsMissingId.length }};
  }};
  const deadline = Date.now() + {max(1000, wait_ms)};
  let current = state();
  while (Date.now() < deadline) {{
    current = state();
    if (!current.pending_count) return {{ ...current, ok: true }};
    await sleep(150);
  }}
  return {{ ...current, ok: false, timed_out: true }};
}})()
""",
    ) or {}


async def _collect_mail_rows_across_pages(
    ws: Any,
    seq_ref: list[int],
    *,
    tab: str,
    wait_ms: int,
    page_limit: int,
) -> list[dict[str, Any]]:
    if tab != "mail":
        return []
    rows: list[dict[str, Any]] = []
    for page_index in range(max(1, page_limit)):
        image_wait = await _wait_mail_reward_images(ws, seq_ref, wait_ms)
        page_rows = await _collect_mail_rows(ws, seq_ref, tab)
        for row in page_rows:
            rows.append(
                {
                    "page_index": page_index + 1,
                    "image_wait_ok": image_wait.get("ok"),
                    "image_wait_pending_count": image_wait.get("pending_count", 0),
                    **row,
                }
            )
        current = page_rows[0] if page_rows else {}
        if current.get("next_disabled"):
            break
        advanced = await _advance_mail_page(ws, seq_ref, wait_ms)
        if not advanced.get("clicked") or advanced.get("timed_out"):
            rows.append(
                {
                    "tab": tab,
                    "page_index": page_index + 1,
                    "page_advance_error": json.dumps(advanced, ensure_ascii=False),
                    "row_count": 0,
                    "reward_image_count": 0,
                    "visible_reward_image_count": 0,
                    "broken_visible_reward_image_count": 0,
                    "reward_slot_count": 0,
                    "missing_icon_slot_count": 0,
                    "empty_alt_image_count": 0,
                    "invalid_item_link_count": 0,
                    "reward_link_count": 0,
                    "item_link_count": 0,
                    "content_button_count": 0,
                    "page_size_text": "",
                    "samples_json": "[]",
                }
            )
            break
    return rows


async def _probe_mail_interactions(ws: Any, seq_ref: list[int], tab: str) -> dict[str, Any]:
    if tab != "mail":
        return {}
    result = await _evaluate_json_await(
        ws,
        seq_ref,
        """
(async () => {
  const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  const result = {
    content_clicked: false,
    content_text: '',
    content_title: '',
    first_item_href: '',
    origin: location.origin,
    item_link_clicked: false,
    item_route_path: '',
    item_route_has_name: false,
  };
  const button = document.querySelector('.mail-content-cell button');
  if (button) {
    button.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    await sleep(500);
    result.content_clicked = true;
    result.content_text = (document.querySelector('.mail-content-body')?.innerText || '').trim();
    result.content_title = (document.querySelector('.mail-content-dialog .el-dialog__title')?.innerText || '').trim();
    const close = document.querySelector('.mail-content-dialog .el-dialog__headerbtn');
    if (close) close.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    await sleep(200);
  }
  const link = document.querySelector('a.mail-reward-slot[href^="/fanxiu-resource/item/"]');
  if (link) {
    result.first_item_href = link.getAttribute('href') || '';
  }
  return result;
})()
""",
    ) or {}
    href = str(result.get("first_item_href") or "").strip()
    if href:
        origin = str(result.get("origin") or "").rstrip("/")
        seq = seq_ref[0]
        seq_ref[0] += 1
        await _cdp_call(ws, seq, "Page.navigate", {"url": origin + href if href.startswith("/") and origin else href})
        await asyncio.sleep(2)
        route_state = await _evaluate_json(
            ws,
            seq_ref,
            """
(() => {
  const bodyText = document.body ? document.body.innerText : '';
  return {
    path: location.pathname,
    has_name: Boolean(bodyText.trim()) && !bodyText.includes('Not Found') && !bodyText.includes('404'),
  };
})()
""",
        ) or {}
        result["item_link_clicked"] = True
        result["item_route_path"] = route_state.get("path") or ""
        result["item_route_has_name"] = bool(route_state.get("has_name"))
    return result


async def _scan_tab(
    ws: Any,
    seq_ref: list[int],
    *,
    frontend_base: str,
    api_base: str,
    tab: str,
    auth_token: str,
    refresh_token: str,
    output_dir: Path,
    wait_ms: int,
    scroll_steps: int,
    mail_page_limit: int,
    screenshot: bool,
    item_type_label: str,
) -> dict[str, Any]:
    item_filter = _fetch_item_type_filter(api_base, item_type_label) if tab == "item" and item_type_label else {}
    route_params: dict[str, str] = {"tab": tab}
    if item_filter.get("type_key"):
        route_params["type_key"] = item_filter["type_key"]
    if item_filter.get("sub_type_key"):
        route_params["sub_type_key"] = item_filter["sub_type_key"]
    params = urllib.parse.urlencode(route_params)
    url = f"{frontend_base.rstrip('/')}/fanxiu/wiki?{params}"
    if auth_token:
        source = f"""
(() => {{
  window.localStorage.setItem('token', {json.dumps(auth_token)});
  if ({json.dumps(refresh_token)}) window.localStorage.setItem('refresh_token', {json.dumps(refresh_token)});
}})()
"""
        await _evaluate_json(ws, seq_ref, source)

    seq = seq_ref[0]
    seq_ref[0] += 1
    await _cdp_call(ws, seq, "Page.navigate", {"url": url})
    await _wait_after_navigation(ws, seq_ref, wait_ms)
    item_filter_result: dict[str, Any] = {}
    item_route_filter: dict[str, Any] = {}
    if tab == "item" and item_type_label:
        item_route_filter = await _evaluate_json(
            ws,
            seq_ref,
            f"""
(() => {{
  const requested = {json.dumps(item_filter, ensure_ascii=False)};
  const activeLabels = Array.from(document.querySelectorAll('.facet-option.active .facet-option-label'))
    .map(el => (el.innerText || '').trim())
    .filter(Boolean);
  const rows = Array.from(document.querySelectorAll('[data-item-id]')).slice(0, 20).map(row => ({{
    id: row.getAttribute('data-item-id') || '',
    title: row.querySelector('.object-row-title')?.innerText || '',
    icon: row.getAttribute('data-item-icon') || '',
  }}));
  const requestRows = (Array.isArray(window.__fanxiuAuditRequests) ? window.__fanxiuAuditRequests : [])
    .filter(value => String(value).includes('/fanxiu/resources/items/cards'))
    .slice(-8)
    .map(value => String(value));
  return {{
    requested,
    href: location.href,
    active_labels: activeLabels,
    rows,
    request_rows: requestRows,
    type_label_active: activeLabels.some(label => label === requested.label || label.startsWith(requested.label)),
  }};
}})()
""",
        ) or {}
    item_route_clear: dict[str, Any] = {}
    if tab == "item" and not item_type_label:
        item_route_clear = await _evaluate_json(
            ws,
            seq_ref,
            """
(() => {
  const activeLabels = Array.from(document.querySelectorAll('.facet-option.active .facet-option-label'))
    .map(el => (el.innerText || '').trim())
    .filter(Boolean);
  const requestRows = (Array.isArray(window.__fanxiuAuditRequests) ? window.__fanxiuAuditRequests : [])
    .filter(value => String(value).includes('/fanxiu/resources/items/cards'))
    .slice(-8)
    .map(value => String(value));
  return {
    href: location.href,
    active_labels: activeLabels,
    request_rows: requestRows,
    row_count: document.querySelectorAll('[data-item-id]').length,
  };
})()
""",
        ) or {}
    observed_rows: list[dict[str, Any]] = []
    broken_rows: list[dict[str, Any]] = []
    item_row_icon_rows: list[dict[str, Any]] = []
    expected_item_icons_by_title = _fetch_expected_item_icons(api_base, item_type_label) if tab == "item" and item_type_label else {}
    mail_rows = (
        await _collect_mail_rows_across_pages(ws, seq_ref, tab=tab, wait_ms=wait_ms, page_limit=mail_page_limit)
        if tab == "mail"
        else []
    )
    for step in range(max(1, scroll_steps)):
        item_image_wait: dict[str, Any] = {}
        if tab == "item":
            item_image_wait = await _wait_item_row_images(ws, seq_ref, wait_ms)
            item_row_icon_rows.extend(
                _annotate_expected_item_icons(
                    [
                        {
                            "image_wait_ok": bool(item_image_wait.get("ok")),
                            "image_wait_pending_count": int(item_image_wait.get("pending_count") or 0),
                            "image_wait_rows_missing_id_count": int(item_image_wait.get("rows_missing_id_count") or 0),
                            **row,
                        }
                        for row in await _collect_item_row_icon_rows(ws, seq_ref, tab, step)
                    ],
                    expected_item_icons_by_title,
                )
            )
        scan = await _evaluate_json(
            ws,
            seq_ref,
            """
(() => {
  const list = document.querySelector('.object-list-scroll') || document.scrollingElement || document.documentElement;
  const rows = Array.from(document.querySelectorAll('.object-row'));
  const imgs = Array.from(document.images).map(img => {
    const rect = img.getBoundingClientRect();
    return {
      src: img.currentSrc || img.src || '',
      alt: img.alt || '',
      complete: img.complete,
      naturalWidth: img.naturalWidth || 0,
      naturalHeight: img.naturalHeight || 0,
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      w: Math.round(rect.width),
      h: Math.round(rect.height),
      visible: rect.width > 0 && rect.height > 0 && rect.y >= 0 && rect.y <= innerHeight && rect.x >= 0 && rect.x <= innerWidth
    };
  });
  const visible = imgs.filter(img => img.visible);
  const brokenVisible = visible.filter(img => !img.complete || !img.naturalWidth || !img.naturalHeight);
  const title = document.title;
  const bodyText = document.body ? document.body.innerText.slice(0, 2000) : '';
  const loginForm = Boolean(document.querySelector('input[type="password"]')) && Boolean(Array.from(document.querySelectorAll('button')).find(button => (button.innerText || '').trim() === '登录'));
  const scrollTop = list ? list.scrollTop || window.scrollY || 0 : 0;
  const scrollHeight = list ? list.scrollHeight || document.documentElement.scrollHeight || 0 : 0;
  const clientHeight = list ? list.clientHeight || innerHeight || 0 : 0;
  if (list) list.scrollTop = Math.min(scrollHeight, scrollTop + Math.max(clientHeight * 0.8, 240));
  return {
    title, bodyText, loginForm, rowCount: rows.length, imageCount: imgs.length, visibleImageCount: visible.length,
    brokenVisible, scrollTop, scrollHeight, clientHeight
  };
})()
""",
        ) or {}
        observed_rows.append(
            {
                "tab": tab,
                "step": step,
                "title": scan.get("title", ""),
                "row_count": scan.get("rowCount", 0),
                "image_count": scan.get("imageCount", 0),
                "visible_image_count": scan.get("visibleImageCount", 0),
                "broken_visible_count": len(scan.get("brokenVisible") or []),
                "scroll_top": scan.get("scrollTop", 0),
                "scroll_height": scan.get("scrollHeight", 0),
                "client_height": scan.get("clientHeight", 0),
                "state": "login" if scan.get("loginForm") else "",
            }
        )
        for img in scan.get("brokenVisible") or []:
            broken_rows.append({"tab": tab, "step": step, **img})
        await asyncio.sleep(0.8)

    hard_failures: list[dict[str, Any]] = []
    checked: dict[str, dict[str, Any]] = {}
    for row in broken_rows:
        src = str(row.get("src") or "")
        if not src:
            hard_failures.append({**row, "ok": False, "status_code": "", "content_type": "", "size": 0, "detail": "empty src"})
            continue
        if src not in checked:
            checked[src] = _check_image_url(src)
        if not checked[src]["ok"]:
            hard_failures.append({**row, **checked[src]})

    api_requests = await _evaluate_json(
        ws,
        seq_ref,
        """
(() => {
  const captured = Array.isArray(window.__fanxiuAuditRequests) ? window.__fanxiuAuditRequests : [];
  const performanceRows = performance.getEntriesByType('resource').map(entry => entry.name || '');
  return [...captured, ...performanceRows]
  .filter(name => name.includes('/api/fanxiu/resources/'))
  .map(name => {
    try {
      const url = new URL(name, location.href);
      return { path: url.pathname, query: url.search.slice(1), url: url.pathname + url.search };
    } catch {
      return { path: name, query: '', url: name };
    }
  })
  .slice(-80);
})()
""",
    ) or []

    request_failures: list[dict[str, Any]] = []
    if tab in CORE_WIKI_TABS:
        max_row_count = max((int(row.get("row_count") or 0) for row in observed_rows), default=0)
        max_visible_image_count = max((int(row.get("visible_image_count") or 0) for row in observed_rows), default=0)
        if max_row_count <= 0:
            request_failures.append(
                {
                    "tab": tab,
                    "kind": "core_tab_rows_not_rendered",
                    "detail": "core wiki tabs must render resource list rows, not only load the shell",
                    "observed": json.dumps(observed_rows, ensure_ascii=False)[:1600],
                }
            )
        if tab in CORE_WIKI_TABS_REQUIRING_VISIBLE_IMAGES and max_visible_image_count <= 0:
            request_failures.append(
                {
                    "tab": tab,
                    "kind": "core_tab_visible_images_not_rendered",
                    "detail": "core wiki tabs with icon-backed resources must render at least one visible image",
                    "observed": json.dumps(observed_rows, ensure_ascii=False)[:1600],
                }
            )
    if tab == "item":
        item_requests = [row for row in api_requests if row.get("path", "").endswith("/fanxiu/resources/items/cards")]
        if not item_type_label and not item_requests:
            request_failures.append(
                {
                    "tab": tab,
                    "kind": "missing_item_cards_request",
                    "detail": "initial item page request must fetch item cards",
                    "observed": " | ".join(str(row.get("url") or "") for row in item_requests[-5:]),
                }
            )
        if not item_type_label:
            route_href = urllib.parse.unquote(str(item_route_clear.get("href") or ""))
            route_labels = [str(label) for label in item_route_clear.get("active_labels") or []]
            route_request_rows = [urllib.parse.unquote(str(row)) for row in item_route_clear.get("request_rows") or []]
            clear_failures: list[str] = []
            if "type_key=" in route_href or "sub_type_key=" in route_href:
                clear_failures.append("href_kept_filter")
            if route_labels:
                clear_failures.append("active_label_kept_filter")
            def has_nonempty_item_filter(raw: str) -> bool:
                query_text = urllib.parse.urlparse(raw).query
                if not query_text and "?" in raw:
                    query_text = raw.split("?", 1)[1]
                parsed = urllib.parse.parse_qs(query_text, keep_blank_values=True)
                return any(str(value or "").strip() for value in parsed.get("type_key", []) + parsed.get("sub_type_key", []))
            if any(has_nonempty_item_filter(row) for row in route_request_rows):
                clear_failures.append("request_kept_filter")
            for failure in clear_failures:
                request_failures.append(
                    {
                        "tab": tab,
                        "kind": f"item_route_clear_{failure}",
                        "detail": "item tab URL without filter parameters must clear stale persisted item filters",
                        "observed": json.dumps(item_route_clear, ensure_ascii=False)[:1600],
                    }
                )
        if item_type_label:
            filtered_item_requests = [
                row for row in item_requests
                if (urllib.parse.parse_qs(str(row.get("query") or "")).get("type_key") or [""])[0]
            ]
            route_filter_failures: list[str] = []
            route_requested = item_route_filter.get("requested") or {}
            route_type_key = str(route_requested.get("type_key") or "")
            route_sub_type_key = str(route_requested.get("sub_type_key") or "")
            route_href = urllib.parse.unquote(str(item_route_filter.get("href") or ""))
            route_labels = [str(label) for label in item_route_filter.get("active_labels") or []]
            route_rows = item_route_filter.get("rows") or []
            route_request_rows = [urllib.parse.unquote(str(row)) for row in item_route_filter.get("request_rows") or []]
            if route_type_key and f"type_key={route_type_key}" not in route_href:
                route_filter_failures.append("href_missing_type_key")
            if route_sub_type_key and f"sub_type_key={route_sub_type_key}" not in route_href:
                route_filter_failures.append("href_missing_sub_type_key")
            if not any(label == item_type_label or label.startswith(item_type_label) for label in route_labels):
                route_filter_failures.append("active_label_missing")
            expected_ids = {str(item.get("id") or "") for item in expected_item_icons_by_title}
            observed_ids = {str(row.get("id") or "") for row in route_rows if str(row.get("id") or "")}
            if expected_ids and observed_ids and not observed_ids.issubset(expected_ids):
                route_filter_failures.append("rows_not_in_requested_type")
            item_request_texts = route_request_rows + [urllib.parse.unquote(str(row.get("url") or "")) for row in item_requests]
            if route_type_key and not any(f"type_key={route_type_key}" in row for row in item_request_texts):
                route_filter_failures.append("request_missing_type_key")
            for failure in route_filter_failures:
                request_failures.append(
                    {
                        "tab": tab,
                        "kind": f"item_route_filter_{failure}",
                        "detail": f"item type filter {item_type_label!r} must be reproducible from the initial URL",
                        "observed": json.dumps(item_route_filter, ensure_ascii=False)[:1600],
                    }
                )
            if not filtered_item_requests or not item_row_icon_rows:
                request_failures.append(
                    {
                        "tab": tab,
                        "kind": "item_type_filter_not_applied",
                        "detail": f"item type filter {item_type_label!r} must update item rows and request list data",
                        "observed": json.dumps(
                            {
                                "filter": item_filter_result,
                                "route_filter": item_route_filter,
                                "requests": item_requests[-8:],
                            },
                            ensure_ascii=False,
                        )[:1600],
                    }
                )
            bad_icon_rows = [
                row for row in item_row_icon_rows
                if row.get("row_visible") and (
                    not row.get("src")
                    or not row.get("naturalWidth")
                    or not row.get("naturalHeight")
                    or row.get("display") == "none"
                )
            ]
            for row in bad_icon_rows:
                request_failures.append(
                    {
                        "tab": tab,
                        "kind": "item_row_icon_not_rendered",
                        "detail": "item rows after type filter must render a real image instead of fallback text",
                        "observed": json.dumps(row, ensure_ascii=False),
                    }
                )
            item_wait_timeout_rows = [
                row for row in item_row_icon_rows
                if row.get("row_visible") and not row.get("image_wait_ok")
            ]
            for row in item_wait_timeout_rows:
                request_failures.append(
                    {
                        "tab": tab,
                        "kind": "item_row_icon_wait_timeout",
                        "detail": "visible item row images must settle before the audit samples DOM state",
                        "observed": json.dumps(row, ensure_ascii=False),
                    }
                )
            missing_id_rows = [
                row for row in item_row_icon_rows
                if row.get("row_visible") and not str(row.get("item_id") or "").strip()
            ]
            for row in missing_id_rows:
                request_failures.append(
                    {
                        "tab": tab,
                        "kind": "item_row_missing_stable_id",
                        "detail": "visible item rows must expose data-item-id so icon checks are matched by identity, not title",
                        "observed": json.dumps(row, ensure_ascii=False),
                    }
                )
            fallback_leak_rows = [
                row for row in item_row_icon_rows
                if row.get("row_visible")
                and row.get("src")
                and row.get("naturalWidth")
                and row.get("naturalHeight")
                and row.get("display") != "none"
                and row.get("fallback_visible")
            ]
            for row in fallback_leak_rows:
                request_failures.append(
                    {
                        "tab": tab,
                        "kind": "item_row_fallback_visible_after_icon_loaded",
                        "detail": "loaded item icons must hide the single-character fallback layer",
                        "observed": json.dumps(row, ensure_ascii=False),
                    }
                )
            mismatched_icon_rows = [
                row for row in item_row_icon_rows
                if row.get("row_visible") and row.get("expected_icon") and not row.get("expected_icon_match")
            ]
            for row in mismatched_icon_rows:
                request_failures.append(
                    {
                        "tab": tab,
                        "kind": "item_row_icon_src_mismatch",
                        "detail": "item row image URL must use the icon returned by the item cards API",
                        "observed": json.dumps(row, ensure_ascii=False),
                    }
                )
    if tab == "activity":
        activity_requests = [row for row in api_requests if row.get("path", "").endswith("/fanxiu/resources/activities/cards")]
        if not any("include_facets=false" in str(row.get("query") or "") for row in activity_requests):
            request_failures.append(
                {
                    "tab": tab,
                    "kind": "missing_lightweight_activity_cards",
                    "detail": "initial activity page request must include include_facets=false",
                    "observed": " | ".join(str(row.get("url") or "") for row in activity_requests[-5:]),
                }
            )
    if tab == "mail":
        mail_row = mail_rows[0] if mail_rows else {}
        row_count = sum(int(row.get("row_count") or 0) for row in mail_rows)
        reward_image_count = sum(int(row.get("reward_image_count") or 0) for row in mail_rows)
        broken_visible_count = sum(int(row.get("broken_visible_reward_image_count") or 0) for row in mail_rows)
        missing_icon_slot_count = sum(int(row.get("missing_icon_slot_count") or 0) for row in mail_rows)
        empty_alt_image_count = sum(int(row.get("empty_alt_image_count") or 0) for row in mail_rows)
        invalid_item_link_count = sum(int(row.get("invalid_item_link_count") or 0) for row in mail_rows)
        item_link_count = sum(int(row.get("item_link_count") or 0) for row in mail_rows)
        content_button_count = sum(int(row.get("content_button_count") or 0) for row in mail_rows)
        page_advance_errors = [row for row in mail_rows if row.get("page_advance_error")]
        if row_count <= 0:
            request_failures.append(
                {
                    "tab": tab,
                    "kind": "mail_rows_not_rendered",
                    "detail": "mail tab must render packet mail rows after authentication",
                    "observed": json.dumps(mail_row, ensure_ascii=False),
                }
            )
        if reward_image_count <= 0:
            request_failures.append(
                {
                    "tab": tab,
                    "kind": "mail_rewards_not_rendered",
                    "detail": "mail tab must render reward attachment icons",
                    "observed": json.dumps(mail_row, ensure_ascii=False)[:1600],
                }
            )
        if broken_visible_count:
            request_failures.append(
                {
                    "tab": tab,
                    "kind": "mail_visible_reward_icon_broken",
                    "detail": "visible mail attachment icons must finish loading with natural size",
                    "observed": json.dumps(mail_row, ensure_ascii=False)[:1600],
                }
            )
        if missing_icon_slot_count:
            request_failures.append(
                {
                    "tab": tab,
                    "kind": "mail_reward_icon_slots_missing_image",
                    "detail": "mail reward slots must render real images, not fallback-only placeholders",
                    "observed": json.dumps(mail_row, ensure_ascii=False)[:1600],
                }
            )
        if empty_alt_image_count:
            request_failures.append(
                {
                    "tab": tab,
                    "kind": "mail_reward_image_alt_missing",
                    "detail": "mail reward images must carry resolved item names for accessibility and diagnostics",
                    "observed": json.dumps(mail_row, ensure_ascii=False)[:1600],
                }
            )
        if invalid_item_link_count:
            request_failures.append(
                {
                    "tab": tab,
                    "kind": "mail_reward_invalid_item_link",
                    "detail": "mail reward links must target item resource detail pages",
                    "observed": json.dumps(mail_row, ensure_ascii=False)[:1600],
                }
            )
        if item_link_count <= 0:
            request_failures.append(
                {
                    "tab": tab,
                    "kind": "mail_reward_links_missing",
                    "detail": "mail reward attachments should link to item resource detail pages",
                    "observed": json.dumps(mail_row, ensure_ascii=False)[:1600],
                }
            )
        if content_button_count <= 0:
            request_failures.append(
                {
                    "tab": tab,
                    "kind": "mail_content_buttons_missing",
                    "detail": "mail rows with parsed content should expose a body viewer button",
                    "observed": json.dumps(mail_row, ensure_ascii=False)[:1600],
                }
            )
        if page_advance_errors:
            request_failures.append(
                {
                    "tab": tab,
                    "kind": "mail_pagination_advance_failed",
                    "detail": "mail browser audit must be able to inspect all configured pages",
                    "observed": json.dumps(page_advance_errors[:3], ensure_ascii=False)[:1600],
                }
            )

    screenshot_path = ""
    if screenshot:
        seq = seq_ref[0]
        seq_ref[0] += 1
        shot = await _cdp_call(ws, seq, "Page.captureScreenshot", {"format": "png", "fromSurface": True})
        screenshot_suffix = f"{tab}_{item_type_label}" if item_type_label else tab
        screenshot_path = str(output_dir / f"browser_{_safe_name(screenshot_suffix)}_latest.png")
        Path(screenshot_path).write_bytes(base64.b64decode(shot["data"]))

    mail_interactions = await _probe_mail_interactions(ws, seq_ref, tab)
    if tab == "mail":
        if not mail_interactions.get("content_clicked") or not str(mail_interactions.get("content_text") or "").strip():
            request_failures.append(
                {
                    "tab": tab,
                    "kind": "mail_content_dialog_not_opened",
                    "detail": "mail content viewer must open and expose parsed body text",
                    "observed": json.dumps(mail_interactions, ensure_ascii=False),
                }
            )
        if not mail_interactions.get("item_link_clicked") or not str(mail_interactions.get("item_route_path") or "").startswith("/fanxiu-resource/item/") or not mail_interactions.get("item_route_has_name"):
            request_failures.append(
                {
                    "tab": tab,
                    "kind": "mail_reward_item_link_not_navigable",
                    "detail": "mail reward item links must navigate to an item detail route",
                    "observed": json.dumps(mail_interactions, ensure_ascii=False),
                }
            )

    return {
        "tab": tab,
        "url": url,
        "observations": observed_rows,
        "broken": broken_rows,
        "hard_failures": hard_failures,
        "item_filter": item_filter_result,
        "item_route_filter": item_route_filter,
        "item_route_clear": item_route_clear,
        "item_row_icons": item_row_icon_rows,
        "mail_rows": mail_rows,
        "mail_interactions": mail_interactions,
        "api_requests": [{"tab": tab, **row} for row in api_requests],
        "request_failures": request_failures,
        "screenshot": screenshot_path,
    }


async def run_browser_audit(args: argparse.Namespace) -> dict[str, Any]:
    export_root = resolve_fanxiu_export_root(args.export_root or None)
    output_dir = export_root / "parsed_configs" / "wiki_browser_audit"
    output_dir.mkdir(parents=True, exist_ok=True)
    auth_token = args.auth_token or os.environ.get(AUTH_TOKEN_ENV, "")
    refresh_token = args.refresh_token or os.environ.get(REFRESH_TOKEN_ENV, "")
    if not auth_token and args.local_auth_user:
        auth_token = _create_local_access_token(args.local_auth_user)
    process, ws_url = await _open_chrome(args.chrome, args.frontend_base, args.width, args.height)
    try:
        async with websockets.connect(ws_url, max_size=50_000_000) as ws:
            seq_ref = [1]
            await _cdp_call(ws, seq_ref[0], "Page.enable")
            seq_ref[0] += 1
            await _cdp_call(ws, seq_ref[0], "Runtime.enable")
            seq_ref[0] += 1
            await _cdp_call(
                ws,
                seq_ref[0],
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": """
(() => {
  const record = (value) => {
    try {
      window.__fanxiuAuditRequests = window.__fanxiuAuditRequests || [];
      const text = typeof value === 'string' ? value : (value && value.url) || String(value || '');
      window.__fanxiuAuditRequests.push(text);
    } catch {}
  };
  const originalFetch = window.fetch;
  if (typeof originalFetch === 'function') {
    window.fetch = function(input, init) {
      record(input);
      return originalFetch.apply(this, arguments);
    };
  }
  const originalOpen = XMLHttpRequest && XMLHttpRequest.prototype && XMLHttpRequest.prototype.open;
  if (typeof originalOpen === 'function') {
    XMLHttpRequest.prototype.open = function(method, url) {
      record(url);
      return originalOpen.apply(this, arguments);
    };
  }
})()
""",
                },
            )
            seq_ref[0] += 1
            await _cdp_call(
                ws,
                seq_ref[0],
                "Emulation.setDeviceMetricsOverride",
                {"width": args.width, "height": args.height, "deviceScaleFactor": 1, "mobile": False},
            )
            seq_ref[0] += 1
            results = []
            for tab in args.tab:
                item_type_labels = [*args.item_type_label, ""] if tab == "item" and args.item_type_label else [""]
                for item_type_label in item_type_labels:
                    label_suffix = f" type={item_type_label}" if item_type_label else ""
                    print(f"browser tab {tab}{label_suffix}", flush=True)
                    results.append(
                        await _scan_tab(
                            ws,
                            seq_ref,
                            frontend_base=args.frontend_base,
                            api_base=args.api_base,
                            tab=tab,
                            auth_token=auth_token,
                            refresh_token=refresh_token,
                            output_dir=output_dir,
                            wait_ms=args.wait_ms,
                            scroll_steps=args.scroll_steps,
                            mail_page_limit=args.mail_page_limit,
                            screenshot=args.screenshot,
                            item_type_label=item_type_label,
                        )
                    )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    observation_rows = [row for result in results for row in result["observations"]]
    broken_rows = [row for result in results for row in result["broken"]]
    hard_failure_rows = [row for result in results for row in result["hard_failures"]]
    request_rows = [row for result in results for row in result["api_requests"]]
    request_failure_rows = [row for result in results for row in result["request_failures"]]
    item_icon_rows = [row for result in results for row in result.get("item_row_icons", [])]
    item_route_filter_rows = [
        {
            "tab": result["tab"],
            "requested": json.dumps((result.get("item_route_filter") or {}).get("requested") or {}, ensure_ascii=False),
            "href": (result.get("item_route_filter") or {}).get("href", ""),
            "active_labels": " | ".join((result.get("item_route_filter") or {}).get("active_labels") or []),
            "row_count": len((result.get("item_route_filter") or {}).get("rows") or []),
            "request_rows": " | ".join((result.get("item_route_filter") or {}).get("request_rows") or []),
        }
        for result in results
        if result.get("item_route_filter")
    ]
    item_route_clear_rows = [
        {
            "tab": result["tab"],
            "href": (result.get("item_route_clear") or {}).get("href", ""),
            "active_labels": " | ".join((result.get("item_route_clear") or {}).get("active_labels") or []),
            "row_count": (result.get("item_route_clear") or {}).get("row_count", 0),
            "request_rows": " | ".join((result.get("item_route_clear") or {}).get("request_rows") or []),
        }
        for result in results
        if result.get("item_route_clear")
    ]
    mail_rows = [row for result in results for row in result.get("mail_rows", [])]
    mail_interaction_rows = [
        {"tab": result["tab"], **result.get("mail_interactions", {})}
        for result in results
        if result.get("mail_interactions")
    ]
    _write_tsv(
        output_dir / "browser_observations_latest.tsv",
        observation_rows,
        [
            "tab",
            "step",
            "title",
            "row_count",
            "image_count",
            "visible_image_count",
            "broken_visible_count",
            "scroll_top",
            "scroll_height",
            "client_height",
            "state",
        ],
    )
    _write_tsv(
        output_dir / "browser_broken_visible_images_latest.tsv",
        broken_rows,
        ["tab", "step", "src", "alt", "complete", "naturalWidth", "naturalHeight", "x", "y", "w", "h", "visible"],
    )
    _write_tsv(
        output_dir / "browser_hard_image_failures_latest.tsv",
        hard_failure_rows,
        [
            "tab",
            "step",
            "src",
            "alt",
            "ok",
            "status_code",
            "content_type",
            "size",
            "detail",
        ],
    )
    _write_tsv(
        output_dir / "browser_api_requests_latest.tsv",
        request_rows,
        ["tab", "path", "query", "url"],
    )
    _write_tsv(
        output_dir / "browser_request_failures_latest.tsv",
        request_failure_rows,
        ["tab", "kind", "detail", "observed"],
    )
    if item_icon_rows:
        _write_tsv(
            output_dir / "browser_item_row_icons_latest.tsv",
            item_icon_rows,
            [
                "tab",
                "sample_step",
                "index",
                "image_wait_ok",
                "image_wait_pending_count",
                "image_wait_rows_missing_id_count",
                "item_id",
                "item_icon",
                "title",
                "fallback",
                "fallback_visible",
                "row_visible",
                "expected_item_id",
                "expected_icon",
                "expected_icon_match",
                "src",
                "complete",
                "naturalWidth",
                "naturalHeight",
                "display",
                "visible",
            ],
        )
    if item_route_filter_rows:
        _write_tsv(
            output_dir / "browser_item_route_filters_latest.tsv",
            item_route_filter_rows,
            ["tab", "requested", "href", "active_labels", "row_count", "request_rows"],
        )
    if item_route_clear_rows:
        _write_tsv(
            output_dir / "browser_item_route_clear_latest.tsv",
            item_route_clear_rows,
            ["tab", "href", "active_labels", "row_count", "request_rows"],
        )
    if mail_rows:
        _write_tsv(
            output_dir / "browser_mail_latest.tsv",
            mail_rows,
            [
                "tab",
                "page_index",
                "page_status",
                "image_wait_ok",
                "image_wait_pending_count",
                "row_count",
                "reward_slot_count",
                "reward_image_count",
                "visible_reward_image_count",
                "broken_visible_reward_image_count",
                "missing_icon_slot_count",
                "empty_alt_image_count",
                "invalid_item_link_count",
                "reward_link_count",
                "item_link_count",
                "content_button_count",
                "page_size_text",
                "page_advance_error",
                "samples_json",
            ],
        )
    if mail_interaction_rows:
        _write_tsv(
            output_dir / "browser_mail_interactions_latest.tsv",
            mail_interaction_rows,
            [
                "tab",
                "content_clicked",
                "content_title",
                "content_text",
                "first_item_href",
                "origin",
                "item_link_clicked",
                "item_route_path",
                "item_route_has_name",
            ],
        )
    summary = {
        "tabs": [result["tab"] for result in results],
        "has_auth_token": bool(auth_token),
        "observation_count": len(observation_rows),
        "broken_visible_image_count": len(broken_rows),
        "hard_image_failure_count": len(hard_failure_rows),
        "request_failure_count": len(request_failure_rows),
        "core_tab_count": len({row.get("tab") for row in observation_rows if row.get("tab") in CORE_WIKI_TABS}),
        "core_tab_with_rows_count": len(
            {
                row.get("tab")
                for row in observation_rows
                if row.get("tab") in CORE_WIKI_TABS and int(row.get("row_count") or 0) > 0
            }
        ),
        "core_tab_image_required_count": len(
            {
                row.get("tab")
                for row in observation_rows
                if row.get("tab") in CORE_WIKI_TABS_REQUIRING_VISIBLE_IMAGES
            }
        ),
        "core_tab_with_visible_images_count": len(
            {
                row.get("tab")
                for row in observation_rows
                if row.get("tab") in CORE_WIKI_TABS_REQUIRING_VISIBLE_IMAGES and int(row.get("visible_image_count") or 0) > 0
            }
        ),
        "item_row_icon_count": len(item_icon_rows),
        "item_route_filter_count": len(item_route_filter_rows),
        "item_route_filter_failure_count": sum(
            1 for row in request_failure_rows
            if str(row.get("kind") or "").startswith("item_route_filter_")
        ),
        "item_route_clear_count": len(item_route_clear_rows),
        "item_route_clear_failure_count": sum(
            1 for row in request_failure_rows
            if str(row.get("kind") or "").startswith("item_route_clear_")
        ),
        "item_row_icon_missing_count": sum(
            1 for row in item_icon_rows
            if row.get("row_visible") and (
                not row.get("src")
                or not row.get("naturalWidth")
                or not row.get("naturalHeight")
                or row.get("display") == "none"
            )
        ),
        "item_row_icon_mismatch_count": sum(
            1 for row in item_icon_rows
            if row.get("row_visible") and row.get("expected_icon") and not row.get("expected_icon_match")
        ),
        "item_row_icon_wait_timeout_count": sum(
            1 for row in item_icon_rows
            if row.get("row_visible") and not row.get("image_wait_ok")
        ),
        "item_row_missing_id_count": sum(
            1 for row in item_icon_rows
            if row.get("row_visible") and not str(row.get("item_id") or "").strip()
        ),
        "item_row_fallback_visible_count": sum(
            1 for row in item_icon_rows
            if row.get("row_visible")
            and row.get("src")
            and row.get("naturalWidth")
            and row.get("naturalHeight")
            and row.get("display") != "none"
            and row.get("fallback_visible")
        ),
        "mail_row_count": sum(int(row.get("row_count") or 0) for row in mail_rows),
        "mail_reward_slot_count": sum(int(row.get("reward_slot_count") or 0) for row in mail_rows),
        "mail_reward_image_count": sum(int(row.get("reward_image_count") or 0) for row in mail_rows),
        "mail_broken_visible_reward_image_count": sum(int(row.get("broken_visible_reward_image_count") or 0) for row in mail_rows),
        "mail_missing_icon_slot_count": sum(int(row.get("missing_icon_slot_count") or 0) for row in mail_rows),
        "mail_empty_alt_image_count": sum(int(row.get("empty_alt_image_count") or 0) for row in mail_rows),
        "mail_invalid_item_link_count": sum(int(row.get("invalid_item_link_count") or 0) for row in mail_rows),
        "mail_item_link_count": sum(int(row.get("item_link_count") or 0) for row in mail_rows),
        "mail_content_button_count": sum(int(row.get("content_button_count") or 0) for row in mail_rows),
        "mail_content_dialog_ok": all(row.get("content_clicked") and str(row.get("content_text") or "").strip() for row in mail_interaction_rows) if mail_interaction_rows else False,
        "mail_item_link_navigation_ok": all(row.get("item_link_clicked") and str(row.get("item_route_path") or "").startswith("/fanxiu-resource/item/") and row.get("item_route_has_name") for row in mail_interaction_rows) if mail_interaction_rows else False,
        "login_state_observed": any(row.get("state") == "login" for row in observation_rows),
        "screenshots": [result["screenshot"] for result in results if result["screenshot"]],
        "output_dir": str(output_dir),
    }
    (output_dir / "summary_latest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the Fanxiu wiki page in a real browser with optional CodeYun auth token injection.")
    parser.add_argument("--frontend-base", default=DEFAULT_FRONTEND_BASE)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--export-root", default="")
    parser.add_argument("--chrome", default=DEFAULT_CHROME)
    parser.add_argument("--auth-token", default="", help=f"CodeYun access token; defaults to ${AUTH_TOKEN_ENV}.")
    parser.add_argument("--refresh-token", default="", help=f"Optional refresh token; defaults to ${REFRESH_TOKEN_ENV}.")
    parser.add_argument("--local-auth-user", default="", help="Generate a short-lived local JWT for this username, useful for authenticated local browser checks.")
    parser.add_argument("--tab", action="append", default=[], help="Wiki tab to verify; repeatable.")
    parser.add_argument("--wait-ms", type=int, default=8000)
    parser.add_argument("--scroll-steps", type=int, default=4)
    parser.add_argument("--mail-page-limit", type=int, default=20)
    parser.add_argument("--width", type=int, default=1365)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--screenshot", action="store_true")
    parser.add_argument(
        "--item-type-label",
        action="append",
        default=[],
        help="For item tab, click this type facet and verify rendered row icons; repeatable.",
    )
    args = parser.parse_args()
    if not args.tab:
        args.tab = ["item", "activity", "visual", "asset", "audio", "protocol"]
    summary = asyncio.run(run_browser_audit(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["hard_image_failure_count"] or summary["request_failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
