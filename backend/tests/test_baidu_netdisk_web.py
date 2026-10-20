import os
import sys
import types

import pytest

from backend.core.baidu_netdisk import web


@pytest.fixture
def paths(tmp_path):
    return web.Paths(profile=tmp_path / "profile", state=tmp_path / "cloud" / "browser-state.json")


def _ident(paths, pid=4242, port=9331, create_time=1000.0):
    return {"pid": pid, "create_time": create_time, "profile": web._norm_path(paths.profile), "port": port}


def _state(pid=4242, port=9331, create_time=1000.0, visible=False):
    return {"pid": pid, "create_time": create_time, "port": port, "address": f"127.0.0.1:{port}", "visible": visible}


def test_module_imported_from_this_checkout():
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert os.path.normcase(web.__file__).startswith(os.path.normcase(here))


# ----- ports ------------------------------------------------------------------

def test_pick_port_skips_foreign_listeners():
    busy = {9331, 9332}
    assert web._pick_port(listening=lambda p: p in busy) == 9333


def test_pick_port_never_uses_attendance_range():
    assert web._pick_port(range(9222, 9234), listening=lambda p: False) == 9233
    with pytest.raises(web.WebError):
        web._pick_port(range(9222, 9233), listening=lambda p: False)


# ----- identity -----------------------------------------------------------------

def test_verify_owned_accepts_only_matching_identity(paths):
    assert web.verify_owned(_state(), paths, identify=lambda pid: _ident(paths))


@pytest.mark.parametrize("ident", [
    None,                                                     # process gone
    {"create_time": 2000.0},                                  # PID reused
    {"profile": web._norm_path("C:/Users/x/Chrome/User Data")},  # someone else's profile
    {"port": 9332},                                           # different debugging port
])
def test_verify_owned_rejects_foreign(paths, ident):
    live = None if ident is None else {**_ident(paths), **ident}
    assert web.verify_owned(_state(), paths, identify=lambda pid: live) is None


def test_verify_owned_without_state(paths):
    assert web.verify_owned(None, paths, identify=lambda pid: _ident(paths)) is None


# ----- auth classification -------------------------------------------------------

def test_login_required_needs_explicit_login_ui():
    assert web.classify("https://pan.baidu.com/login?redirect=x", True, False) == "login_required"
    # Being on the login URL / main page without visible login UI is not proof.
    assert web.classify("https://pan.baidu.com/login", False, False) == "unknown"
    assert web.classify("https://pan.baidu.com/disk/main", False, False) == "unknown"


def test_authenticated_and_foreign_host():
    assert web.classify("https://pan.baidu.com/disk/main#/index", False, True) == "authenticated"
    assert web.classify("https://evil.example/pan.baidu.com/disk/main", False, True) == "unknown"
    assert web.classify("https://pan.baidu.com.evil.example/disk/main", True, False) == "unknown"


def test_probe_strips_query_and_returns_selector_only():
    class Tab:
        url = "https://pan.baidu.com/login?token=SECRET"

        def ele(self, selector, timeout=0):
            return selector == "css:.bd-login-content__login"

    out = web._probe_tab(Tab(), wait=0)
    assert out == {"auth": "login_required", "page": "https://pan.baidu.com/login",
                   "evidence": "css:.bd-login-content__login"}


# ----- state + lock ----------------------------------------------------------------

def test_state_roundtrip_survives_restart(paths):
    web.write_state(paths, _state())
    assert web.read_state(paths)["pid"] == 4242
    assert not paths.state.with_name(paths.state.name + ".tmp").exists()


def test_source_lock_excludes_live_owner_and_releases(paths):
    import subprocess

    paths.lock.parent.mkdir(parents=True)
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys,time; from filelock import FileLock; "
         "l=FileLock(sys.argv[1]); l.acquire(); print('held', flush=True); time.sleep(30)", str(paths.lock)],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(web.WebError, match="lock busy"):
            with web.source_lock(paths, timeout=0.5):
                pass
    finally:
        holder.kill()
        holder.wait()
    with web.source_lock(paths, timeout=2):  # OS released the dead owner's lock
        pass


@pytest.mark.parametrize("content", ["", '{"pid": 1, "create_time": 1.0}', "garbage"])
def test_source_lock_recovers_empty_or_leftover_file(paths, content):
    paths.lock.parent.mkdir(parents=True)
    paths.lock.write_text(content)
    with web.source_lock(paths, timeout=0.5):
        pass
    with web.source_lock(paths, timeout=0.5):
        pass


def test_status_with_dead_browser_reports_not_running(paths, monkeypatch):
    web.write_state(paths, {**_state(), "auth": "authenticated"})
    monkeypatch.setattr(web, "process_identity", lambda pid: None)
    monkeypatch.setitem(sys.modules, "DrissionPage", types.SimpleNamespace(Chromium=None))
    out = web.status(paths=paths)
    assert out["running"] is False and out["auth"] == "unknown"


