"""Pure, deterministic parsing of Baidu Netdisk ``/api/list`` pages.

No browser, network or clock beyond ``observed_at``.  A directory listing is
complete only when paging reached an *empty* terminal page; a short page or a
stable DOM never proves completeness.  Bodies are parsed with ``json.loads``
from the raw response text so ``fs_id`` keeps full integer precision.  Any
doubt is reported as ``unavailable`` / ``incomplete`` / ``unstable`` -- never
``listed`` -- and nothing is retried here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

API_HOST = "pan.baidu.com"
API_PATH = "/api/list"
PAGE_KEYS = ("dir", "page", "num")  # the only query keys a request may change


class PageError(Exception):
    def __init__(self, status: str, reason: str, **detail):
        super().__init__(reason)
        self.status, self.reason, self.detail = status, reason, detail


def join(parent: str, name: str) -> str:
    return f"{parent.rstrip('/')}/{name}"


def _int(value, *, minimum: int = 0) -> int | None:
    return value if type(value) is int and value >= minimum else None


def parse_row(row: object, dir_path: str) -> dict:
    """One ``list`` row -> ``{id,name,path,is_dir,size,version}`` or ``PageError``."""
    if not isinstance(row, dict):
        raise PageError("unavailable", "row_not_object")
    fs_id = _int(row.get("fs_id"), minimum=1)
    name = row.get("server_filename")
    path = row.get("path")
    isdir = row.get("isdir")
    size = _int(row.get("size"))
    mtime = _int(row.get("server_mtime"))
    if fs_id is None:
        raise PageError("unavailable", "row_bad_fs_id")
    if not isinstance(name, str) or not name or "/" in name or name in {".", ".."}:
        raise PageError("unavailable", "row_bad_name")
    if not isinstance(path, str) or not path.startswith("/"):
        raise PageError("unavailable", "row_bad_path")
    parent, _, last = path.rpartition("/")
    if (parent or "/") != dir_path:
        raise PageError("unavailable", "row_wrong_parent")
    if last != name or path != join(dir_path, name):
        raise PageError("unavailable", "row_name_path_conflict")
    if type(isdir) is not int or isdir not in (0, 1):
        raise PageError("unavailable", "row_bad_isdir")
    if size is None or mtime is None:
        raise PageError("unavailable", "row_bad_size_or_mtime")
    return {"id": str(fs_id), "name": name, "path": path, "is_dir": bool(isdir),
            "size": size, "version": mtime}


def parse_page(response: object, dir_path: str) -> list[dict]:
    """``{status, text}`` from the browser fetch -> parsed rows of one page."""
    if not isinstance(response, dict):
        raise PageError("unavailable", "fetch_result_invalid")
    http = response.get("status")
    text = response.get("text")
    if http != 200:
        raise PageError("unavailable", "http_status", http_status=http if type(http) is int else None)
    if not isinstance(text, str):
        raise PageError("unavailable", "body_not_text")
    try:
        body = json.loads(text)
    except ValueError:
        raise PageError("unavailable", "body_not_json") from None
    if not isinstance(body, dict):
        raise PageError("unavailable", "body_not_object")
    errno = body.get("errno")
    if type(errno) is not int:
        raise PageError("unavailable", "errno_missing")
    if errno != 0:  # meaning of non-zero codes is not guessed
        raise PageError("unavailable", "errno", errno=errno)
    rows = body.get("list")
    if not isinstance(rows, list):
        raise PageError("unavailable", "list_missing")
    return [parse_row(r, dir_path) for r in rows]


def snapshot_sha256(entries: list[dict]) -> str:
    canon = sorted((e["id"], e["path"], e["is_dir"], e["size"], e["version"]) for e in entries)
    return hashlib.sha256(json.dumps(canon, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scan_listing(fetch_page: Callable[[int, int], object], path: str, page_size: int = 100,
                 max_pages: int = 100) -> dict:
    """Page ``fetch_page(page, num)`` from 1 until an empty page, each page fetched once.

    ``status`` is ``listed`` (complete), ``incomplete`` (page budget spent before
    an empty page), ``unstable`` (an id/path repeats across pages, i.e. the list
    shifted while paging) or ``unavailable`` (fetch error, HTTP/errno refusal,
    malformed or oversize page)."""
    if type(page_size) is not int or page_size < 1 or type(max_pages) is not int or max_pages < 1:
        raise ValueError("page_size and max_pages must be positive integers")
    base = {"path": path, "page_size": page_size}
    entries: list[dict] = []
    ids: set[str] = set()
    paths: set[str] = set()
    counts: list[int] = []
    for page in range(1, max_pages + 1):
        try:
            response = fetch_page(page, page_size)
        except Exception as exc:  # noqa: BLE001 - reported, never retried
            return {**base, "status": "unavailable", "reason": "fetch_failed",
                    "error": type(exc).__name__, "page": page, "page_counts": counts}
        try:
            rows = parse_page(response, path)
        except PageError as exc:
            return {**base, "status": exc.status, "reason": exc.reason, **exc.detail,
                    "page": page, "page_counts": counts}
        counts.append(len(rows))
        if len(rows) > page_size:
            return {**base, "status": "unavailable", "reason": "page_oversize", "page": page,
                    "page_counts": counts}
        if not rows:
            return {**base, "status": "listed", "count": len(entries), "entries": entries,
                    "complete": True,
                    "complete_basis": {"terminal_empty_page": page, "page_counts": counts},
                    "snapshot_sha256": snapshot_sha256(entries), "observed_at": _now()}
        for row in rows:
            if row["id"] in ids or row["path"] in paths:
                return {**base, "status": "unstable",
                        "reason": "duplicate_id" if row["id"] in ids else "duplicate_path",
                        "page": page, "page_counts": counts}
            ids.add(row["id"])
            paths.add(row["path"])
            entries.append(row)
    return {**base, "status": "incomplete", "reason": "page_budget_exhausted",
            "page_counts": counts}


def compare_scans(first: dict, second: dict) -> dict:
    """Two complete scans of one path -> the second, or ``unstable`` if they differ."""
    for scan in (first, second):
        if scan.get("status") != "listed":
            return scan
    if first["snapshot_sha256"] != second["snapshot_sha256"]:
        return {"path": first["path"], "status": "unstable", "reason": "scans_differ",
                "snapshots": [first["snapshot_sha256"], second["snapshot_sha256"]]}
    return {**second, "complete_basis": {**second["complete_basis"], "consistent_scans": 2}}


def prove_child(parent_scan: dict, path: str, expected_id: str | None = None, *,
                want_dir: bool | None = True) -> dict:
    """Exactly one row of a complete parent listing names ``path`` (by full path and name)."""
    if parent_scan.get("status") != "listed":
        return {"status": parent_scan.get("status", "unavailable"), "reason": "parent_not_listed",
                "at": parent_scan.get("path"),
                # Preserve only public diagnostic fields, never response bodies or URLs.
                "parent_read": {k: parent_scan[k] for k in
                                ("status", "reason", "error", "errno", "http_status")
                                if k in parent_scan}}
    name = path.rpartition("/")[2]
    matches = [e for e in parent_scan["entries"] if e["path"] == path and e["name"] == name]
    if not matches:
        return {"status": "missing", "at": path}
    if len(matches) > 1:
        return {"status": "ambiguous", "at": path}
    row = matches[0]
    if want_dir is not None and row["is_dir"] is not want_dir:
        return {"status": "type_mismatch", "at": path, "id": row["id"], "is_dir": row["is_dir"]}
    if expected_id is not None and row["id"] != str(expected_id):
        return {"status": "id_mismatch", "at": path, "id": row["id"]}
    return {"status": "ok", "entry": row}


def validate_seed(url: object) -> str | None:
    """A captured request URL usable as seed: ``https://pan.baidu.com/api/list?...dir=...``."""
    if not isinstance(url, str):
        return None
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != API_HOST or parts.port or parts.path != API_PATH:
        return None
    keys = [k for k, _ in parse_qsl(parts.query, keep_blank_values=True)]
    if "dir" not in keys or any(keys.count(k) > 1 for k in PAGE_KEYS):
        return None
    return url


def page_url(seed: str, dir_path: str, page: int, num: int) -> str:
    """The seed with only ``dir``/``page``/``num`` replaced; every other key kept verbatim."""
    parts = urlsplit(seed)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in PAGE_KEYS]
    pairs += [("dir", dir_path), ("page", str(page)), ("num", str(num))]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), ""))
