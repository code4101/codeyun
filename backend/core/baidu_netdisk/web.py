"""Isolated DrissionPage browser for the official Baidu Netdisk web UI.

CodeYun owns one dedicated Chrome process here: a persistent profile under
``F:/CodeYun/browser-profiles/baidu-netdisk``, a loopback debugging port picked
from 9331 upward, and a state file recording its PID, creation time and
profile.  A port is only ever attached to after that identity verifies; any
other listener is foreign and is neither attached to nor closed.  The shared
attendance runtime (``dp_browser.py``, ports 9222-9232) is never touched.

Ownership is persisted as soon as the launched process is identified, before
any navigation, so page or CDP failures never leave an unclaimable profile.

``status`` reports only ``login_required`` / ``authenticated`` / ``unknown``
plus process facts; account names, file lists, HTML, QR tokens, cookies and
URL query/fragment parts are never returned.

Business adapters (``web_share``) use ``owned_context``: it only attaches to
an already-running, verified own browser under the same lock and never
launches or stops one.

CLI::

    uv run python -m backend.core.baidu_netdisk.web prepare [--visible]
    uv run python -m backend.core.baidu_netdisk.web status
    uv run python -m backend.core.baidu_netdisk.web stop
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlsplit

from filelock import FileLock, Timeout as FileLockTimeout

PROFILE_DIR = Path("F:/CodeYun/browser-profiles/baidu-netdisk")
STATE_FILE = Path("F:/CodeYun/materials/m2610skilldependency/cloud/browser-state.json")
PORTS = range(9331, 9341)
RESERVED_PORTS = range(9222, 9233)  # shared attendance DP runtime
HOST = "127.0.0.1"
MAIN_URL = "https://pan.baidu.com/disk/main"
OFFICIAL_HOSTS = ("pan.baidu.com", "passport.baidu.com")
CREATE_TIME_TOLERANCE = 0.01  # project PID-reuse tolerance (seconds)
CHROME_CANDIDATES = (
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
)

# Explicit login UI: pan.baidu.com/login "去登录" entry, passport form or QR panel.
LOGIN_SELECTORS = (
    "css:.bd-login-content__login",
    "css:[id^='TANGRAM__PSP_'][id$='__form']",
    "css:[id^='TANGRAM__PSP_'][id$='__QrcodeMain']",
    "css:.tang-pass-login",
    "css:.tang-pass-qrcode",
    "css:.pass-login-pop",
)
# Logged-in workspace chrome (header user area / file list container).
AUTH_SELECTORS = (
    "css:.wp-s-header-user",
    "css:.wp-s-core-pan",
    "css:.nd-main-list",
    "css:.wp-s-pan-table",
)


@dataclass
class Paths:
    profile: Path = PROFILE_DIR
    state: Path = STATE_FILE

    @property
    def lock(self) -> Path:
        return self.state.with_name(self.state.name + ".lock")


class WebError(RuntimeError):
    pass


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# ----- process identity -----------------------------------------------------

def _norm_path(value: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(value).strip('"')))


def _cmd_value(cmdline: list[str], flag: str) -> str | None:
    for arg in cmdline:
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return None


def process_identity(pid: int) -> dict | None:
    """Return ``{pid, create_time, profile, port}`` for a live browser main process."""
    import psutil

    try:
        proc = psutil.Process(pid)
        cmdline = proc.cmdline()
        create_time = proc.create_time()
    except (psutil.Error, OSError):
        return None
    if _cmd_value(cmdline, "--type"):
        return None  # renderer/gpu child, not the browser main process
    profile = _cmd_value(cmdline, "--user-data-dir")
    port = _cmd_value(cmdline, "--remote-debugging-port")
    return {
        "pid": pid,
        "create_time": create_time,
        "profile": _norm_path(profile) if profile else None,
        "port": int(port) if port and port.isdigit() else None,
    }


def find_profile_browsers(profile: Path) -> list[dict]:
    """All live browser main processes using *profile* (whatever their port)."""
    import psutil

    want = _norm_path(profile)
    found = []
    for proc in psutil.process_iter(["pid", "name"]):
        if "chrome" not in (proc.info.get("name") or "").lower():
            continue
        ident = process_identity(proc.info["pid"])
        if ident and ident["profile"] == want:
            found.append(ident)
    return found


def port_listening(port: int, host: str = HOST) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def verify_owned(state: dict | None, paths: Paths,
                 identify: Callable[[int], dict | None] | None = None) -> dict | None:
    """Return the live identity iff *state* still describes our own browser."""
    identify = identify or process_identity
    if not state or not state.get("pid") or not state.get("port"):
        return None
    ident = identify(int(state["pid"]))
    if not ident:
        return None
    if abs(ident["create_time"] - float(state.get("create_time") or 0)) >= CREATE_TIME_TOLERANCE:
        return None  # PID reused by another process
    if ident["profile"] != _norm_path(paths.profile) or ident["port"] != int(state["port"]):
        return None
    return ident


def _terminate_tree(pid: int) -> None:
    import psutil

    try:
        proc = psutil.Process(pid)
        procs = [*proc.children(recursive=True), proc]
    except psutil.Error:
        return
    for item in procs:
        try:
            item.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=5)
    for item in alive:
        try:
            item.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(alive, timeout=5)


# ----- state + lock ----------------------------------------------------------

def read_state(paths: Paths) -> dict | None:
    try:
        return json.loads(paths.state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_state(paths: Paths, state: dict) -> None:
    paths.state.parent.mkdir(parents=True, exist_ok=True)
    tmp = paths.state.with_name(paths.state.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, paths.state)


@contextmanager
def source_lock(paths: Paths, timeout: float = 30.0) -> Iterator[None]:
    """Cross-process exclusive lock. The OS drops it when the holder exits, so an
    empty or leftover lock file never blocks later callers."""
    paths.lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock = FileLock(str(paths.lock), timeout=timeout)
        lock.acquire()
    except FileLockTimeout:
        raise WebError(f"browser lock busy: {paths.lock}") from None
    try:
        yield
    finally:
        lock.release()


# ----- auth classification ---------------------------------------------------

def official_host(url: str) -> str | None:
    """Exact official https host of *url*, else None (no suffix/substring matching)."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    return host if parts.scheme == "https" and host in OFFICIAL_HOSTS else None


