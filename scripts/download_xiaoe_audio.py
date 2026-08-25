from __future__ import annotations

import argparse
import json
import msvcrt
import os
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.xiaoe_audio_archive import download_audio_file
from backend.core.xiaoe_video_archive import INVALID_FILENAME_CHARS
from scripts.download_xiaoe_video_daemon import (
    ITEMS_PER_PAGE,
    XiaoeLoginRequired,
    _body_text,
    _catalog_total,
    _load_xlproject_env,
    _login_with_env,
    _normalize_page_title,
    _open_browser,
    _wait_until,
)

AUDIO_LIST_URL = "https://admin.xiaoe-tech.com/t/course/audio/list"
HELPER_DIR_NAME = "_下载辅助"
INDEX_FILE_NAME = "audio-catalog-index.json"
FULL_STATE_FILE_NAME = "audio-current-state.json"
INCREMENTAL_STATE_FILE_NAME = "audio-incremental-state.json"
VIDEO_FILENAME_PATTERN = re.compile(r"^\d{8}_\d{6}_(.+)\.mp4$", re.IGNORECASE)


class XiaoeAudioError(RuntimeError):
    """小鹅通音频下载流程错误。"""


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
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temp_path.replace(path)


def _title_key(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", _normalize_page_title(title))
    filename_safe = INVALID_FILENAME_CHARS.sub("_", normalized)
    compact = re.sub(r"\s+", "", filename_safe).strip().rstrip(".")
    compact = re.sub(r"^20\d{6}[-_—:：]*", "", compact)
    compact = re.sub(r"[-_](?:副本)?\d+$", "", compact)
    return compact


def _video_title_keys(output_dir: Path) -> set[str]:
    keys: set[str] = set()
    video_root = output_dir / "视频"
    if not video_root.exists():
        return keys
    for year_dir in video_root.iterdir():
        if not year_dir.is_dir():
            continue
        for path in year_dir.glob("*.mp4"):
            match = VIDEO_FILENAME_PATTERN.fullmatch(path.name)
            if match:
                keys.add(_title_key(match.group(1)))
    return keys


def _audio_list_tab(browser: Any) -> Any:
    for tab in browser.get_tabs():
        if AUDIO_LIST_URL in str(tab.url):
            return tab
    return browser.new_tab(AUDIO_LIST_URL)


def _ensure_audio_list_ready(tab: Any) -> None:
    if AUDIO_LIST_URL not in str(tab.url):
        tab.get(AUDIO_LIST_URL)
    if _wait_until(
        lambda: _catalog_total(tab) is not None and "管理" in _body_text(tab), timeout=30
    ):
        return
    text = _body_text(tab)
    if "登录" in text or "验证码" in text or "/t/login" in str(tab.url):
        raise XiaoeLoginRequired("DrissionPage 默认浏览器中的小鹅通需要登录")
    raise XiaoeAudioError(f"音频列表没有加载完成，当前页面：{tab.url}")


def _fetch_audio_page(tab: Any, page: int) -> dict[str, Any]:
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
            resource_type: '2',
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
        raise XiaoeAudioError(f"第 {page} 页音频列表接口失败")
    data = payload.get("data") or {}
    rows = [
        {
            "resource_id": str(item.get("resource_id") or ""),
            "title": str(item.get("title") or ""),
            "published_at": str(item.get("sale_at") or ""),
        }
        for item in data.get("list") or []
    ]
    return {"total": int(data.get("total") or 0), "rows": rows}


def _fetch_audio_info(tab: Any, resource_id: str) -> dict[str, Any]:
    payload = tab.run_js(
        """
        return fetch(
          '/xe.course.b_admin_r.audio.info.get/1.0.0?resource_id='
            + encodeURIComponent(arguments[0])
        ).then((response) => response.json());
        """,
        resource_id,
        timeout=20,
    )
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise XiaoeAudioError(f"音频详情接口失败：{resource_id}")
    return dict(payload.get("data") or {})


def _audio_info_is_empty(info: dict[str, Any]) -> bool:
    values: list[float] = []
    for key in ("audio_size", "audio_length"):
        value = info.get(key)
        if value in (None, ""):
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            return False
    return len(values) == 2 and all(value <= 0 for value in values)


def _audio_download_error_reason(exc: Exception) -> str:
    message = str(exc).lower()
    if "403 forbidden" in message or "access denied" in message:
        return "音频源拒绝访问"
    if "404 not found" in message:
        return "音频源不存在"
    if "invalid data" in message or "mpeg audio frames" in message:
        return "音频文件已损坏"
    return "音频下载失败"


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
        "downloaded_count": int(previous.get("downloaded_count") or 0),
        "skipped_video_count": int(previous.get("skipped_video_count") or 0),
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
        "downloaded_count": 0,
        "skipped_video_count": 0,
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
    error: str | None = None,
) -> None:
    index.setdefault("items", {})[item["resource_id"]] = {
        **item,
        "outcome": outcome,
        "result": result,
        "error": error,
        "processed_at": _now(),
    }
    index["version"] = 1
    index["updated_at"] = _now()
    _write_json(path, index)


