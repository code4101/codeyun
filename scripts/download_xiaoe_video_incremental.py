from __future__ import annotations

import argparse
import hashlib
import json
import msvcrt
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.xiaoe_video_archive import build_archive_path, download_hls_video
from scripts.download_xiaoe_video_daemon import (
    ITEMS_PER_PAGE,
    XiaoeLoginRequired,
    XiaoeUnsupportedPreview,
    _capture_playlist,
    _catalog_total,
    _complete_page_metadata,
    _get_or_open_list_tab,
    _goto_page,
    _load_xlproject_env,
    _login_with_env,
    _normalize_page_title,
    _open_browser,
    _open_detail,
    _read_page_rows,
)

HELPER_DIR_NAME = "_下载辅助"
INDEX_FILE_NAME = "catalog-index.json"
STATE_FILE_NAME = "incremental-state.json"


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
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def _item_key(title: str, published_at: str) -> str:
    normalized = f"{published_at.strip()}\n{_normalize_page_title(title)}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _legacy_special_keys(output_dir: Path) -> set[str]:
    state = _read_json(output_dir / HELPER_DIR_NAME / "current-state.json")
    keys: set[str] = set()
    for item in state.get("special_items") or []:
        title = str(item.get("title") or "")
        published_at = str(item.get("published_at") or "")
        if title and published_at:
            keys.add(_item_key(title, published_at))
    return keys


def _item_is_known(
    *,
    item: dict[str, str],
    output_dir: Path,
    index: dict[str, Any],
    legacy_special_keys: set[str],
) -> bool:
    key = _item_key(item["title"], item["published_at"])
    if key in (index.get("items") or {}) or key in legacy_special_keys:
        return True
    return build_archive_path(output_dir, item["title"], item["published_at"]).exists()


def _record_index_item(
    index_path: Path,
    index: dict[str, Any],
    *,
    item: dict[str, str],
    outcome: str,
    detail_url: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    entries = index.setdefault("items", {})
    entries[_item_key(item["title"], item["published_at"])] = {
        **item,
        "outcome": outcome,
        "detail_url": detail_url,
        "result": result,
        "error": error,
        "processed_at": _now(),
    }
    index["version"] = 1
    index["updated_at"] = _now()
    _write_json(index_path, index)


def _finish_state(
    state_path: Path,
    state: dict[str, Any],
    *,
    status: str,
    boundary: dict[str, Any] | None = None,
) -> None:
    state["status"] = status
    state["finished_at"] = _now()
    state["updated_at"] = _now()
    state["boundary"] = boundary
    state.pop("current", None)
    _write_json(state_path, state)


def run_incremental(args: argparse.Namespace) -> dict[str, Any]:
    _load_xlproject_env()
    helper_dir = args.output_dir / HELPER_DIR_NAME
    helper_dir.mkdir(parents=True, exist_ok=True)
    state_path = helper_dir / STATE_FILE_NAME
    index_path = helper_dir / INDEX_FILE_NAME
    lock_path = helper_dir / "daemon.lock"

    lock_file = lock_path.open("a+b")
    try:
        if lock_file.seek(0, 2) == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        lock_file.close()
        raise RuntimeError("已有小鹅通下载任务在运行") from exc

    state: dict[str, Any] = {
        "status": "starting",
        "pid": os.getpid(),
        "started_at": _now(),
        "updated_at": _now(),
        "new_downloaded": [],
        "new_special": [],
        "last_error": None,
    }
    _write_json(state_path, state)
    index = _read_json(index_path)
    legacy_special_keys = _legacy_special_keys(args.output_dir)
    detail_tab = None

    try:
        browser = _open_browser()
        list_tab = _get_or_open_list_tab(browser)
        try:
            _goto_page(list_tab, 1)
        except XiaoeLoginRequired:
            _login_with_env(list_tab)
            _goto_page(list_tab, 1)

        page = 1
        while True:
            _goto_page(list_tab, page)
            rows = _complete_page_metadata(list_tab, _read_page_rows(list_tab), page)
            total_items = _catalog_total(list_tab)
            state["catalog_total"] = total_items

            for item_index, item in enumerate(rows):
                if _item_is_known(
                    item=item,
                    output_dir=args.output_dir,
                    index=index,
                    legacy_special_keys=legacy_special_keys,
                ):
                    _finish_state(
                        state_path,
                        state,
                        status="completed",
                        boundary={"page": page, "item_index": item_index, **item},
                    )
                    return state

                state["status"] = "capturing"
                state["updated_at"] = _now()
                state["current"] = {"page": page, "item_index": item_index, **item}
                _write_json(state_path, state)
                detail_tab = _open_detail(browser, list_tab, item_index)
                detail_url = str(detail_tab.url)
                try:
                    playlist_url = _capture_playlist(detail_tab)
                except XiaoeUnsupportedPreview as exc:
                    detail_tab.close()
                    detail_tab = None
                    record = {
                        "page": page,
                        "item_index": item_index,
                        **item,
                        "detail_url": detail_url,
                        "error": str(exc),
                    }
                    state["new_special"].append(record)
                    _record_index_item(
                        index_path,
                        index,
                        item=item,
                        outcome="special",
                        detail_url=detail_url,
                        error=str(exc),
                    )
                    _write_json(state_path, state)
                    continue

                detail_tab.close()
                detail_tab = None
                state["status"] = "downloading"
                state["updated_at"] = _now()
                state["current"]["detail_url"] = detail_url
                _write_json(state_path, state)
                result = download_hls_video(
                    playlist_url=playlist_url,
                    title=item["title"],
                    published_at=item["published_at"],
                    output_dir=args.output_dir,
                )
                state["new_downloaded"].append(
                    {"page": page, "item_index": item_index, **item, **result}
                )
                _record_index_item(
                    index_path,
                    index,
                    item=item,
                    outcome="downloaded",
                    detail_url=detail_url,
                    result=result,
                )
                _write_json(state_path, state)

            if total_items is not None and page * ITEMS_PER_PAGE >= total_items:
                _finish_state(state_path, state, status="completed")
                return state
            page += 1
    except Exception as exc:
        state["status"] = "failed"
        state["updated_at"] = _now()
        state["last_error"] = f"{type(exc).__name__}: {exc}"
        _write_json(state_path, state)
        raise
    finally:
        if detail_tab is not None:
            try:
                detail_tab.close()
            except Exception:
                pass
        lock_file.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="小鹅通视频每周严格串行增量更新")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = run_incremental(args)
    print(
        json.dumps(
            {
                "status": result["status"],
                "catalog_total": result.get("catalog_total"),
                "new_downloaded_count": len(result["new_downloaded"]),
                "new_special_count": len(result["new_special"]),
                "boundary": result.get("boundary"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
