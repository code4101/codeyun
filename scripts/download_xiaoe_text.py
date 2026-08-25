from __future__ import annotations

import argparse
import json
import msvcrt
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.xiaoe_text_archive import archive_text_article
from scripts.download_xiaoe_video_daemon import (
    ITEMS_PER_PAGE,
    XiaoeLoginRequired,
    _body_text,
    _catalog_total,
    _cleanup_xiaoe_incremental_tabs,
    _load_xlproject_env,
    _login_with_env,
    _open_browser,
    _wait_until,
)

TEXT_LIST_URL = "https://admin.xiaoe-tech.com/t/course/text/list"
TEXT_DETAIL_URL = "https://admin.xiaoe-tech.com/t/course/text/detail/{resource_id}"
HELPER_DIR_NAME = "_下载辅助"
INDEX_FILE_NAME = "text-catalog-index.json"
FULL_STATE_FILE_NAME = "text-current-state.json"
INCREMENTAL_STATE_FILE_NAME = "text-incremental-state.json"


class XiaoeTextError(RuntimeError):
    """小鹅通图文归档流程错误。"""


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _fetch_text_page(tab: Any, page: int) -> dict[str, Any]:
    payload = tab.run_js(
        """
        const appId = Object.keys(localStorage)
          .find((key) => key.endsWith('_shopInfo'))
          ?.replace(/_shopInfo$/, '');
        if (!appId) return {code: -1, msg: 'missing app id'};
        return fetch('/xe.course.b_admin_r.course.base.list/1.0.0', {
          method: 'POST',
          headers: {'Content-Type': 'application/x-www-form-urlencoded'},
          body: new URLSearchParams({
            app_id: appId,
            search_content: '',
            resource_type: '1',
            sale_status: '-1',
            created_source: '1',
            auth_type: '-1',
            page_index: String(arguments[0]),
            page_size: '10',
          }).toString(),
        }).then((response) => response.json());
        """,
        page,
        timeout=20,
    )
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise XiaoeTextError(f"第 {page} 页图文列表接口失败")
    data = payload.get("data") or {}
    rows = [
        {
            "resource_id": str(item.get("resource_id") or ""),
            "title": str(item.get("title") or ""),
            "published_at": str(item.get("sale_at") or ""),
            "h5_url": str(item.get("h5_url") or ""),
        }
        for item in data.get("list") or []
    ]
    return {"total": int(data.get("total") or 0), "rows": rows}


def _fetch_text_detail(tab: Any, resource_id: str) -> dict[str, Any]:
    payload = tab.run_js(
        """
        return fetch(
          '/xe.course.b_admin_r.image_text.detail.get/1.0.0?resource_id='
            + encodeURIComponent(arguments[0])
        ).then((response) => response.json());
        """,
        resource_id,
        timeout=30,
    )
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise XiaoeTextError(f"图文详情接口失败：{resource_id}")
    return dict(payload.get("data") or {})


def _next_cursor(page: int, item_index: int) -> dict[str, int]:
    if item_index + 1 < ITEMS_PER_PAGE:
        return {"page": page, "item_index": item_index + 1}
    return {"page": page + 1, "item_index": 0}


def _new_full_state(path: Path) -> dict[str, Any]:
    previous = _read_json(path)
    if previous.get("status") == "completed":
        return previous
    cursor = previous.get("cursor") or {"page": 1, "item_index": 0}
    return {
        "status": "starting",
        "pid": os.getpid(),
        "started_at": previous.get("started_at") or _now(),
        "updated_at": _now(),
        "cursor": {
            "page": int(cursor.get("page") or 1),
            "item_index": int(cursor.get("item_index") or 0),
        },
        "archived_count": int(previous.get("archived_count") or 0),
        "special_count": int(previous.get("special_count") or 0),
        "last_processed": previous.get("last_processed"),
        "last_error": None,
    }


def _new_incremental_state() -> dict[str, Any]:
    return {
        "status": "starting",
        "pid": os.getpid(),
        "started_at": _now(),
        "updated_at": _now(),
        "cursor": {"page": 1, "item_index": 0},
        "archived_count": 0,
        "special_count": 0,
        "last_error": None,
    }


def _record_index(
    path: Path,
    index: dict[str, Any],
    *,
    item: dict[str, str],
    outcome: str,
    result: dict[str, Any] | None = None,
) -> None:
    index.setdefault("items", {})[item["resource_id"]] = {
        **item,
        "outcome": outcome,
        "result": result,
        "processed_at": _now(),
    }
    index["version"] = 1
    index["updated_at"] = _now()
    _write_json(path, index)


def _mark_processed(
    state_path: Path,
    state: dict[str, Any],
    *,
    page: int,
    item_index: int,
    item: dict[str, str],
    outcome: str,
) -> None:
    state["cursor"] = _next_cursor(page, item_index)
    state["last_processed"] = {
        "page": page,
        "item_index": item_index,
        **item,
        "outcome": outcome,
    }
    state["status"] = "running"
    state["updated_at"] = _now()
    state.pop("current", None)
    _write_json(state_path, state)


def _wait_for_prerequisite(args: argparse.Namespace) -> None:
    if not args.wait_for:
        return
    helper_dir = args.output_dir / HELPER_DIR_NAME
    state_path = helper_dir / (
        INCREMENTAL_STATE_FILE_NAME if args.mode == "incremental" else FULL_STATE_FILE_NAME
    )
    while True:
        prerequisite = _read_json(args.wait_for)
        if prerequisite.get("status") == "completed":
            return
        state = _new_incremental_state() if args.mode == "incremental" else _new_full_state(state_path)
        state.update(
            {
                "status": "waiting",
                "pid": os.getpid(),
                "updated_at": _now(),
                "waiting_for": str(args.wait_for),
            }
        )
        _write_json(state_path, state)
        time.sleep(args.wait_seconds)


