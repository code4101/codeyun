"""Resource contracts for native account sessions: real FileLock, no GUI."""
import functools
import os

import pytest

from backend.core.baidu_netdisk import web
from backend.core.baidu_netdisk.ownership import (
    NativeAccountBusy, NativeAccountSession, NativeAccountSessionClosed,
)
from backend.core.runtime.resource_leases import (
    DESKTOP_GUI_RESOURCE as GUI, LeaseError, ResourceLeaseStore,
)


@pytest.fixture
def env(tmp_path):
    now = [1000.0]
    store = ResourceLeaseStore(tmp_path / 'leases', clock=lambda: now[0])
    paths = web.Paths(profile=tmp_path / 'profile', state=tmp_path / 'web' / 'state.json')
    return store, paths, now


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 6))
        self.t += seconds


def test_busy_browser_lock_rejects_without_touching_lease(env):
    store, paths, _ = env
    with web.source_lock(paths, timeout=0):
        with pytest.raises(NativeAccountBusy):
            with NativeAccountSession('native', lease_store=store, web_paths=paths, action_gap=0):
                pytest.fail('must not enter')
    assert not store.status(GUI)['active']
    assert not paths.state.exists() and not paths.profile.exists()


def test_session_excludes_web_provider_and_releases_own_lease(env):
    store, paths, _ = env
    with NativeAccountSession('native', lease_store=store, web_paths=paths, action_gap=0) as s:
        assert not s.external and store.status(GUI)['owner'] == 'native'
        with pytest.raises(web.WebError):
            with web.source_lock(paths, timeout=0):
                pass
        s.pace()
    assert not store.status(GUI)['active']
    with web.source_lock(paths, timeout=0):
        pass


def test_external_lease_reused_and_never_released(env):
    store, paths, _ = env
    token = store.acquire(GUI, 'outer')['token']
    with pytest.raises(RuntimeError, match='boom'):
        with NativeAccountSession('native', lease_token=token, lease_store=store,
                                  web_paths=paths, action_gap=0) as s:
            assert s.external and s.token == token
            s.pace()
            raise RuntimeError('boom')
    assert store.check(GUI, token)['owner'] == 'outer'
    assert store.status(GUI)['generation'] == 1
    with web.source_lock(paths, timeout=0):
        pass


def test_invalid_external_token_releases_browser_lock(env):
    store, paths, _ = env
    with pytest.raises(LeaseError, match='not_owner'):
        with NativeAccountSession('native', lease_token='bogus', lease_store=store, web_paths=paths):
            pass
    with web.source_lock(paths, timeout=0):
        pass


def test_gui_busy_releases_browser_lock(env):
    store, paths, _ = env
    store.acquire(GUI, 'other')
    with pytest.raises(LeaseError, match='resource_busy'):
        with NativeAccountSession('native', lease_store=store, web_paths=paths):
            pass
    with web.source_lock(paths, timeout=0):
        pass


def test_exception_releases_own_lock_and_lease(env):
    store, paths, _ = env
    with pytest.raises(ValueError):
        with NativeAccountSession('native', lease_store=store, web_paths=paths, action_gap=0):
            raise ValueError('x')
    assert not store.status(GUI)['active']
    with web.source_lock(paths, timeout=0):
        pass


def test_expired_lease_fails_closed_and_no_auto_renew(env):
    store, paths, now = env
    with NativeAccountSession('native', lease_store=store, web_paths=paths, action_gap=0) as s:
        with pytest.raises(LeaseError, match='renewal_too_early'):
            s.renew()
        now[0] += 250
        assert s.renew()['expires_at'] == 1600
        now[0] += 351
        with pytest.raises(LeaseError, match='lease_expired'):
            s.check()
        with pytest.raises(LeaseError, match='lease_expired'):
            s.pace()
    assert not s.actions_taken
    with pytest.raises(NativeAccountSessionClosed):
        s.check()


def test_pacing_boundaries(env):
    store, paths, _ = env
    clock = Clock()
    with NativeAccountSession('native', lease_store=store, web_paths=paths,
                              action_gap=3.2, clock=clock, sleep=clock.sleep) as s:
        s.pace()                      # first action waits full gap after lock
        clock.t += 1.0                # caller's action took 1s
        s.pace()
        clock.t += 5.0                # already past gap: no sleep
        s.pace()
    assert clock.sleeps == [3.2, 2.2, 3.2]  # last sleep: hand-back gap
    assert clock.t == pytest.approx(3.2 + 1.0 + 2.2 + 5.0 + 3.2)


def test_no_hand_back_wait_without_actions(env):
    store, paths, _ = env
    clock = Clock()
    with NativeAccountSession('native', lease_store=store, web_paths=paths,
                              action_gap=3.2, clock=clock, sleep=clock.sleep) as s:
        s.check()
    assert clock.sleeps == []


# ----- borrowed own browser under the same account lock ----------------------
# Real web.owned_context (real FileLock) with its public identify/attach hooks;
# the attached "browser" is a static sentinel, nothing is launched or driven.

class Sentinel:
    process_id = 4242