def test_stop_never_terminates_unverified_process(paths, monkeypatch):
    web.write_state(paths, _state())
    monkeypatch.setattr(web, "process_identity", lambda pid: {**_ident(paths), "create_time": 9.0})
    monkeypatch.setattr(web, "_stop_owned", lambda *a: pytest.fail("must not stop foreign process"))
    assert web.stop(paths=paths)["stopped"] is False


# ----- prepare -----------------------------------------------------------------------

class FakeBrowser:
    def __init__(self, opts):
        self.opts = opts
        self.process_id = FakeBrowser.pid


def _fake_dp(monkeypatch, pid):
    FakeBrowser.pid = pid
    monkeypatch.setitem(sys.modules, "DrissionPage", types.SimpleNamespace(Chromium=FakeBrowser))
    monkeypatch.setattr(web, "_options", lambda port, paths, visible, attach: (port, visible, attach))
    monkeypatch.setattr(web, "_main_tab", lambda b: types.SimpleNamespace(url="https://pan.baidu.com/disk/main"))
    monkeypatch.setattr(web, "_probe_tab", lambda tab, wait: {"auth": "unknown", "page": tab.url, "evidence": None})


def test_prepare_launches_on_free_port_beside_foreign_listener(paths, monkeypatch):
    _fake_dp(monkeypatch, pid=777)
    monkeypatch.setattr(web, "port_listening", lambda p, host=web.HOST: p == 9331)
    monkeypatch.setattr(web, "find_profile_browsers", lambda profile: [])
    launched = []
    monkeypatch.setattr(web, "_launch", lambda port, p, visible: launched.append((port, visible)) or 777)
    monkeypatch.setattr(web, "process_identity", lambda pid: _ident(paths, pid=777, port=9332) if pid == 777 else None)
    out = web.prepare_browser(paths=paths, wait=0)
    assert launched == [(9332, False)]
    assert out["running"] and out["pid"] == 777 and out["port"] == 9332 and out["visible"] is False
    assert web.read_state(paths)["create_time"] == 1000.0


def test_prepare_reattaches_own_browser_in_same_mode(paths, monkeypatch):
    _fake_dp(monkeypatch, pid=4242)
    web.write_state(paths, _state())
    monkeypatch.setattr(web, "process_identity", lambda pid: _ident(paths))
    monkeypatch.setattr(web, "_launch", lambda *a, **k: pytest.fail("must reuse own browser"))
    seen = []
    monkeypatch.setattr(web, "_options", lambda port, p, visible, attach: seen.append((port, visible, attach)))
    assert web.prepare_browser(paths=paths, wait=0)["pid"] == 4242
    assert seen == [(9331, False, True)]  # headless attach for headless browser


def test_prepare_refuses_profile_held_by_unverified_browser(paths, monkeypatch):
    _fake_dp(monkeypatch, pid=1)
    monkeypatch.setattr(web, "find_profile_browsers", lambda profile: [_ident(paths, pid=555)])
    monkeypatch.setattr(web, "_launch", lambda *a, **k: pytest.fail("must not launch"))
    with pytest.raises(web.WebError, match="not taking over"):
        web.prepare_browser(paths=paths, wait=0)


def test_public_output_has_no_sensitive_fields(paths):
    out = web._public({**_state(), "auth": "authenticated", "cookies": "x", "html": "y", "user": "z"}, running=True)
    assert not {"cookies", "html", "user"} & set(out)


# ----- integration fixes ----------------------------------------------------------------

def test_public_page_strips_query_and_fragment():
    url = "https://PAN.baidu.com/s/1abc?pwd=SECRET#/list/path=%2Fprivate&token=T"
    assert web.public_page(url) == "https://pan.baidu.com/s/1abc"
    assert web.public_page("https://pan.baidu.com/disk/main#/index?category=all") == "https://pan.baidu.com/disk/main"
    assert web.public_page("not a url") is None


def test_probe_never_leaks_fragment():
    class Tab:
        url = "https://pan.baidu.com/disk/main#/index?path=%2Fsecret&token=T"

        def ele(self, selector, timeout=0):
            return selector == "css:.wp-s-header-user"

    out = web._probe_tab(Tab(), wait=0)
    assert out["page"] == "https://pan.baidu.com/disk/main" and out["auth"] == "authenticated"
    assert "secret" not in str(out) and "token" not in str(out)


