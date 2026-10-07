"""Cross-process resource leases for cooperating tasks, independent of business.

Native desktop focus and clipboard share DESKTOP_GUI_RESOURCE. A lease lasts
five minutes and expires without a cleanup worker. Its holder may explicitly
extend the existing deadline by five minutes only during the last minute.
There is no automatic heartbeat renewal. Before each GUI action the executor
must assert ownership; this protocol cannot revoke an already running OS call.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

from filelock import FileLock

from backend.core.settings import get_settings

DESKTOP_GUI_RESOURCE = 'gui:desktop:local'
LEASE_SECONDS = 300
RENEW_WINDOW_SECONDS = 60


class LeaseError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ResourceLeaseStore:
    """One short file mutex protects each resource's atomic state transitions.

    The mutex is never held during task execution. Random tokens identify one
    grant; monotonically increasing generations fence replaced holders. Release
    and renew require the grant token, even when the owner name is identical.
    """
    def __init__(self, root: Path | None = None, *, clock=time.time):
        self.root = Path(root) if root is not None else get_settings().data_dir / 'runtime' / 'resource-leases'
        self.clock = clock

    @contextmanager
    def _state(self, resource: str):
        if not isinstance(resource, str) or not re.fullmatch(r'[A-Za-z0-9:_./-]{1,200}', resource):
            raise ValueError('resource must be an identifier using letters, digits, colon, underscore, dot, slash or hyphen')
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / (hashlib.sha256(resource.encode()).hexdigest() + '.json')
        with FileLock(str(path.with_suffix('.lock')), timeout=5):
            state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'resource': resource, 'generation': 0}
            original = dict(state)
            yield state
            # Failed transitions never publish state. Tokens persist across API,
            # worker and CLI processes; a process crash cannot leave a partial JSON.
            if state != original:
                temp = path.with_suffix('.tmp')
                temp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
                temp.replace(path)

    def _view(self, state: dict, *, credentials=False) -> dict:
        remaining = max(0, state.get('expires_at', 0) - self.clock())
        result = {k: v for k, v in state.items() if k != 'token'}
        result.update(active=bool(state.get('token')) and remaining > 0,
                      remaining_seconds=remaining,
                      renewable=bool(state.get('token')) and 0 < remaining <= RENEW_WINDOW_SECONDS)
        if credentials:
            result['token'] = state['token']
        return result

    def status(self, resource: str) -> dict:
        """Inspect occupancy; expired grants have no ownership and expose no token."""
        with self._state(resource) as state:
            return self._view(state)

    def acquire(self, resource: str, owner: str) -> dict:
        """Grant five minutes or fail immediately with resource_busy."""
        if not isinstance(owner, str) or not owner.strip() or len(owner) > 200:
            raise ValueError('owner must identify the task in at most 200 characters')
        with self._state(resource) as state:
            if self._view(state)['active']:
                raise LeaseError('resource_busy')
            now = self.clock()
            state.update(owner=owner, token=uuid.uuid4().hex,
                         generation=state['generation'] + 1,
                         acquired_at=now, expires_at=now + LEASE_SECONDS)
            return self._view(state, credentials=True)

    def _require(self, state: dict, token: str):
        if not token or state.get('token') != token:
            raise LeaseError('not_owner')
        if not self._view(state)['active']:
            raise LeaseError('lease_expired')

    def check(self, resource: str, token: str) -> dict:
        """Executor gate before a GUI action; expired ownership fails closed."""
        with self._state(resource) as state:
            self._require(state, token)
            return self._view(state)

    def renew(self, resource: str, token: str) -> dict:
        """Add 300 seconds to expiry during 0 < remaining <= 60; never resurrect."""
        with self._state(resource) as state:
            self._require(state, token)
            if not self._view(state)['renewable']:
                raise LeaseError('renewal_too_early')
            state['expires_at'] += LEASE_SECONDS
            return self._view(state, credentials=True)

    def release(self, resource: str, token: str) -> bool:
        """Only this grant can release; stale cleanup cannot clear a successor."""
        with self._state(resource) as state:
            if not token or state.get('token') != token:
                return False
            state.pop('token')
            state['expires_at'] = 0
            return True


@dataclass(frozen=True)
class ResourceLease:
    store: ResourceLeaseStore
    resource: str
    token: str

    def check(self) -> dict:
        return self.store.check(self.resource, self.token)

    def renew(self) -> dict:
        return self.store.renew(self.resource, self.token)

    def lost(self) -> bool:
        """Interruption predicate for long executors; no implicit renewal."""
        try:
            self.check()
            return False
        except LeaseError:
            return True

    def instructions(self) -> str:
        return (f'工程已取得通用GUI租约，resource={self.resource}，token={self.token}。'
                '不得另行申请同一资源。每个GUI动作前通过下列CLI的check核验持有权；'
                'check失败或任务中断立即停止GUI。默认5分钟有效，仅剩余时间<=60秒且>0时，'
                '若仍需操作由你显式调用renew，将原到期时间延长5分钟。禁止后台自动续租。'
                f'核验命令：uv run python scripts/resource_lease.py check --resource {self.resource} --token {self.token}。'
                f'续期命令：uv run python scripts/resource_lease.py renew --resource {self.resource} --token {self.token}。此CLI只管理锁，不执行GUI；'
                '锁命令是允许的基础设施操作。结束后由工程释放租约。')


@contextmanager
def hold_resource(resource: str, owner: str, *, wait_seconds: float = 30,
                  stop_event=None, store: ResourceLeaseStore | None = None):
    """Acquire a bounded lease, release on exit, never automatically renew.

    Callers propagate check/renew credentials to the actual GUI executor and use
    lease.lost as its interruption predicate. Independent resources stay parallel.
    """
    if wait_seconds < 0:
        raise ValueError('wait_seconds must be nonnegative')
    store = store or ResourceLeaseStore()
    deadline = time.monotonic() + wait_seconds
    while True:
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError('Resource acquisition cancelled')
        try:
            grant = store.acquire(resource, owner)
            break
        except LeaseError as exc:
            if exc.code != 'resource_busy' or time.monotonic() >= deadline:
                raise
            if stop_event is not None:
                stop_event.wait(0.1)
            else:
                time.sleep(0.1)
    lease = ResourceLease(store, resource, grant['token'])
    try:
        yield lease
    finally:
        store.release(resource, lease.token)