@pytest.fixture
def owned(env):
    _, paths, _ = env
    attaches = []

    def attach(state, p):
        attaches.append(state['pid'])
        return Sentinel()

    def identify(pid):
        return {'create_time': 1.0, 'profile': os.path.normcase(os.path.normpath(str(paths.profile))),
                'port': 9333} if pid == 4242 else None

    web.write_state(paths, {'pid': 4242, 'port': 9333, 'create_time': 1.0})
    return functools.partial(web.owned_context, identify=identify, attach=attach), attaches


def test_borrowed_browser_reuses_session_lock_without_reacquire(env, owned):
    store, paths, _ = env
    ctx, attaches = owned
    with NativeAccountSession('native', lease_store=store, web_paths=paths, action_gap=0,
                              with_browser=True, owned_context=ctx) as s:
        factory = s.browser_context              # listing-style: context=handle.browser_context
        with factory() as first:
            assert isinstance(first, web.OwnedBrowser) and isinstance(first.browser, Sentinel)
            with pytest.raises(web.WebError):    # lock still held by the session itself
                with web.source_lock(paths, timeout=0):
                    pass
        with factory() as second:
            assert second is first
        assert attaches == [4242]                # attached once, never re-entered
        assert store.status(GUI)['owner'] == 'native'
        with pytest.raises(web.WebError):        # borrow end does not release the lock
            with web.source_lock(paths, timeout=0):
                pass
    assert not store.status(GUI)['active']
    with web.source_lock(paths, timeout=0):
        pass


def test_browser_session_busy_when_lock_held(env, owned):
    store, paths, _ = env
    ctx, attaches = owned
    with web.source_lock(paths, timeout=0):
        with pytest.raises(NativeAccountBusy):
            with NativeAccountSession('native', lease_store=store, web_paths=paths,
                                      with_browser=True, owned_context=ctx):
                pytest.fail('must not enter')
    assert attaches == [] and not store.status(GUI)['active']


def test_browser_unavailable_typed_and_no_gui_lease(env):
    store, paths, _ = env                        # no recorded own browser
    with pytest.raises(web.OwnershipError) as err:
        with NativeAccountSession('native', lease_store=store, web_paths=paths, with_browser=True):
            pytest.fail('must not enter')
    assert err.value.reason == 'not_owned'
    assert not isinstance(err.value, NativeAccountBusy)
    assert not store.status(GUI)['active'] and not paths.profile.exists()
    with web.source_lock(paths, timeout=0):
        pass


def test_borrow_refused_when_closed_native_only_or_nested(env, owned):
    store, paths, _ = env
    ctx, _ = owned
    with NativeAccountSession('native', lease_store=store, web_paths=paths, action_gap=0) as plain:
        with pytest.raises(RuntimeError, match='with_browser'):
            with plain.browser_context():
                pass
    with NativeAccountSession('native', lease_store=store, web_paths=paths, action_gap=0,
                              with_browser=True, owned_context=ctx) as s:
        with s.browser_context():
            with pytest.raises(RuntimeError, match='already borrowed'):
                with s.browser_context():
                    pass
    with pytest.raises(NativeAccountSessionClosed):
        with s.browser_context():
            pass


def test_browser_session_external_lease_kept_own_lease_released(env, owned):
    store, paths, _ = env
    ctx, _ = owned
    token = store.acquire(GUI, 'outer')['token']
    with pytest.raises(RuntimeError, match='boom'):
        with NativeAccountSession('native', lease_token=token, lease_store=store, web_paths=paths,
                                  action_gap=0, with_browser=True, owned_context=ctx) as s:
            with s.browser_context():
                raise RuntimeError('boom')
    assert store.check(GUI, token)['owner'] == 'outer'
    store.release(GUI, token)
    with pytest.raises(RuntimeError, match='boom'):
        with NativeAccountSession('native', lease_store=store, web_paths=paths, action_gap=0,
                                  with_browser=True, owned_context=ctx) as s:
            with s.browser_context():
                raise RuntimeError('boom')
    assert not store.status(GUI)['active']
    with web.source_lock(paths, timeout=0):
        pass


def test_borrow_end_rechecks_lease(env, owned):
    store, paths, now = env
    ctx, _ = owned
    with NativeAccountSession('native', lease_store=store, web_paths=paths, action_gap=0,
                              with_browser=True, owned_context=ctx) as s:
        with pytest.raises(LeaseError, match='lease_expired'):
            with s.browser_context():
                now[0] += 301
        assert s.actions_taken
    with web.source_lock(paths, timeout=0):
        pass


def test_borrowed_use_paced_as_one_action_with_handback(env, owned):
    store, paths, _ = env
    ctx, _ = owned
    clock = Clock()
    with NativeAccountSession('native', lease_store=store, web_paths=paths, action_gap=3.2,
                              clock=clock, sleep=clock.sleep, with_browser=True, owned_context=ctx) as s:
        s.pace()                      # native action
        clock.t += 1.0
        with s.browser_context():     # native -> web: rest of gap
            clock.t += 5.0            # web listing takes 5s
        s.pace()                      # web -> native: full gap after web end
    assert clock.sleeps == [3.2, 2.2, 3.2, 3.2]
    clock = Clock()
    with NativeAccountSession('native', lease_store=store, web_paths=paths, action_gap=3.2,
                              clock=clock, sleep=clock.sleep, with_browser=True, owned_context=ctx) as s:
        with s.browser_context():
            clock.t += 4.0
    assert clock.sleeps == [3.2, 3.2]  # first-use wait, hand-back after web end
