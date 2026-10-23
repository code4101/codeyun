"""Run ownership for native Baidu Netdisk GUI actions on the shared account.

The deployment has exactly one dedicated account browser, guarded by
``web.source_lock``; the existing web worker holds that lock while it runs.
A native client session on the same account must take the same lock first,
immediately (``timeout=0``): if the worker is running, entry fails with
``NativeAccountBusy`` instead of waiting, attaching, reading browser state or
navigating. Multi-profile / multi-account switching is not implemented.

With ``with_browser=True`` the session instead enters the public
``web.owned_context(paths=..., lock_timeout=0)`` once: that takes the same
lock and yields our own verified browser (``OwnershipError`` if there is
none; nothing is launched, retried or reattached). ``handle.browser_context``
is a context factory (e.g. a listing ``context=``) lending that same
``OwnedBrowser`` while the session stays owned; it never takes the lock again
and never closes or relaunches the browser.

The browser lock and the desktop GUI lease (``gui:desktop:local``) are two
independent run rights; a session holds both. An external lease token is
checked and reused, never released; otherwise the session acquires its own
lease and releases only that one. Renewal is never automatic: ``renew()``
forwards to the store, which only allows the last-60-seconds window.

Pacing spans providers without touching the worker's own pacer: the first
action waits ``action_gap`` after taking the lock, adjacent actions are at
least ``action_gap`` apart, and if any action ran the lock is handed back only
``action_gap`` after the last one. A borrowed browser use counts as one action
from entry to its real end, so native/web transitions keep the same gap.
No background thread, polling or retry.
"""
from __future__ import annotations

from contextlib import contextmanager
import time
from typing import Callable, ContextManager, Iterator

from backend.core.baidu_netdisk import web
from backend.core.runtime.resource_leases import (
    DESKTOP_GUI_RESOURCE, LeaseError, ResourceLeaseStore,
)

DEFAULT_ACTION_GAP = 3.2


class NativeAccountBusy(RuntimeError):
    """The dedicated account browser lock is held by another provider."""


class NativeAccountSessionClosed(RuntimeError):
    """The session was used after its context exited."""


class NativeAccountHandle:
    """Ownership view handed to the caller inside ``NativeAccountSession``."""

    def __init__(self, store: ResourceLeaseStore, token: str, *, external: bool,
                 action_gap: float, clock: Callable[[], float],
                 sleep: Callable[[float], None],
                 browser: web.OwnedBrowser | None = None):
        self.store = store
        self.token = token
        self.external = external
        self.action_gap = action_gap
        self._clock = clock
        self._sleep = sleep
        self._locked_at = clock()
        self._last_action: float | None = None
        self._open = True
        self._browser = browser
        self._borrowed = False

    @property
    def actions_taken(self) -> bool:
        return self._last_action is not None

    def check(self) -> dict:
        """Assert the session and lease are still owned; no desktop action."""
        if not self._open:
            raise NativeAccountSessionClosed('native account session closed')
        return self.store.check(DESKTOP_GUI_RESOURCE, self.token)

    def renew(self) -> dict:
        """Explicit forward to the store's last-minute renewal contract."""
        self.check()
        return self.store.renew(DESKTOP_GUI_RESOURCE, self.token)

    def _wait_until(self, deadline: float) -> None:
        remaining = deadline - self._clock()
        if remaining > 0:
            self._sleep(remaining)

    def _ready_at(self) -> float:
        anchor = self._locked_at if self._last_action is None else self._last_action
        return anchor + self.action_gap

    def wait_ready(self) -> dict:
        """Wait out the current gap and re-check the lease; not itself an action."""
        self.check()
        self._wait_until(self._ready_at())
        return self.check()

    def pace(self) -> dict:
        """Wait out the gap, re-check the lease, then permit one GUI action."""
        self.check()
        self._wait_until(self._ready_at())
        lease = self.check()
        self._last_action = self._clock()
        return lease

    def record_activity(self) -> None:
        """Move the gap anchor to an input's real completion (also after a failed delegate)."""
        if self._last_action is None:
            raise RuntimeError('record_activity requires a prior pace()')
        self._last_action = max(self._last_action, self._clock())

    @contextmanager
    def browser_context(self) -> Iterator[web.OwnedBrowser]:
        """Lend the session's own browser; usable as ``context=handle.browser_context``.

        Paced like one action (gap wait + lease check before yielding), its
        real end recorded even on failure, and the lease re-checked before a
        normal return. Holds no lock of its own and never closes the browser.
        """
        self.check()
        if self._browser is None:
            raise RuntimeError('native account session opened without with_browser')
        if self._borrowed:
            raise RuntimeError('session browser already borrowed')
        self.pace()
        self._borrowed = True
        try:
            yield self._browser
        finally:
            self._borrowed = False
            self.record_activity()
        self.check()

    def _close(self) -> None:
        if self._open and self._last_action is not None:
            self._wait_until(self._last_action + self.action_gap)
        self._open = False


@contextmanager
def NativeAccountSession(owner: str, *, lease_token: str | None = None,
                         lease_store: ResourceLeaseStore | None = None,
                         web_paths: web.Paths | None = None,
                         action_gap: float = DEFAULT_ACTION_GAP,
                         clock: Callable[[], float] = time.monotonic,
                         sleep: Callable[[float], None] = time.sleep,
                         with_browser: bool = False,
                         lock_timeout: float = 0,
                         owned_context: Callable[..., ContextManager[web.OwnedBrowser]] = web.owned_context,
                         ) -> Iterator[NativeAccountHandle]:
    """Hold the account browser lock plus the GUI lease for native actions.

    Raises ``NativeAccountBusy`` when the browser lock is held, and
    ``LeaseError`` when the external token is invalid/expired or the GUI
    resource is busy. With ``with_browser`` the lock comes from one
    ``owned_context(paths=..., lock_timeout=...)`` entry (zero by default), whose
    ``web.OwnershipError`` (no verified own browser) propagates before any
    lease is touched. Exceptions release only what this session acquired.
    """
    if not isinstance(lock_timeout, (int, float)) or isinstance(lock_timeout, bool) or not 0 <= lock_timeout <= 30:
        raise ValueError('lock_timeout must be between 0 and 30 seconds')
    if action_gap < 0:
        raise ValueError('action_gap must be nonnegative')
    paths = web_paths or web.Paths()
    try:
        if with_browser:
            lock = owned_context(paths=paths, lock_timeout=lock_timeout)
        else:
            lock = web.source_lock(paths, timeout=lock_timeout)
        browser = lock.__enter__()
    except web.OwnershipError:
        raise
    except web.WebError as exc:
        raise NativeAccountBusy(str(exc)) from None
    try:
        store = lease_store or ResourceLeaseStore()
        if lease_token:
            store.check(DESKTOP_GUI_RESOURCE, lease_token)
            token, external = lease_token, True
        else:
            token = store.acquire(DESKTOP_GUI_RESOURCE, owner)['token']
            external = False
        try:
            handle = NativeAccountHandle(store, token, external=external,
                                         action_gap=action_gap, clock=clock, sleep=sleep,
                                         browser=browser if with_browser else None)
            try:
                yield handle
            finally:
                handle._close()
        finally:
            if not external:
                store.release(DESKTOP_GUI_RESOURCE, token)
    finally:
        lock.__exit__(None, None, None)


__all__ = ['DEFAULT_ACTION_GAP', 'LeaseError', 'NativeAccountBusy',
           'NativeAccountHandle', 'NativeAccountSession',
           'NativeAccountSessionClosed']