@pytest.mark.parametrize("url", [
    "https://pan.baidu.com@evil.example/disk/main",
    "https://evil.example/?u=https://pan.baidu.com/disk/main",
    "https://xpan.baidu.com/disk/main",
    "http://pan.baidu.com/disk/main",
    "https://evil.example/#https://pan.baidu.com/disk/",
])
def test_official_host_is_exact(url):
    assert web.official_host(url) is None
    assert web.classify(url, True, True) == "unknown"


def test_official_host_accepts_port_and_case():
    assert web.official_host("https://Passport.Baidu.com:443/v2/?login") == "passport.baidu.com"
    assert web.classify("https://pan.baidu.com/login#x/disk/", False, True) == "unknown"  # not /disk/ path


@pytest.mark.parametrize("delta, owned", [(0.0, True), (0.009, True), (-0.009, True), (0.011, False), (0.5, False)])
def test_pid_reuse_tolerance_is_ten_ms(paths, delta, owned):
    live = _ident(paths, create_time=1000.0 + delta)
    assert (web.verify_owned(_state(), paths, identify=lambda pid: live) is not None) is owned


def test_process_identity_keeps_full_create_time_precision():
    import psutil

    ident = web.process_identity(os.getpid())
    assert ident and abs(ident["create_time"] - psutil.Process().create_time()) < web.CREATE_TIME_TOLERANCE


def _launch_env(monkeypatch, paths, pid=777, port=9331):
    _fake_dp(monkeypatch, pid=pid)
    monkeypatch.setattr(web, "port_listening", lambda p, host=web.HOST: False)
    monkeypatch.setattr(web, "find_profile_browsers", lambda profile: [])
    monkeypatch.setattr(web, "_launch", lambda port_, p, visible: pid)
    monkeypatch.setattr(web, "process_identity", lambda p: _ident(paths, pid=pid, port=port) if p == pid else None)


def test_prepare_saves_state_before_navigation_and_survives_page_fault(paths, monkeypatch):
    _launch_env(monkeypatch, paths)

    def boom(browser):
        assert web.read_state(paths)["pid"] == 777  # ownership already persisted
        raise RuntimeError("page crashed")

    monkeypatch.setattr(web, "_main_tab", boom)
    out = web.prepare_browser(paths=paths, wait=0)
    assert out["running"] and out["pid"] == 777 and out["auth"] == "unknown"
    assert out["probe_error"] == "RuntimeError"
    # status still sees the managed process; stop can claim it.
    assert web.status(paths=paths)["running"] is True
    stopped = []
    monkeypatch.setattr(web, "_stop_owned", lambda st, p: stopped.append(st["pid"]) or True)
    monkeypatch.setattr(web, "verify_owned", lambda st, p, identify=None: None if stopped else _ident(paths, pid=777))
    assert web.stop(paths=paths)["stopped"] is True and stopped == [777]


def test_prepare_attach_failure_keeps_claimable_state(paths, monkeypatch):
    _launch_env(monkeypatch, paths)

    def no_cdp(opts):
        raise ConnectionError("cdp down")

    monkeypatch.setitem(sys.modules, "DrissionPage", types.SimpleNamespace(Chromium=no_cdp))
    with pytest.raises(web.WebError, match="ConnectionError"):
        web.prepare_browser(paths=paths, wait=0)
    saved = web.read_state(paths)
    assert saved["pid"] == 777 and saved["port"] == 9331 and saved["create_time"] == 1000.0
    assert web.verify_owned(saved, paths) is not None  # still recognised as ours


def test_stop_reports_running_when_process_survives(paths, monkeypatch):
    web.write_state(paths, {**_state(), "auth": "authenticated"})
    monkeypatch.setattr(web, "process_identity", lambda pid: _ident(paths))  # never dies
    monkeypatch.setattr(web, "_stop_owned", lambda st, p: False)
    out = web.stop(paths=paths)
    assert out["running"] is True and out["stopped"] is False
    assert "stopped_at" not in web.read_state(paths)


def test_stop_cli_exit_code_reflects_survivor(paths, monkeypatch):
    monkeypatch.setattr(web, "stop", lambda: {"running": True, "stopped": False})
    assert web.main(["stop"]) == 3
    monkeypatch.setattr(web, "stop", lambda: {"running": False, "stopped": True})
    assert web.main(["stop"]) == 0


def test_stop_owned_terminates_only_verified_tree(paths, monkeypatch):
    monkeypatch.setitem(sys.modules, "DrissionPage", types.SimpleNamespace(Chromium=lambda o: 1 / 0))
    monkeypatch.setattr(web, "_options", lambda *a, **k: None)
    alive = {"v": True}
    monkeypatch.setattr(web, "process_identity", lambda pid: _ident(paths) if alive["v"] else None)
    killed = []
    monkeypatch.setattr(web, "_terminate_tree", lambda pid: killed.append(pid) or alive.update(v=False))
    assert web._stop_owned(_state(), paths) is True and killed == [4242]