def public_page(url: str) -> str | None:
    """``scheme://host/path`` only: query and fragment may carry tokens or share codes."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if not parts.scheme or not host:
        return None
    return f"{parts.scheme}://{host}{parts.path}"


def _on_disk_page(url: str) -> bool:
    return official_host(url) == "pan.baidu.com" and urlsplit(url).path.startswith("/disk/")


def classify(url: str, login_ui: bool, auth_ui: bool) -> str:
    """Map observed page facts to an auth state. Only explicit login UI is required."""
    if not official_host(url):
        return "unknown"
    if login_ui:
        return "login_required"
    if auth_ui and _on_disk_page(url):
        return "authenticated"
    return "unknown"


def _probe_tab(tab, wait: float) -> dict:
    deadline = time.monotonic() + wait
    login_hit = auth_hit = None
    while True:
        login_hit = next((s for s in LOGIN_SELECTORS if tab.ele(s, timeout=0)), None)
        auth_hit = next((s for s in AUTH_SELECTORS if tab.ele(s, timeout=0)), None)
        if login_hit or auth_hit or time.monotonic() > deadline:
            break
        time.sleep(0.5)
    url = str(tab.url or "")
    return {
        "auth": classify(url, bool(login_hit), bool(auth_hit)),
        "page": public_page(url),
        "evidence": login_hit or auth_hit,  # selector name only, no content
    }


# ----- browser control -------------------------------------------------------

def _chrome_path() -> str:
    for path in CHROME_CANDIDATES:
        if path.exists():
            return str(path)
    raise WebError("Chrome executable not found")


def _options(port: int, paths: Paths, *, visible: bool, attach: bool):
    """Attach options must repeat the running mode: DrissionPage quits and
    relaunches an existing browser whose headless flag differs."""
    from DrissionPage import ChromiumOptions

    opts = ChromiumOptions(read_file=False)
    opts.set_address(f"{HOST}:{port}")
    opts.set_browser_path(_chrome_path())
    opts.set_user_data_path(str(paths.profile))
    opts.headless(not visible)
    opts.set_argument("--no-first-run")
    opts.set_argument("--no-default-browser-check")
    if attach:
        opts.existing_only(True)
    return opts


def _launch(port: int, paths: Paths, *, visible: bool, timeout: float = 20.0) -> int:
    """Start Chrome detached so it outlives this CLI; return the browser main PID."""
    import subprocess

    opts = _options(port, paths, visible=visible, attach=False)
    args = [opts.browser_path, f"--remote-debugging-port={port}", *opts.arguments]
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        proc = subprocess.Popen(args, creationflags=flags | 0x01000000,  # CREATE_BREAKAWAY_FROM_JOB
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:  # job forbids breakaway
        proc = subprocess.Popen(args, creationflags=flags,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + timeout
    while not port_listening(port):
        if proc.poll() is not None or time.monotonic() > deadline:
            if proc.poll() is None:  # still our own child: PID cannot have been reused
                _terminate_tree(proc.pid)
            raise WebError(f"browser did not open port {port}")
        time.sleep(0.3)
    return proc.pid


def _pick_port(ports=PORTS, listening: Callable[[int], bool] | None = None) -> int:
    listening = listening or port_listening
    for port in ports:
        if port in RESERVED_PORTS:
            continue
        if not listening(port):
            return port
    raise WebError(f"no free port in {ports.start}-{ports.stop - 1}")


def _attach(state: dict, paths: Paths):
    from DrissionPage import Chromium

    # Always repeat the recorded mode so DrissionPage never relaunches our browser.
    return Chromium(_options(int(state["port"]), paths, visible=bool(state.get("visible")), attach=True))


def _main_tab(browser):
    for tab_id in browser.tab_ids:
        tab = browser.get_tab(tab_id)
        if official_host(str(tab.url or "")):
            return tab
    tab = browser.latest_tab
    tab.get(MAIN_URL)
    return tab


def _probe_owned(state: dict, paths: Paths, wait: float, *, navigate: bool) -> None:
    """Attach, optionally open the main page, and record the auth probe.

    Page/CDP faults keep the managed identity (status/stop still work) and are
    recorded as ``auth=unknown`` with the exception class name only."""
    try:
        tab = _main_tab(_attach(state, paths))
        if navigate and not _on_disk_page(str(tab.url or "")):
            tab.get(MAIN_URL)
        state.update(_probe_tab(tab, wait), probe_error=None)
    except Exception as exc:  # noqa: BLE001 - a page fault must not lose process ownership
        state.update(auth="unknown", page=None, evidence=None, probe_error=type(exc).__name__)
    state["checked_at"] = _now()
    write_state(paths, state)


def prepare_browser(*, visible: bool = False, paths: Paths | None = None, wait: float = 15.0) -> dict:
    """Start (or reuse) our own browser, open the official main page, report auth."""
    paths = paths or Paths()
    with source_lock(paths):
        state = read_state(paths)
        owned = verify_owned(state, paths)
        if owned and bool(state.get("visible")) != visible:
            if not _stop_owned(state, paths):
                raise WebError(f"own browser pid {state['pid']} did not stop; not relaunching")
            owned = None
        if not owned:
            others = find_profile_browsers(paths.profile)
            if others:
                raise WebError(f"profile already used by unverified browser pid(s) "
                               f"{[o['pid'] for o in others]}; not taking over")
            port = _pick_port()
            paths.profile.mkdir(parents=True, exist_ok=True)
            pid = _launch(port, paths, visible=visible)
            ident = process_identity(pid)
            if not ident or ident["profile"] != _norm_path(paths.profile) or ident["port"] != port:
                if ident:  # alive main process of the child we just spawned
                    _terminate_tree(pid)
                raise WebError(f"launched browser identity mismatch on port {port}")
            # Persist ownership before any CDP/page work so a fault never orphans the profile.
            state = {
                "pid": ident["pid"], "create_time": ident["create_time"], "port": port,
                "address": f"{HOST}:{port}", "profile": str(paths.profile),
                "visible": visible, "started_at": _now(), "auth": "unknown",
            }
            write_state(paths, state)
            try:
                attached_pid = int(_attach(state, paths).process_id or 0)
            except Exception as exc:  # noqa: BLE001 - state is saved; status/stop can recover
                state.update(probe_error=type(exc).__name__, checked_at=_now())
                write_state(paths, state)
                raise WebError(f"attach to own browser on port {port} failed: {type(exc).__name__}") from None
            if attached_pid != pid:
                _stop_owned(state, paths)
                raise WebError(f"port {port} answered by pid {attached_pid}, expected {pid}")
        _probe_owned(state, paths, wait, navigate=True)
        return _public(state, running=True)


def status(*, paths: Paths | None = None, wait: float = 5.0) -> dict:
    paths = paths or Paths()
    with source_lock(paths):
        state = read_state(paths)
        if not verify_owned(state, paths):
            return _public(state or {}, running=False)
        _probe_owned(state, paths, wait, navigate=False)
        return _public(state, running=True)


def stop(*, paths: Paths | None = None) -> dict:
    """Stop only our verified browser; ``running``/``stopped`` are re-read from the live process."""
    paths = paths or Paths()
    with source_lock(paths):
        state = read_state(paths)
        was_owned = verify_owned(state, paths) is not None
        if was_owned:
            _stop_owned(state, paths)
        running = verify_owned(state, paths) is not None
        if state and not running:
            state.update(stopped_at=_now(), auth="unknown")
            write_state(paths, state)
        return {**_public(state or {}, running=running), "stopped": was_owned and not running}


def _stop_owned(state: dict, paths: Paths) -> bool:
    """Quit our verified browser, escalating to its process tree; True iff it is gone."""
    try:
        _attach(state, paths).quit(timeout=5)
    except Exception:  # noqa: BLE001 - fall through to verified termination
        pass
    if verify_owned(state, paths):  # quit did not finish: terminate verified tree only
        _terminate_tree(int(state["pid"]))
    return verify_owned(state, paths) is None


def _public(state: dict, *, running: bool) -> dict:
    keys = ("pid", "create_time", "port", "address", "profile", "visible", "page", "evidence",
            "probe_error", "checked_at")
    out = {k: state.get(k) for k in keys if k in state}
    out["running"] = running
    out["auth"] = state.get("auth", "unknown") if running else "unknown"
    return out


# ----- owned browser context (for business adapters) --------------------------

class OwnershipError(WebError):
    """Refusal to attach; ``reason`` is ``not_owned``, ``foreign_port`` or ``attach_failed``."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@dataclass