def _get_or_open_text_list_tab(browser: Any) -> tuple[Any, bool]:
    for tab in browser.get_tabs():
        if TEXT_LIST_URL in str(tab.url):
            return tab, False
    return browser.new_tab(TEXT_LIST_URL), True


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    _load_xlproject_env()
    helper_dir = args.output_dir / HELPER_DIR_NAME
    helper_dir.mkdir(parents=True, exist_ok=True)
    state_path = helper_dir / (
        INCREMENTAL_STATE_FILE_NAME if args.mode == "incremental" else FULL_STATE_FILE_NAME
    )
    index_path = helper_dir / INDEX_FILE_NAME
    lock_file = (helper_dir / "daemon.lock").open("a+b")
    try:
        if lock_file.seek(0, 2) == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        lock_file.close()
        raise XiaoeTextError("已有小鹅通下载任务在运行") from exc

    state = _new_incremental_state() if args.mode == "incremental" else _new_full_state(state_path)
    if state.get("status") == "completed":
        lock_file.close()
        return state
    state["pid"] = os.getpid()
    state.pop("waiting_for", None)
    _write_json(state_path, state)
    index = _read_json(index_path)
    indexed_items = index.setdefault("items", {})
    list_tab = None
    list_tab_created = False
    try:
        browser = _open_browser()
        list_tab, list_tab_created = _get_or_open_text_list_tab(browser)
        if not _wait_until(
            lambda: _catalog_total(list_tab) is not None and "管理" in _body_text(list_tab),
            timeout=30,
        ):
            text = _body_text(list_tab)
            if "登录" in text or "验证码" in text or "/t/login" in str(list_tab.url):
                _login_with_env(list_tab)
                list_tab.get(TEXT_LIST_URL)
            if not _wait_until(
                lambda: _catalog_total(list_tab) is not None and "管理" in _body_text(list_tab),
                timeout=30,
            ):
                raise XiaoeLoginRequired("小鹅通图文列表需要重新登录")

        page = int(state["cursor"]["page"])
        start_item = int(state["cursor"]["item_index"])
        while True:
            page_data = _fetch_text_page(list_tab, page)
            total = page_data["total"]
            rows = page_data["rows"]
            state["catalog_total"] = total
            for item_index in range(start_item, len(rows)):
                item = rows[item_index]
                if not item["resource_id"] or not item["title"] or not item["published_at"]:
                    raise XiaoeTextError(f"第 {page} 页第 {item_index + 1} 条元数据不完整")
                if item["resource_id"] in indexed_items:
                    if args.mode == "incremental":
                        state["status"] = "completed"
                        state["boundary"] = {"page": page, "item_index": item_index, **item}
                        state["finished_at"] = _now()
                        state["updated_at"] = _now()
                        _write_json(state_path, state)
                        return state
                    _mark_processed(
                        state_path,
                        state,
                        page=page,
                        item_index=item_index,
                        item=item,
                        outcome="indexed",
                    )
                    continue

                state["current"] = {"page": page, "item_index": item_index, **item}
                state["status"] = "archiving"
                state["updated_at"] = _now()
                _write_json(state_path, state)
                detail = _fetch_text_detail(list_tab, item["resource_id"])
                result = archive_text_article(
                    title=item["title"],
                    published_at=item["published_at"],
                    content_html=str(detail.get("org_content") or ""),
                    cover_url=str(detail.get("img_url") or ""),
                    source_url=item["h5_url"]
                    or TEXT_DETAIL_URL.format(resource_id=item["resource_id"]),
                    output_dir=args.output_dir,
                )
                outcome = "archived"
                state["archived_count"] += 1
                _record_index(index_path, index, item=item, outcome=outcome, result=result)
                _mark_processed(
                    state_path,
                    state,
                    page=page,
                    item_index=item_index,
                    item=item,
                    outcome=outcome,
                )
                time.sleep(args.between_items)

            if page * ITEMS_PER_PAGE >= total:
                state["status"] = "completed"
                state["finished_at"] = _now()
                state["updated_at"] = _now()
                _write_json(state_path, state)
                return state
            page += 1
            start_item = 0
    except Exception as exc:
        state["status"] = "failed"
        state["updated_at"] = _now()
        state["last_error"] = f"{type(exc).__name__}: {exc}"
        _write_json(state_path, state)
        raise
    finally:
        if (
            list_tab is not None
            and args.mode == "incremental"
            and state.get("status") == "completed"
        ):
            _cleanup_xiaoe_incremental_tabs(browser)
        elif list_tab is not None and list_tab_created and args.mode != "incremental":
            try:
                if len(browser.get_tabs()) > 1:
                    list_tab.close()
            except Exception:
                pass
        lock_file.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="小鹅通图文离线归档器")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--mode", choices=("full", "incremental"), default="full")
    parser.add_argument("--between-items", type=float, default=1)
    parser.add_argument("--retry-forever", action="store_true")
    parser.add_argument("--retry-seconds", type=float, default=300)
    parser.add_argument("--wait-for", type=Path)
    parser.add_argument("--wait-seconds", type=float, default=60)
    args = parser.parse_args()
    _wait_for_prerequisite(args)
    while True:
        try:
            state = run_once(args)
        except Exception:
            if not args.retry_forever:
                raise
            time.sleep(args.retry_seconds)
            continue
        print(
            json.dumps(
                {
                    "status": state.get("status"),
                    "catalog_total": state.get("catalog_total"),
                    "archived_count": state.get("archived_count"),
                    "special_count": state.get("special_count"),
                    "boundary": state.get("boundary"),
                },
                ensure_ascii=False,
            )
        )
        return


if __name__ == "__main__":
    main()