def _reconcile_downloaded_duplicates(
    output_dir: Path,
    index_path: Path,
    index: dict[str, Any],
    video_titles: set[str],
) -> int:
    """删除旧规则漏判但能由新规则明确匹配到视频的音频。"""
    audio_root = (output_dir / "音频").resolve()
    removed = 0
    for entry in (index.get("items") or {}).values():
        if entry.get("outcome") != "downloaded":
            continue
        if _title_key(str(entry.get("title") or "")) not in video_titles:
            continue
        path_text = str((entry.get("result") or {}).get("path") or "")
        path = Path(path_text).resolve() if path_text else None
        if path and path.is_relative_to(audio_root) and path.exists():
            path.unlink()
        entry["outcome"] = "skipped_video_title"
        entry["result"] = None
        entry["reconciled_at"] = _now()
        removed += 1
    if removed:
        index["updated_at"] = _now()
        _write_json(index_path, index)
    return removed


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
    state["last_processed"] = {"page": page, "item_index": item_index, **item, "outcome": outcome}
    state["status"] = "running"
    state["updated_at"] = _now()
    state.pop("current", None)
    _write_json(state_path, state)


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
        raise XiaoeAudioError("已有小鹅通下载任务在运行") from exc

    state = _new_incremental_state() if args.mode == "incremental" else _new_full_state(state_path)
    if state.get("status") == "completed":
        lock_file.close()
        return state
    state["pid"] = os.getpid()
    _write_json(state_path, state)
    index = _read_json(index_path)
    indexed_items = index.setdefault("items", {})
    video_titles = _video_title_keys(args.output_dir)
    reconciled = _reconcile_downloaded_duplicates(
        args.output_dir, index_path, index, video_titles
    )
    if reconciled:
        state["downloaded_count"] = max(0, state["downloaded_count"] - reconciled)
        state["skipped_video_count"] += reconciled
        _write_json(state_path, state)

    try:
        browser = _open_browser()
        list_tab = _audio_list_tab(browser)
        try:
            _ensure_audio_list_ready(list_tab)
        except XiaoeLoginRequired:
            _login_with_env(list_tab)
            list_tab.get(AUDIO_LIST_URL)
            _ensure_audio_list_ready(list_tab)

        page = int(state["cursor"]["page"])
        start_item = int(state["cursor"]["item_index"])
        while True:
            page_data = _fetch_audio_page(list_tab, page)
            total = page_data["total"]
            rows = page_data["rows"]
            state["catalog_total"] = total
            for item_index in range(start_item, len(rows)):
                item = rows[item_index]
                if not all(item.values()):
                    raise XiaoeAudioError(f"第 {page} 页第 {item_index + 1} 条元数据不完整")
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
                state["status"] = "checking"
                state["updated_at"] = _now()
                _write_json(state_path, state)
                if _title_key(item["title"]) in video_titles:
                    outcome = "skipped_video_title"
                    state["skipped_video_count"] += 1
                    _record_index(index_path, index, item=item, outcome=outcome)
                    _mark_processed(
                        state_path,
                        state,
                        page=page,
                        item_index=item_index,
                        item=item,
                        outcome=outcome,
                    )
                    continue

                info = _fetch_audio_info(list_tab, item["resource_id"])
                audio_url = str(info.get("audio_url") or info.get("audio_compress_url") or "")
                if not audio_url or _audio_info_is_empty(info):
                    outcome = "special"
                    error = (
                        "音频文件为空或已损坏"
                        if audio_url
                        else "音频详情没有可下载地址"
                    )
                    state["special_count"] += 1
                    _record_index(index_path, index, item=item, outcome=outcome, error=error)
                    _mark_processed(
                        state_path,
                        state,
                        page=page,
                        item_index=item_index,
                        item=item,
                        outcome=outcome,
                    )
                    continue

                state["status"] = "downloading"
                state["updated_at"] = _now()
                _write_json(state_path, state)
                try:
                    result = download_audio_file(
                        audio_url=audio_url,
                        title=item["title"],
                        published_at=item["published_at"],
                        output_dir=args.output_dir,
                    )
                except Exception as exc:
                    outcome = "special"
                    error = _audio_download_error_reason(exc)
                    state["special_count"] += 1
                    _record_index(index_path, index, item=item, outcome=outcome, error=error)
                    _mark_processed(
                        state_path,
                        state,
                        page=page,
                        item_index=item_index,
                        item=item,
                        outcome=outcome,
                    )
                    continue
                outcome = "downloaded"
                state["downloaded_count"] += 1
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
        lock_file.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="小鹅通音频严格串行下载器")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--mode", choices=("full", "incremental"), default="full")
    parser.add_argument("--between-items", type=float, default=1)
    parser.add_argument("--retry-forever", action="store_true")
    parser.add_argument("--retry-seconds", type=float, default=300)
    args = parser.parse_args()
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
                    "downloaded_count": state.get("downloaded_count"),
                    "skipped_video_count": state.get("skipped_video_count"),
                    "special_count": state.get("special_count"),
                    "boundary": state.get("boundary"),
                },
                ensure_ascii=False,
            )
        )
        return


if __name__ == "__main__":
    main()
