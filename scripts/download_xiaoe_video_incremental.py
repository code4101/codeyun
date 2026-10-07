from __future__ import annotations

import argparse
import hashlib
import json
import msvcrt
import os
import re
import sys
from datetime import datetime
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.xiaoe_video_archive import build_archive_path, download_hls_video, probe_video
from scripts.download_xiaoe_video_daemon import (
    XiaoeLoginRequired,
    XiaoeUnsupportedPreview,
    _capture_playlist,
    _get_or_open_list_tab,
    _goto_page,
    _load_xlproject_env,
    _login_with_env,
    _normalize_page_title,
    _open_browser,
    read_video_catalog,
    open_video_detail,
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


def _legacy_special_ids(output_dir: Path) -> set[str]:
    state = _read_json(output_dir / HELPER_DIR_NAME / "current-state.json")
    return {str(item.get("detail_url") or "").split("/")[-1].split("?")[0]
            for item in state.get("special_items") or [] if item.get("detail_url")}


def _item_is_known(
    *,
    item: dict[str, str],
    output_dir: Path,
    index: dict[str, Any],
    legacy_special_keys: set[str],
) -> bool:
    key = _item_key(item["title"], item["published_at"])
    entry = (index.get("items") or {}).get(key) or {}
    if entry.get("outcome") == "special" or key in legacy_special_keys:
        return True
    return _existing_archive(item, output_dir, index) is not None


def _existing_archive(item: dict[str, str], output_dir: Path, index: dict[str, Any]) -> Path | None:
    """文件必须实际存在且非空；索引中的 downloaded 不能替代落盘证据。"""
    canonical = build_archive_path(output_dir, _normalize_page_title(item["title"]), item["published_at"], resource_id=item.get("resource_id"))
    raw = build_archive_path(output_dir, item["title"], item["published_at"], resource_id=item.get("resource_id"))
    entry = (index.get("items") or {}).get(_item_key(item["title"], item["published_at"])) or {}
    candidates = [canonical, raw, build_archive_path(output_dir,
        re.sub(r"\s+", " ", _normalize_page_title(item["title"])), item["published_at"],
        resource_id=item.get("resource_id"))]
    # 旧全量下载器把列表徽标换行写进文件名，Windows 清洗后成为下划线。
    # 精确匹配已知徽标变体，仍保留日期与标题约束，不按相似标题猜测文件。
    for badge in ("免费", "指定用户", "加密"):
        candidates.append(build_archive_path(output_dir, _normalize_page_title(item["title"]) + "\n" + badge,
                                             item["published_at"], resource_id=item.get("resource_id")))
    recorded_path = (entry.get("result") or {}).get("path")
    if recorded_path:
        recorded = Path(recorded_path).resolve()
        if recorded.is_relative_to(output_dir.resolve()):
            candidates.append(recorded)
    for path in candidates:
        if path.is_file() and path.stat().st_size > 0:
            return path
    if canonical.parent.exists():
        legacy_name = re.compile(re.escape(canonical.stem) + r"_指定用户(?:[\s_]*\d+天)?\.mp4")
        matches = [path for path in canonical.parent.iterdir()
                   if legacy_name.fullmatch(path.name) and path.is_file() and path.stat().st_size > 0]
        if len(matches) == 1:
            return matches[0]
    return None


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


def get_video_archive_status(output_dir: Path) -> dict[str, Any]:
    """从最近完整扫描及当前实际文件回读归档状态，不启动浏览器或下载。"""
    helper_dir = Path(output_dir) / HELPER_DIR_NAME
    audit = _read_json(helper_dir / "video-audit.json")
    state = _read_json(helper_dir / STATE_FILE_NAME)
    index = _read_json(helper_dir / INDEX_FILE_NAME)
    legacy_special = _legacy_special_keys(Path(output_dir))
    special_ids = _legacy_special_ids(Path(output_dir))
    legacy_records = _read_json(helper_dir / "current-state.json").get("special_items") or []
    legacy_reasons = {str(row.get("detail_url") or "").split("/")[-1].split("?")[0]: row.get("error")
                      for row in legacy_records}
    items = []
    for row in audit.get("items") or []:
        entry = (index.get("items") or {}).get(_item_key(row["title"], row["published_at"])) or {}
        existing = _existing_archive(row, Path(output_dir), index)
        special = entry.get("outcome") == "special" or _item_key(row["title"], row["published_at"]) in legacy_special or row.get("resource_id") in special_ids
        items.append({**row, "disposition": "existing" if existing else "special" if special else "missing",
                      "path": str(existing) if existing else None,
                      "reason": (entry.get("error") or legacy_reasons.get(row.get("resource_id"))) if special else None})
    return {"catalog_total": audit.get("catalog_total"), "scanned_at": audit.get("scanned_at"),
            "counts": {label: sum(row["disposition"] == label for row in items)
                       for label in ("existing", "missing", "special")},
            "run": state, "items": items}


def verify_video_archives(output_dir: Path) -> dict[str, Any]:
    """核验最近目录所匹配的全部文件的音视频轨道、时长及大小，保留逐项证据。

    只并行本地 ffprobe 检查；网络下载仍严格串行。最终文件由下载器原子落盘。
    """
    report = get_video_archive_status(output_dir)
    existing = [row for row in report["items"] if row["disposition"] == "existing"]

    def verify(row: dict[str, Any]) -> dict[str, Any]:
        try:
            probe = probe_video(Path(row["path"]))
            if probe.duration_seconds <= 0 or probe.size_bytes <= 0:
                raise RuntimeError("文件时长或大小无效")
            return {"resource_id": row["resource_id"], "path": row["path"], "valid": True, "probe": asdict(probe)}
        except Exception as exc:
            return {"resource_id": row["resource_id"], "path": row["path"], "valid": False, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        checked = list(pool.map(verify, existing))
    result = {"checked_at": _now(), "checked_count": len(checked),
              "invalid_count": sum(not row["valid"] for row in checked), "items": checked}
    _write_json(Path(output_dir) / HELPER_DIR_NAME / "video-integrity.json", result)
    return result


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
        "failed_items": [],
        "last_error": None,
    }
    _write_json(state_path, state)
    index = _read_json(index_path)
    legacy_special_keys = _legacy_special_keys(args.output_dir)
    special_ids = _legacy_special_ids(args.output_dir)
    detail_tab = None
    list_tab = None
    browser = None
    original_tab_ids: set[str] = set()

    try:
        browser = _open_browser()
        original_tab_ids = set(browser.tab_ids)
        list_tab = _get_or_open_list_tab(browser)
        try:
            _goto_page(list_tab, 1)
        except XiaoeLoginRequired:
            _login_with_env(list_tab)
            _goto_page(list_tab, 1)

        state["status"] = "scanning"
        _write_json(state_path, state)
        catalog = read_video_catalog(list_tab)
        state["catalog_total"] = len(catalog)
        audit: dict[str, Any] = {"scanned_at": _now(), "catalog_total": len(catalog), "items": []}
        pending: list[dict[str, str]] = []
        for item in catalog:
            key = _item_key(item["title"], item["published_at"])
            entry = (index.get("items") or {}).get(key) or {}
            existing = _existing_archive(item, args.output_dir, index)
            special = entry.get("outcome") == "special" or key in legacy_special_keys or item.get("resource_id") in special_ids
            disposition = "existing" if existing else "special" if special else "missing"
            audit["items"].append({**item, "disposition": disposition,
                "path": str(existing) if existing else None, "reason": entry.get("error") if special else None})
            if disposition == "missing" or (disposition == "special" and getattr(args, "retry_special", False)):
                pending.append(item)
        audit["counts"] = {label: sum(row["disposition"] == label for row in audit["items"])
                           for label in ("existing", "missing", "special")}
        audit_path = helper_dir / "video-audit.json"
        _write_json(audit_path, audit)
        state["audit_path"] = str(audit_path)
        state["audit_counts"] = audit["counts"]
        state["pending_count"] = len(pending)
        _write_json(state_path, state)
        if getattr(args, "audit_only", False):
            _finish_state(state_path, state, status="audited")
            return state

        for item_index, item in enumerate(pending):
            detail_url = ""
            try:
                state["status"] = "capturing"
                state["updated_at"] = _now()
                state["current"] = {"pending_index": item_index, **item}
                _write_json(state_path, state)
                detail_tab = open_video_detail(browser, item["resource_id"])
                detail_url = str(detail_tab.url)
                try:
                    playlist_url = _capture_playlist(detail_tab)
                except XiaoeUnsupportedPreview as exc:
                    detail_tab.close()
                    detail_tab = None
                    record = {
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
                    title=_normalize_page_title(item["title"]),
                    published_at=item["published_at"],
                    output_dir=args.output_dir,
                    resource_id=item["resource_id"],
                )
                state["new_downloaded"].append(
                    {**item, **result}
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
            except XiaoeLoginRequired:
                raise
            except Exception as exc:
                # 某一视频失败仍继续后续条目；最终失败状态促使定时作业重试真实漏项。
                state["failed_items"].append({**item, "error": f"{type(exc).__name__}: {exc}"})
                _write_json(state_path, state)
            finally:
                if detail_tab is not None:
                    detail_tab.close()
                    detail_tab = None
        state["remaining_count"] = len(state["failed_items"])
        _finish_state(state_path, state, status="failed" if state["failed_items"] else "completed")
        return state
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
        if browser is not None and list_tab is not None and list_tab.tab_id not in original_tab_ids:
            if list_tab.tab_id in browser.tab_ids and len(browser.tab_ids) > 1:
                list_tab.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="小鹅通视频每周严格串行增量更新")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--audit-only", action="store_true", help="核对完整目录并输出漏项，不下载")
    parser.add_argument("--retry-special", action="store_true", help="重新检查此前不可预览的条目")
    parser.add_argument("--status", action="store_true", help="回读最近扫描与实际文件状态，不启动下载")
    args = parser.parse_args()
    if args.status:
        report = get_video_archive_status(args.output_dir)
        report.pop("items")
        print(json.dumps(report, ensure_ascii=False))
        return
    result = run_incremental(args)
    print(
        json.dumps(
            {
                "status": result["status"],
                "catalog_total": result.get("catalog_total"),
                "new_downloaded_count": len(result["new_downloaded"]),
                "new_special_count": len(result["new_special"]),
                "boundary": result.get("boundary"),
                "audit_counts": result.get("audit_counts"),
                "failed_count": len(result["failed_items"]),
            },
            ensure_ascii=False,
        )
    )
    if result["status"] == "failed":
        raise RuntimeError(f"视频归档仍有 {len(result['failed_items'])} 个失败条目，详见 {result['audit_path']}")


if __name__ == "__main__":
    main()
