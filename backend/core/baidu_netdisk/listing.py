"""Complete, paged Baidu Netdisk directory listings and exact object lookup.

Runs only inside ``web.owned_context(lock_timeout=0)`` (own verified browser,
lock not waited for) and only in one tab this call opens and alone closes;
pre-existing tabs are never navigated or closed.  The session's own request
parameters are learned by listening for the page's real ``/api/list`` request
after a public ``web_directories.route`` navigation; follow-up pages are read
with a same-origin browser ``fetch`` that changes only ``dir``/``page``/``num``
(no cookie/token reads, no raw HTTP, no hard-coded app parameters).

A listing is ``listed`` only when two full scans, each paged to an empty
terminal page, agree on ids, paths, types, sizes and mtimes.  A non-root
directory's identity comes from its parent's complete listing (exact path and
name, ``expected_id`` when given) and is re-checked after listing it.  Every
navigation and page read is preceded by ``pace()`` (default ``ACTION_GAP``).
Nothing is retried.  ``cleanup`` reports the listener stop and own-tab close;
a cleanup failure turns ``listed``/``resolved`` into ``cleanup_failed``.

CLI (real account browser; run by the acceptance owner only)::

    uv run python -m backend.core.baidu_netdisk.listing ls PATH [--expected-id ID]
    uv run python -m backend.core.baidu_netdisk.listing resolve PATH [--expected-id ID]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Callable
from urllib.parse import urlsplit

from ..temp_paths import codeyun_temp_root
from . import web, web_directories
from .listing_parse import (API_HOST, API_PATH, compare_scans, join, page_url, prove_child,
                            scan_listing, validate_seed)
from .local_files import validate_remote_dir

__all__ = ["scan_listing", "list_directory", "resolve_remote"]

FETCH_JS = ('return fetch(arguments[0],{credentials:"same-origin"})'
            '.then(async r=>({status:r.status,text:await r.text()}));')
SEED_TIMEOUT = 20.0


class _Stop(Exception):
    def __init__(self, result: dict):
        super().__init__(result.get("reason", ""))
        self.result = result


def _default_context():
    return web.owned_context(lock_timeout=0)


def _default_pace() -> None:
    time.sleep(web_directories.ACTION_GAP)


class _Listener:
    """``tab.listen`` started at most once and stopped at most once (DP stop is not idempotent)."""

    def __init__(self, tab):
        self.tab, self.state = tab, "not_started"

    def start(self) -> None:
        self.state = "starting"
        self.tab.listen.start(targets=API_HOST + API_PATH)
        self.state = "started"

    def stop_once(self) -> str:
        if self.state in ("starting", "started"):
            try:
                self.tab.listen.stop()
                self.state = "stopped"
            except Exception as exc:  # noqa: BLE001 - reported in cleanup, never retried
                self.state = f"stop_failed:{type(exc).__name__}"
        return self.state


def _write_record(path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _close_and_verify(browser, tab, created: str, before: list[str]) -> dict:
    out = {"tab": "close_not_run", "before_preserved": None}
    try:
        tab.close()
    except Exception as exc:  # noqa: BLE001
        out["tab"] = f"close_failed:{type(exc).__name__}"
        return out
    try:
        after = set(browser.tab_ids)
    except Exception:  # noqa: BLE001
        out["tab"] = "unverified"
        return out
    out["tab"] = "still_open" if created in after else "closed"
    out["before_preserved"] = set(before) <= after
    return out


def _in_own_tab(base: dict, context, body: Callable[[object, _Listener], dict]) -> dict:
    try:
        with (context or _default_context)() as owned:
            browser = getattr(owned, "browser", None)
            if browser is None:
                return {**base, "status": "blocked", "reason": "no_dedicated_tab"}
            try:
                before = list(browser.tab_ids)
                tab = browser.new_tab()
                created = tab.tab_id
            except Exception as exc:  # noqa: BLE001 - nothing done in any tab
                return {**base, "status": "blocked", "reason": "new_tab_failed", "error": type(exc).__name__}
            if not created or created in before:  # not provably ours: never use or close it
                return {**base, "status": "blocked", "reason": "new_tab_not_distinct"}
            record = codeyun_temp_root("baidu-netdisk", "listing-tabs") / f"{os.getpid()}-{time.time_ns()}.json"
            facts = {"pid": os.getpid(), "before_ids": before, "created_id": created}
            listener = _Listener(tab)
            cleanup: dict = {}
            try:
                try:
                    _write_record(record, {**facts, "phase": "open"})
                except OSError:
                    raise _Stop({"status": "blocked", "reason": "record_write_failed"}) from None
                result = body(tab, listener)
            except _Stop as stop:
                result = stop.result
            except Exception as exc:  # noqa: BLE001 - reported, never retried
                result = {"status": "unavailable", "reason": "browser_error", "error": type(exc).__name__}
            finally:
                try:
                    cleanup["listener"] = listener.stop_once()
                finally:
                    cleanup.update(_close_and_verify(browser, tab, created, before))
                    try:
                        _write_record(record, {**facts, "phase": "cleaned", "cleanup": cleanup})
                        cleanup["record"] = str(record)
                    except OSError:
                        cleanup["record"] = "write_failed"
            ok = (cleanup["listener"] in ("not_started", "stopped") and cleanup["tab"] == "closed"
                  and cleanup["before_preserved"] is True)
            if not ok and result.get("status") in ("listed", "resolved"):
                result = {**result, "status": "cleanup_failed", "data_status": result["status"]}
            return {**base, **result, "cleanup": cleanup}
    except web.OwnershipError as exc:
        return {**base, "status": "blocked", "reason": exc.reason}
    except web.WebError:
        return {**base, "status": "blocked", "reason": "browser_lock_busy"}


class _Reader:
    """Seeded page reader inside the own tab; every navigation/read is paced."""

    def __init__(self, tab, listener: _Listener, pace: Callable[[], None], page_size: int,
                 max_pages: int, seed_timeout: float):
        self.tab, self.listener, self.pace = tab, listener, pace
        self.page_size, self.max_pages, self.seed_timeout = page_size, max_pages, seed_timeout
        self.seed: str | None = None

    def capture_seed(self, nav_path: str) -> None:
        self.listener.start()
        self.pace()
        self.tab.get(web_directories.route(nav_path))
        packet = self.tab.listen.wait(timeout=self.seed_timeout, raise_err=False)
        self.listener.stop_once()
        seed = validate_seed(getattr(packet, "url", None) if packet else None)
        if seed is None:
            raise _Stop({"status": "unavailable", "reason": "seed_not_captured" if not packet else "seed_invalid"})
        if urlsplit(str(self.tab.url or "")).hostname != API_HOST:
            raise _Stop({"status": "unavailable", "reason": "tab_off_origin"})
        self.seed = seed

    def _fetch(self, dir_path: str) -> Callable[[int, int], object]:
        def fetch(page: int, num: int) -> object:
            self.pace()
            return self.tab.run_js(FETCH_JS, page_url(self.seed, dir_path, page, num))
        return fetch

    def scan(self, dir_path: str) -> dict:
        return scan_listing(self._fetch(dir_path), dir_path, self.page_size, self.max_pages)

    def stable(self, dir_path: str) -> dict:
        first = self.scan(dir_path)
        if first.get("status") != "listed":
            return first
        return compare_scans(first, self.scan(dir_path))


def _parent(path: str) -> str:
    return path.rpartition("/")[0] or "/"


def _identity(entry: dict) -> tuple:
    return entry["id"], entry["path"], entry["is_dir"]


def _check_args(path: str, page_size: int, max_pages: int) -> str | None:
    try:
        clean = validate_remote_dir(path)
    except ValueError:
        return None
    if type(page_size) is not int or not 1 <= page_size <= 1000 or type(max_pages) is not int or max_pages < 1:
        return None
    return clean


def list_directory(path: str, expected_id: str | None = None, page_size: int = 100,
                   max_pages: int = 100, context: Callable[[], object] | None = None,
                   pace: Callable[[], None] | None = None, *, seed_timeout: float = SEED_TIMEOUT) -> dict:
    """``{status: listed, path, count, entries[id,name,path,is_dir,size,version], complete,
    complete_basis, snapshot_sha256, observed_at, cleanup}`` plus ``is_root``/``id``.

    Non-root: parent complete listing proves the folder row (``expected_id`` must
    match when given), the folder is scanned twice, then the parent is re-listed
    and must still show the same id/path/type."""
    clean = _check_args(path, page_size, max_pages)
    if clean is None:
        return {"status": "invalid", "reason": "invalid_arguments", "path": path}
    if clean == "/" and expected_id is not None:
        return {"status": "invalid", "reason": "root_has_no_id", "path": "/"}
    base = {"path": clean}

    def body(tab, listener) -> dict:
        reader = _Reader(tab, listener, pace or _default_pace, page_size, max_pages, seed_timeout)
        reader.capture_seed(clean)
        if clean == "/":
            return {**reader.stable("/"), "is_root": True, "id": None}
        parent = _parent(clean)
        proof = prove_child(reader.scan(parent), clean, expected_id, want_dir=True)
        if proof["status"] != "ok":
            return {**proof, "status": proof["status"], "reason": "identity_not_proven"}
        listing = reader.stable(clean)
        if listing.get("status") != "listed":
            return listing
        again = prove_child(reader.scan(parent), clean, proof["entry"]["id"], want_dir=True)
        if again["status"] != "ok" or _identity(again["entry"]) != _identity(proof["entry"]):
            return {"status": "unstable", "reason": "parent_identity_changed", "recheck": again["status"]}
        return {**listing, "is_root": False, "id": proof["entry"]["id"],
                "identity_basis": {"parent": parent, "checks": 2}}

    return _in_own_tab(base, context, body)


def resolve_remote(path: str, expected_id: str | None = None, want_dir: bool | None = None,
                   page_size: int = 100, max_pages: int = 100,
                   context: Callable[[], object] | None = None, pace: Callable[[], None] | None = None,
                   *, seed_timeout: float = SEED_TIMEOUT) -> dict:
    """Exact object at ``path`` via stable (two-scan) parent listings segment by segment.

    ``{status: resolved, path, is_root, id, name, is_dir, size, version, chain[{path,id}]}``;
    the root is ``is_root: true, id: null`` without any account access."""
    clean = _check_args(path, page_size, max_pages)
    if clean is None:
        return {"status": "invalid", "reason": "invalid_arguments", "path": path}
    if clean == "/":
        if expected_id is not None or want_dir is False:
            return {"status": "invalid", "reason": "root_has_no_id", "path": "/"}
        return {"status": "resolved", "path": "/", "is_root": True, "id": None, "is_dir": True, "chain": []}

    def body(tab, listener) -> dict:
        reader = _Reader(tab, listener, pace or _default_pace, page_size, max_pages, seed_timeout)
        reader.capture_seed("/")
        parts = clean.strip("/").split("/")
        chain, current, entry = [], "/", None
        for i, name in enumerate(parts):
            last = i == len(parts) - 1
            target = join(current, name)
            proof = prove_child(reader.stable(current), target, expected_id if last else None,
                                want_dir=want_dir if last else True)
            if proof["status"] != "ok":
                return {**proof, "chain": chain}
            entry = proof["entry"]
            chain.append({"path": target, "id": entry["id"]})
            current = target
        return {"status": "resolved", "is_root": False, **entry, "chain": chain}

    return _in_own_tab({"path": clean}, context, body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Complete Baidu Netdisk listing (own DP tab)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("ls", "resolve"):
        p = sub.add_parser(name)
        p.add_argument("path")
        p.add_argument("--expected-id")
        p.add_argument("--page-size", type=int, default=100)
        p.add_argument("--max-pages", type=int, default=100)
    args = parser.parse_args(argv)
    fn = list_directory if args.cmd == "ls" else resolve_remote
    result = fn(args.path, args.expected_id, page_size=args.page_size, max_pages=args.max_pages)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if result.get("status") in ("listed", "resolved") else 1


if __name__ == "__main__":
    raise SystemExit(main())
