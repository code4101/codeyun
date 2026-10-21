"""List and rename remote folders through the official Baidu Netdisk web UI.

Runs only inside ``web.owned_context`` (browser lock + verified own browser);
never launches browsers, sends raw HTTP or reads private storage.  A listing
is trusted only when the URL names the exact folder, the page is not loading
and the row snapshot stays unchanged for ``settle`` consecutive polls (an
empty folder is allowed).  A rename is submitted at most once: any failure
after pressing Enter is reported as ``ambiguous`` and never retried.
Results carry only status, paths, row ids and counts.

CLI::

    uv run python -m backend.core.baidu_netdisk.web_directories ls PATH
    uv run python -m backend.core.baidu_netdisk.web_directories rename PATH NEW_NAME [--expected-id ID]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from typing import Callable
from urllib.parse import parse_qs, quote, urlsplit

from . import web
from .local_files import validate_file_name, validate_remote_dir

ROW = "css:tr.wp-s-pan-table__body-row[data-id]"
ROW_TITLE = "css:.wp-s-pan-list__file-name-content[data-title]"
ROW_FOLDER = "css:img[alt=folder]"
MORE_BUTTON = 'css:button[title="更多"]'
RENAME_BUTTON = 'css:button[title="重命名"]'
EDIT_BOX = "css:.wp-s-pan-list__file-name-edit textarea"
_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")


class ListingError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def route(path: str) -> str:
    return f"{web.MAIN_URL}#/index?category=all&path={quote(path, safe='')}"


def url_path(url: str) -> str | None:
    """Folder path named by an official listing URL, else ``None``."""
    parts = urlsplit(str(url or ""))
    if parts.scheme != "https" or parts.hostname != "pan.baidu.com" or parts.path != "/disk/main":
        return None
    frag_path, _, query = parts.fragment.partition("?")
    values = parse_qs(query).get("path") if frag_path == "/index" else None
    return values[0] if values and len(values) == 1 else None


def _join(parent: str, name: str) -> str:
    return f"{parent.rstrip('/')}/{name}"


def _snapshot(tab, path: str) -> list[dict]:
    entries, seen = [], set()
    for row in tab.eles(ROW, timeout=0) or ():
        row_id = str(row.attr("data-id") or "")
        title = row.ele(ROW_TITLE, timeout=0)
        name = str(title.attr("data-title") or "") if title else ""
        if not _ID_RE.match(row_id) or row_id in seen or not name:
            raise ListingError("unreadable_row")
        seen.add(row_id)
        entries.append({"id": row_id, "name": name, "path": _join(path, name),
                        "is_dir": bool(row.ele(ROW_FOLDER, timeout=0))})
    return entries


def _settled(tab, path: str, *, timeout: float, poll: float, settle: int) -> list[dict]:
    """Wait until the URL names ``path``, the page is idle and rows are stable."""
    deadline, last, stable = time.monotonic() + timeout, None, 0
    while True:
        snap = None
        if url_path(tab.url) == path and not tab.states.is_loading and tab.states.ready_state == "complete":
            snap = _snapshot(tab, path)
        stable = stable + 1 if snap is not None and snap == last else 0
        last = snap
        if stable >= settle:
            return snap
        if time.monotonic() >= deadline:
            raise ListingError("listing_not_settled" if url_path(tab.url) == path else "path_mismatch")
        time.sleep(poll)


def _open(tab, path: str, **wait) -> list[dict]:
    tab.get(route(path))
    tab.refresh()  # full reload, so no rows of a previously shown folder survive
    return _settled(tab, path, **wait)


def _by_name(entries: list[dict], name: str) -> dict | None:
    return next((e for e in entries if e["name"] == name), None)


def list_directory(path: str, *, context: Callable[[], object] | None = None,
                   timeout: float = 20.0, poll: float = 0.3, settle: int = 3) -> dict:
    """Entries of one remote folder: ``{status, path, count, entries[id,name,path,is_dir]}``."""
    try:
        path = validate_remote_dir(path)
    except ValueError:
        return {"status": "invalid", "reason": "invalid_path"}
    try:
        with (context or web.owned_context)() as owned:
            entries = _open(owned.main_tab(), path, timeout=timeout, poll=poll, settle=settle)
    except ListingError as exc:
        return {"status": "unavailable", "path": path, "reason": exc.reason}
    except web.OwnershipError as exc:
        return {"status": "blocked", "path": path, "reason": exc.reason}
    except web.WebError:
        return {"status": "blocked", "path": path, "reason": "browser_lock_busy"}
    return {"status": "listed", "path": path, "count": len(entries), "entries": entries}


def rename_directory(path: str, new_name: str, expected_id: str | None = None, *,
                     context: Callable[[], object] | None = None,
                     timeout: float = 20.0, poll: float = 0.3, settle: int = 3) -> dict:
    """Rename folder ``path`` to ``new_name`` in place, submitting at most once.

    Statuses: ``renamed``, ``already_renamed`` (only with matching ``expected_id``),
    ``invalid``, ``blocked``, ``unavailable``, ``source_missing``, ``not_directory``,
    ``id_mismatch``, ``target_exists``, ``not_submitted`` (UI step failed before
    Enter), ``ambiguous`` (submitted but not verified: inspect, never retry)."""
    try:
        path = validate_remote_dir(path)
        new_name = validate_file_name(new_name)
    except ValueError:
        return {"status": "invalid", "reason": "invalid_path_or_name"}
    if path == "/":
        return {"status": "invalid", "reason": "root_rename_forbidden"}
    if expected_id is not None and not _ID_RE.match(expected_id):
        return {"status": "invalid", "reason": "invalid_expected_id"}
    parent, _, old_name = path.rpartition("/")
    parent = parent or "/"
    if new_name == old_name:
        return {"status": "invalid", "reason": "same_name"}
    target = _join(parent, new_name)
    base = {"path": path, "new_path": target}
    wait = {"timeout": timeout, "poll": poll, "settle": settle}
    try:
        with (context or web.owned_context)() as owned:
            tab = owned.main_tab()
            try:
                entries = _open(tab, parent, **wait)
            except ListingError as exc:
                return {**base, "status": "unavailable", "reason": exc.reason}
            return {**base, **_rename(tab, parent, entries, old_name, new_name, expected_id, wait)}
    except web.OwnershipError as exc:
        return {**base, "status": "blocked", "reason": exc.reason}
    except web.WebError:
        return {**base, "status": "blocked", "reason": "browser_lock_busy"}


def _rename(tab, parent, entries, old_name, new_name, expected_id, wait) -> dict:
    source, done = _by_name(entries, old_name), _by_name(entries, new_name)
    if source is None:
        if done and done["is_dir"] and expected_id is not None and done["id"] == expected_id:
            return {"status": "already_renamed", "id": done["id"]}
        if done and expected_id is None:
            return {"status": "source_missing", "reason": "target_present_expected_id_required"}
        if done:
            return {"status": "source_missing", "reason": "target_id_mismatch"}
        return {"status": "source_missing"}
    row_id = source["id"]
    if not source["is_dir"]:
        return {"status": "not_directory", "id": row_id}
    if expected_id is not None and row_id != expected_id:
        return {"status": "id_mismatch", "id": row_id}
    if any(e["name"].casefold() == new_name.casefold() for e in entries if e["id"] != row_id):
        return {"status": "target_exists", "id": row_id}

    stage = "find_row"
    try:
        row = tab.ele(f'css:tr.wp-s-pan-table__body-row[data-id="{row_id}"]', timeout=0)
        if not row:
            raise LookupError
        stage = "hover"
        tab.actions.move_to(row)
        stage = "more_menu"
        row.ele(MORE_BUTTON).click()
        stage = "rename_menu"
        row.ele(RENAME_BUTTON).click()
        stage = "edit_box"
        boxes = tab.eles(EDIT_BOX)
        if len(boxes) != 1 or boxes[0].value != old_name:
            raise LookupError
        box = boxes[0]
        stage = "input"
        box.input(new_name, clear=True)
    except Exception as exc:  # noqa: BLE001 - nothing submitted yet
        return {"status": "not_submitted", "id": row_id, "reason": stage, "error": type(exc).__name__}

    from DrissionPage.common import Keys

    try:
        box.input(Keys.ENTER)
    except Exception as exc:  # noqa: BLE001 - the key may have landed; never retry
        return {"status": "ambiguous", "id": row_id, "reason": "submit_error", "error": type(exc).__name__}

    def applied(rows: list[dict]) -> bool:
        row = next((e for e in rows if e["id"] == row_id), None)
        return bool(row and row["name"] == new_name and row["is_dir"]) and _by_name(rows, old_name) is None

    def shown() -> bool:
        try:
            return applied(_snapshot(tab, parent))
        except ListingError:  # edit box may still replace the title cell
            return False

    try:
        deadline = time.monotonic() + wait["timeout"]
        while not shown():
            if time.monotonic() >= deadline:
                return {"status": "ambiguous", "id": row_id, "reason": "readback_unconfirmed"}
            time.sleep(wait["poll"])
        if not applied(_open(tab, parent, **wait)):
            return {"status": "ambiguous", "id": row_id, "reason": "reload_unconfirmed"}
    except Exception as exc:  # noqa: BLE001 - submitted; report, never retry
        return {"status": "ambiguous", "id": row_id, "reason": "readback_error", "error": type(exc).__name__}
    return {"status": "renamed", "id": row_id}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List/rename Baidu Netdisk folders via the official web UI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ls", help="list one remote folder")
    p.add_argument("path")
    p = sub.add_parser("rename", help="rename one remote folder in place (submits at most once)")
    p.add_argument("path")
    p.add_argument("new_name")
    p.add_argument("--expected-id", help="row data-id; required to confirm an already completed rename")
    args = parser.parse_args(argv)
    if args.cmd == "ls":
        result = list_directory(args.path)
        ok = result["status"] == "listed"
    else:
        result = rename_directory(args.path, args.new_name, args.expected_id)
        ok = result["status"] in {"renamed", "already_renamed"}
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 3 if result["status"] == "ambiguous" else 2


if __name__ == "__main__":
    raise SystemExit(main())