class OwnedBrowser:
    facts: dict  # _public() process facts, no page content
    browser: object

    def main_tab(self):
        return _main_tab(self.browser)


@contextmanager
def owned_context(*, paths: Paths | None = None, identify: Callable[[int], dict | None] | None = None,
                  attach: Callable[[dict, Paths], object] | None = None,
                  lock_timeout: float = 30.0) -> Iterator[OwnedBrowser]:
    """Hold the browser lock and yield our own already-running browser.

    Never launches, relaunches or stops anything.  The recorded PID, creation
    time, profile and port must verify before attaching, and the attached CDP
    endpoint must report that same PID; otherwise ``OwnershipError`` is raised
    and nothing on the port is touched further."""
    paths = paths or Paths()
    attach = attach or _attach
    with source_lock(paths, timeout=lock_timeout):
        state = read_state(paths)
        if not verify_owned(state, paths, identify):
            raise OwnershipError("not_owned", "no verified own browser; run prepare first")
        try:
            browser = attach(state, paths)
            attached_pid = int(getattr(browser, "process_id", 0) or 0)
        except Exception as exc:  # noqa: BLE001 - report class only
            raise OwnershipError("attach_failed", type(exc).__name__) from None
        if attached_pid != int(state["pid"]):
            raise OwnershipError("foreign_port", f"port {state['port']} answered by another process")
        yield OwnedBrowser(_public(state, running=True), browser)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated Baidu Netdisk web browser (DrissionPage)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="start/reuse own browser and open pan.baidu.com/disk/main")
    p.add_argument("--visible", action="store_true", help="visible window for one-time QR login")
    p.add_argument("--wait", type=float, default=15.0)
    sub.add_parser("status", help="report auth state and process facts")
    sub.add_parser("stop", help="close only our own verified browser")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "prepare":
            result = prepare_browser(visible=args.visible, wait=args.wait)
        elif args.cmd == "status":
            result = status()
        else:
            result = stop()
    except WebError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # Non-zero when the recorded browser is still alive after stop, so callers never trust a false stop.
    return 3 if args.cmd == "stop" and result.get("running") else 0


if __name__ == "__main__":
    raise SystemExit(main())
