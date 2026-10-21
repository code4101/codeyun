"""List and rename remote folders through the official Baidu Netdisk web UI.

Runs only inside ``web.owned_context`` (browser lock + verified own browser);
never launches browsers, sends raw HTTP or reads private storage.  A listing
is trusted only when the URL names the exact folder, the page is not loading
and the row snapshot stays unchanged for ``settle`` consecutive polls (an
empty folder is allowed).  A rename is submitted at most once: any failure
after pressing Enter is reported as ``ambiguous`` and never retried.
Results carry only status, paths, row ids and counts.

Folder creation follows the same rules: the parent must first be seen as a
folder row (matching ``expected_parent_id``) in a fresh listing of its own
parent -- a URL path alone never proves a folder exists -- then exactly one
inline editor is filled, its value read back verbatim and Enter pressed once.
Every navigation, click and key press is spaced by at least ``ACTION_GAP``
seconds.  Create/ensure work in one tab they open themselves and close only
that tab; pre-existing tabs are never navigated or closed.

CLI::

    uv run python -m backend.core.baidu_netdisk.web_directories ls PATH
    uv run python -m backend.core.baidu_netdisk.web_directories rename PATH NEW_NAME [--expected-id ID]
    uv run python -m backend.core.baidu_netdisk.web_directories mkdir PARENT NEW_NAME [--expected-parent-id ID]
    uv run python -m backend.core.baidu_netdisk.web_directories ensure PATH --anchor ANCHOR [--anchor-id ID]
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
CREATE_BUTTON = 'css:button[title="新建文件夹"]'
CREATE_EDITOR_CLASS = "u-textarea__inner"
ACTION_GAP = 3.2  # seconds before every account navigation/click/key press
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
                   timeout: float = 20.0, poll: float = 0.3, settle: int = 3,
                   pace: Callable[[], None] | None = None) -> dict:
    """Entries of one remote folder: ``{status, path, count, entries[id,name,path,is_dir]}``.

    One listing makes exactly two account navigations, ``tab.get`` of the folder
    route then a full ``tab.refresh``; ``pace()`` is called immediately before
    *each* of them, inside the context.  The default sleeps ``ACTION_GAP``
    seconds; callers sharing one account across processes inject their shared
    pacer (any zero-argument callable, e.g. a cross-process file pacer's wait).
    An invalid path calls neither ``pace`` nor the browser.  A navigation or
    ``pace`` error propagates unchanged and is never retried: a failed ``get``
    means no ``refresh``.  The context's ``main_tab()`` is used as is -- no tab
    is opened or closed here."""
    try:
        path = validate_remote_dir(path)
    except ValueError:
        return {"status": "invalid", "reason": "invalid_path"}
    if pace is None:
        pace = lambda: time.sleep(ACTION_GAP)  # noqa: E731
    try:
        with (context or web.owned_context)() as owned:
            entries = _paced_open(owned.main_tab(), path, pace,
                                  {"timeout": timeout, "poll": poll, "settle": settle})
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


# ---- create ------------------------------------------------------------------

def _paced_open(tab, path: str, pace: Callable[[], None], wait: dict) -> list[dict]:
    pace()
    tab.get(route(path))
    pace()
    tab.refresh()
    return _settled(tab, path, **wait)


def _verify_folder(tab, path: str, expected_id: str | None, pace, wait) -> dict:
    """``path`` must be exactly one folder row, id ``expected_id``, in a fresh listing of its parent.

    The drive root ``/`` has no row and needs no id."""
    if path == "/":
        return {"status": "ok", "id": None}
    parent, _, name = path.rpartition("/")
    try:
        entries = _paced_open(tab, parent or "/", pace, wait)
    except ListingError as exc:
        return {"status": "unavailable", "reason": exc.reason, "at": parent or "/"}
    matches = [e for e in entries if e["name"] == name]
    if not matches:
        return {"status": "parent_missing", "at": path}
    if len(matches) > 1:
        return {"status": "parent_ambiguous", "at": path}
    row = matches[0]
    if not row["is_dir"]:
        return {"status": "parent_not_directory", "at": path, "id": row["id"]}
    if row["id"] != expected_id:
        return {"status": "parent_id_mismatch", "at": path, "id": row["id"]}
    return {"status": "ok", "id": row["id"]}


def _single_editor(tab, wait: dict):
    """The one inline name editor that appears after 新建文件夹; more than one is refused."""
    deadline = time.monotonic() + wait["timeout"]
    while True:
        boxes = tab.eles(EDIT_BOX, timeout=0) or []
        if len(boxes) > 1:
            raise LookupError("duplicate_editor")
        if boxes:
            if CREATE_EDITOR_CLASS not in str(boxes[0].attr("class") or "").split():
                raise LookupError("unexpected_editor")
            return boxes[0]
        if time.monotonic() >= deadline:
            raise LookupError("no_editor")
        time.sleep(wait["poll"])


def _create(tab, parent: str, name: str, parent_id: str | None, pace, wait) -> dict:
    """Create ``parent/name`` once; never re-submits after Enter."""
    check = _verify_folder(tab, parent, parent_id, pace, wait)
    if check["status"] != "ok":
        return check
    try:
        entries = _paced_open(tab, parent, pace, wait)
    except ListingError as exc:
        return {"status": "unavailable", "reason": exc.reason, "at": parent}
    same = [e for e in entries if e["name"] == name]
    if len(same) == 1 and same[0]["is_dir"]:
        return {"status": "already_exists", "id": same[0]["id"]}
    if any(not e["is_dir"] for e in same):
        return {"status": "target_is_file", "id": next(e["id"] for e in same if not e["is_dir"])}
    if same or any(e["name"].casefold() == name.casefold() for e in entries):
        return {"status": "name_conflict"}

    stage, clicked = "stale_editor", False
    try:
        if tab.eles(EDIT_BOX, timeout=0):
            raise LookupError
        stage = "parent_changed"
        if url_path(tab.url) != parent:
            raise LookupError
        stage = "create_button"
        buttons = tab.eles(CREATE_BUTTON, timeout=0) or []
        if len(buttons) != 1:
            raise LookupError
        pace()
        clicked = True
        buttons[0].click()
        stage = "editor"
        box = _single_editor(tab, wait)
        stage = "parent_changed"
        if url_path(tab.url) != parent:
            raise LookupError
        stage = "input"
        pace()
        box.input(name, clear=True)
        stage = "name_roundtrip"
        if box.value != name:  # verbatim: no strip/normalisation of the typed name
            raise LookupError
        stage = "duplicate_editor"
        if len(tab.eles(EDIT_BOX, timeout=0) or []) != 1:
            raise LookupError
        stage = "parent_changed"
        if url_path(tab.url) != parent:
            raise LookupError
    except Exception as exc:  # noqa: BLE001 - nothing submitted yet
        # the editor may still be open: list the parent before any new attempt
        return {"status": "not_submitted", "reason": stage, "error": type(exc).__name__,
                "editor_opened": clicked}

    from DrissionPage.common import Keys

    pace()
    try:
        box.input(Keys.ENTER)
    except Exception as exc:  # noqa: BLE001 - the key may have landed; never retry
        return {"status": "ambiguous", "reason": "submit_error", "error": type(exc).__name__}

    def created(rows: list[dict]) -> dict | None:
        hits = [e for e in rows if e["name"] == name]
        return hits[0] if len(hits) == 1 and hits[0]["is_dir"] else None

    def shown() -> bool:
        try:
            return url_path(tab.url) == parent and created(_snapshot(tab, parent)) is not None
        except ListingError:  # the editor may still replace the title cell
            return False

    try:
        deadline = time.monotonic() + wait["timeout"]
        while not shown():
            if time.monotonic() >= deadline:
                return {"status": "ambiguous", "reason": "readback_unconfirmed"}
            time.sleep(wait["poll"])
        row = created(_paced_open(tab, parent, pace, wait))
    except Exception as exc:  # noqa: BLE001 - submitted; report, never retry
        return {"status": "ambiguous", "reason": "readback_error", "error": type(exc).__name__}
    if row is None:
        return {"status": "ambiguous", "reason": "reload_unconfirmed"}
    return {"status": "created", "id": row["id"]}


def _close_own_tab(browser, tab) -> str:
    """Close only the tab this call opened; a failure is reported, never raised."""
    try:
        tab.close()
    except Exception as exc:  # noqa: BLE001 - must not mask the operation's outcome
        return f"failed:{type(exc).__name__}"
    try:
        return "still_open" if tab.tab_id in set(browser.tab_ids) else "closed"
    except Exception:  # noqa: BLE001
        return "unverified"


def _run(base: dict, context, body: Callable[[object], dict]) -> dict:
    """Run ``body`` in one tab this call opens and alone closes, under the owned lock.

    Context contract (``web.owned_context`` or an injected one): it yields an
    object whose ``browser`` offers ``tab_ids`` and ``new_tab()``; that tab has
    ``tab_id`` and ``close()``.  Tabs that existed before (the homepage, a
    caller's tab) are never navigated or closed, and there is no ``main_tab``
    fallback.  ``tab_cleanup`` reports ``closed``/``still_open``/``failed:*``."""
    try:
        with (context or web.owned_context)() as owned:
            browser = getattr(owned, "browser", None)
            if browser is None:
                return {**base, "status": "blocked", "reason": "no_dedicated_tab"}
            try:
                before = set(browser.tab_ids)
                tab = browser.new_tab()
                tab_id = tab.tab_id
            except Exception as exc:  # noqa: BLE001 - nothing was done in any tab
                return {**base, "status": "blocked", "reason": "new_tab_failed", "error": type(exc).__name__}
            if not tab_id or tab_id in before:  # not provably ours: never use or close it
                return {**base, "status": "blocked", "reason": "new_tab_not_distinct"}
            cleanup = "not_run"
            try:
                result = body(tab)
            except Exception as exc:  # noqa: BLE001 - only pre-Enter steps can raise here
                result = {"status": "unavailable", "reason": "browser_error", "error": type(exc).__name__}
            finally:
                cleanup = _close_own_tab(browser, tab)
            return {**base, **result, "tab_cleanup": cleanup}
    except web.OwnershipError as exc:
        return {**base, "status": "blocked", "reason": exc.reason}
    except web.WebError:
        return {**base, "status": "blocked", "reason": "browser_lock_busy"}


def create_directory(parent: str, new_name: str, expected_parent_id: str | None = None, *,
                     context: Callable[[], object] | None = None,
                     timeout: float = 20.0, poll: float = 0.3, settle: int = 3,
                     sleep: Callable[[float], None] = time.sleep) -> dict:
    """Create folder ``new_name`` directly in ``parent``, submitting at most once.

    A non-root ``parent`` needs ``expected_parent_id``: its row in a fresh listing
    of its own parent must be a folder with that id.  Statuses: ``created``,
    ``already_exists`` (exact folder already there, with ``id``), ``invalid``,
    ``blocked``, ``unavailable``, ``parent_missing``, ``parent_ambiguous``,
    ``parent_not_directory``, ``parent_id_mismatch``, ``target_is_file``,
    ``name_conflict`` (case variant or duplicate rows), ``not_submitted`` (UI step
    failed before Enter; ``editor_opened`` tells whether an editor may remain), and
    ``ambiguous`` (Enter pressed but not verified: list the parent later, never retry).
    Creating a folder says nothing about any copy into it."""
    try:
        if validate_remote_dir(parent) != parent:  # no silent strip / '//' collapse
            raise ValueError
        new_name = validate_file_name(new_name)
    except ValueError:
        return {"status": "invalid", "reason": "invalid_path_or_name"}
    if parent == "/" and expected_parent_id is not None:
        return {"status": "invalid", "reason": "root_has_no_id"}
    if parent != "/" and (expected_parent_id is None or not _ID_RE.match(expected_parent_id)):
        return {"status": "invalid", "reason": "expected_parent_id_required"}
    wait = {"timeout": timeout, "poll": poll, "settle": settle}
    pace = lambda: sleep(ACTION_GAP)  # noqa: E731
    base = {"parent": parent, "path": _join(parent, new_name)}
    return _run(base, context, lambda tab: _create(tab, parent, new_name, expected_parent_id, pace, wait))


def ensure_directory(path: str, anchor_path: str, expected_anchor_id: str | None = None, *,
                     context: Callable[[], object] | None = None,
                     timeout: float = 20.0, poll: float = 0.3, settle: int = 3,
                     sleep: Callable[[float], None] = time.sleep) -> dict:
    """Make every level of ``path`` below the existing folder ``anchor_path`` exist.

    ``anchor_path`` must be a folder row with ``expected_anchor_id`` in a fresh
    listing of its parent (the drive root ``/`` takes no id); ``path`` must stay
    inside it.  Levels are walked in one owned browser session, each created
    with :func:`create_directory` semantics; the walk halts at the first
    non-success and never replays an ``ambiguous`` level.  Statuses: ``created``
    (at least one level made), ``already_exists``, else the failing level's status
    with ``at``.  ``steps`` lists each level's ``{path, status, id}``."""
    try:
        anchor = anchor_path
        if validate_remote_dir(path) != path or validate_remote_dir(anchor) != anchor:
            raise ValueError  # no silent strip / '//' collapse of names to create
    except ValueError:
        return {"status": "invalid", "reason": "invalid_path"}
    if anchor == "/" and expected_anchor_id is not None:
        return {"status": "invalid", "reason": "root_has_no_id"}
    if anchor != "/" and (expected_anchor_id is None or not _ID_RE.match(expected_anchor_id)):
        return {"status": "invalid", "reason": "expected_anchor_id_required"}
    prefix = anchor.rstrip("/") + "/"
    if path != anchor and not path.startswith(prefix):
        return {"status": "invalid", "reason": "outside_anchor"}
    names = [] if path == anchor else path[len(prefix):].split("/")
    try:
        for name in names:
            validate_file_name(name)
    except ValueError:
        return {"status": "invalid", "reason": "invalid_path"}
    wait = {"timeout": timeout, "poll": poll, "settle": settle}
    pace = lambda: sleep(ACTION_GAP)  # noqa: E731

    def walk(tab) -> dict:
        check = _verify_folder(tab, anchor, expected_anchor_id, pace, wait)
        if check["status"] != "ok":
            return {**check, "steps": []}
        current, current_id, steps = anchor, check["id"], []
        for name in names:
            try:
                result = _create(tab, current, name, current_id, pace, wait)
            except Exception as exc:  # noqa: BLE001 - pre-Enter fault; keep the levels already done
                result = {"status": "unavailable", "reason": "browser_error", "error": type(exc).__name__}
            current = _join(current, name)
            if result["status"] not in {"created", "already_exists"}:
                return {"at": current, **result, "steps": steps}
            current_id = result["id"]
            steps.append({"path": current, "status": result["status"], "id": current_id})
        made = any(s["status"] == "created" for s in steps)
        return {"status": "created" if made else "already_exists", "id": current_id, "steps": steps}

    return _run({"path": path, "anchor": anchor}, context, walk)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List/rename/create Baidu Netdisk folders via the official web UI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ls", help="list one remote folder")
    p.add_argument("path")
    p = sub.add_parser("rename", help="rename one remote folder in place (submits at most once)")
    p.add_argument("path")
    p.add_argument("new_name")
    p.add_argument("--expected-id", help="row data-id; required to confirm an already completed rename")
    p = sub.add_parser("mkdir", help="create one folder directly in PARENT (submits at most once)")
    p.add_argument("parent")
    p.add_argument("new_name")
    p.add_argument("--expected-parent-id", help="PARENT row data-id; required unless PARENT is /")
    p = sub.add_parser("ensure", help="create missing levels of PATH below an existing anchor folder")
    p.add_argument("path")
    p.add_argument("--anchor", required=True, help="existing folder that PATH must stay inside")
    p.add_argument("--anchor-id", help="anchor row data-id; required unless the anchor is /")
    args = parser.parse_args(argv)
    if args.cmd == "ls":
        result = list_directory(args.path)
        ok = result["status"] == "listed"
    elif args.cmd == "rename":
        result = rename_directory(args.path, args.new_name, args.expected_id)
        ok = result["status"] in {"renamed", "already_renamed"}
    elif args.cmd == "mkdir":
        result = create_directory(args.parent, args.new_name, args.expected_parent_id)
        ok = result["status"] in {"created", "already_exists"}
    else:
        result = ensure_directory(args.path, args.anchor, args.anchor_id)
        ok = result["status"] in {"created", "already_exists"}
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 3 if result["status"] == "ambiguous" else 2


if __name__ == "__main__":
    raise SystemExit(main())
