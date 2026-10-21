"""Atomic JSON writes of the Gitee crawler under Windows sharing conflicts (worker.json crash 2026-10-21)."""
import errno
import json
import os
import sys
import threading
import time

import pytest
from filelock import FileLock

from backend.core.skilldep_materials import gitee_crawl as g
from backend.tests.test_skilldep_gitee_crawl import Clock, Gitee, Registry, cursor, seed_v1, step

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows replace semantics')

REAL_REPLACE = os.replace


def win_error(winerror, path='x'):
    return PermissionError(errno.EACCES, 'Access is denied', str(path), winerror)


class FlakyReplace:
    """Fail ``os.replace`` onto targets named ``name`` with ``winerror`` while ``failing()`` is true."""

    def __init__(self, name, winerror=5, times=None, failing=None):
        self.name, self.winerror, self.times, self.failing = name, winerror, times, failing
        self.attempts = []

    def __call__(self, src, dst):
        if os.path.basename(dst) == self.name:
            self.attempts.append(str(src))
            busy = self.failing() if self.failing else len(self.attempts) <= self.times
            if busy:
                raise win_error(self.winerror, src)
        return REAL_REPLACE(src, dst)


@pytest.fixture
def fast_retry(monkeypatch):
    monkeypatch.setattr(g, 'REPLACE_DELAYS', (0.0,) * 4)


def own_temps(d, name):
    return sorted(p.name for p in d.iterdir() if p.name.startswith(name + '.') and p.name != name + '.tmp')


def test_two_sharing_failures_then_exact_write(tmp_path, fast_retry, monkeypatch):
    target = tmp_path / 'worker.json'
    target.write_text('{"old": 1}', encoding='utf-8')
    flaky = FlakyReplace('worker.json', winerror=32, times=2)
    monkeypatch.setattr(g.os, 'replace', flaky)
    g._wj(target, {'pid': 7, 'state': 'paused'})
    assert json.loads(target.read_text(encoding='utf-8')) == {'pid': 7, 'state': 'paused'}
    assert len(flaky.attempts) == 3 and len(set(flaky.attempts)) == 1  # same unique temp, retried in place
    assert own_temps(tmp_path, 'worker.json') == []


def test_exhausted_replace_keeps_old_json_and_leaves_no_own_temp(tmp_path, fast_retry, monkeypatch):
    target = tmp_path / 'worker.json'
    target.write_text('{"old": 1}', encoding='utf-8')
    legacy = tmp_path / 'worker.json.tmp'  # a stale file from the old fixed-name writer stays untouched
    legacy.write_text('legacy', encoding='utf-8')
    flaky = FlakyReplace('worker.json', winerror=5, failing=lambda: True)
    monkeypatch.setattr(g.os, 'replace', flaky)
    with pytest.raises(PermissionError) as info:
        g._wj(target, {'new': 2})
    assert info.value.winerror == 5 and g.replace_is_transient(info.value)
    assert len(flaky.attempts) == len(g.REPLACE_DELAYS) + 1  # bounded
    assert json.loads(target.read_text(encoding='utf-8')) == {'old': 1}
    assert legacy.read_text(encoding='utf-8') == 'legacy' and own_temps(tmp_path, 'worker.json') == []


@pytest.mark.parametrize('exc', [win_error(1314), PermissionError(errno.EACCES, 'denied'),
                                 OSError(errno.ENOSPC, 'No space left on device')])
def test_non_transient_replace_errors_propagate_without_retry(tmp_path, fast_retry, monkeypatch, exc):
    target = tmp_path / 'cursor.json'
    target.write_text('{"next_page": 5}', encoding='utf-8')
    calls = []

    def replace(src, dst):
        calls.append(src)
        raise exc

    monkeypatch.setattr(g.os, 'replace', replace)
    with pytest.raises(OSError) as info:
        g._wj(target, {'next_page': 6})
    assert info.value is exc and len(calls) == 1 and not g.replace_is_transient(exc)
    assert json.loads(target.read_text(encoding='utf-8')) == {'next_page': 5}
    assert own_temps(tmp_path, 'cursor.json') == []


def test_disk_full_while_writing_temp_propagates(tmp_path, monkeypatch):
    target = tmp_path / 'cursor.json'
    target.write_text('{"next_page": 5}', encoding='utf-8')

    def fsync(fd):
        raise OSError(errno.ENOSPC, 'No space left on device')

    monkeypatch.setattr(g.os, 'fsync', fsync)
    with pytest.raises(OSError) as info:
        g._wj(target, {'next_page': 6})
    assert info.value.errno == errno.ENOSPC
    assert json.loads(target.read_text(encoding='utf-8')) == {'next_page': 5}
    assert own_temps(tmp_path, 'cursor.json') == []


def test_real_windows_reader_handle_blocks_replace_until_released(tmp_path):
    """A plain ``open()`` reader (no FILE_SHARE_DELETE) is exactly the production WinError 5."""
    target = tmp_path / 'worker.json'
    target.write_text('{"old": 1}', encoding='utf-8')
    probe = tmp_path / 'probe.json'
    probe.write_text('{}', encoding='utf-8')
    reader = target.open('rb')
    try:
        with pytest.raises(PermissionError) as info:
            os.replace(probe, target)
        assert info.value.winerror in g.REPLACE_WINERRORS
        threading.Timer(0.3, reader.close).start()  # status reader lets go during the backoff
        g._wj(target, {'new': 2})
    finally:
        reader.close()
    assert json.loads(target.read_text(encoding='utf-8')) == {'new': 2}
    assert own_temps(tmp_path, 'worker.json') == []


def test_real_windows_reader_held_too_long_exhausts_and_keeps_old(tmp_path, monkeypatch):
    monkeypatch.setattr(g, 'REPLACE_DELAYS', (0.01, 0.01))
    target = tmp_path / 'worker.json'
    target.write_text('{"old": 1}', encoding='utf-8')
    with target.open('rb'):
        t0 = time.monotonic()
        with pytest.raises(PermissionError) as info:
            g._wj(target, {'new': 2})
        assert time.monotonic() - t0 < 5 and g.replace_is_transient(info.value)
    assert json.loads(target.read_text(encoding='utf-8')) == {'old': 1}
    assert own_temps(tmp_path, 'worker.json') == []


def worker(tmp_path, clock, api, sleep):
    return g.run_worker(tmp_path, clock=clock, sleep=sleep, session=api, registry_factory=Registry,
                        disk_free=lambda r: 10 * 1024 ** 3)


def test_worker_survives_transient_heartbeat_failures_without_extra_requests(tmp_path, fast_retry, monkeypatch):
    """The production crash: a paused heartbeat replace hits WinError 5; the worker keeps going."""
    seed_v1(tmp_path)
    clock = Clock()
    api = Gitee(clock, remaining=2)  # page 22, then a one-hour quota pause
    state = {'paused_beats': 0}
    flaky = FlakyReplace('worker.json', failing=lambda: 1 <= state['paused_beats'] <= 3)
    monkeypatch.setattr(g.os, 'replace', flaky)
    real_sleep = clock.sleep

    def sleep(s):
        real_sleep(s)
        if json.loads((tmp_path / 'worker.json').read_text(encoding='utf-8'))['state'] == 'paused':
            state['paused_beats'] += 1
            if state['paused_beats'] == 5:
                g.request_stop(tmp_path)

    info = worker(tmp_path, clock, api, sleep)
    saved = json.loads((tmp_path / 'worker.json').read_text(encoding='utf-8'))
    assert info['state'] == saved['state'] == 'stopped' and saved['pid'] == os.getpid()
    assert saved['heartbeat_misses'] == 0 and 'WinError 5' in saved['last_heartbeat_error']
    assert [n for n, _ in api.calls] == [22] and cursor(tmp_path)['next_page'] == 23
    assert own_temps(tmp_path, 'worker.json') == []
    FileLock(str(tmp_path / '.crawl.lock')).acquire(timeout=0)  # released


def test_worker_stops_after_persistent_heartbeat_failure_with_original_error(tmp_path, fast_retry, monkeypatch):
    """Not every WinError 5 is transient: a target that never frees ends the worker, unmasked."""
    seed_v1(tmp_path)
    clock = Clock()
    api = Gitee(clock, remaining=2)
    started = {'ok': False}
    flaky = FlakyReplace('worker.json', failing=lambda: started['ok'])
    monkeypatch.setattr(g.os, 'replace', flaky)
    real_sleep = clock.sleep

    def sleep(s):
        real_sleep(s)
        started['ok'] = bool(api.calls)

    with pytest.raises(PermissionError) as info:
        worker(tmp_path, clock, api, sleep)
    assert info.value.winerror == 5
    assert any('final worker.json write failed' in n for n in getattr(info.value, '__notes__', []))
    assert [n for n, _ in api.calls] == [22]
    assert json.loads((tmp_path / 'worker.json').read_text(encoding='utf-8'))['pid'] == os.getpid()
    FileLock(str(tmp_path / '.crawl.lock')).acquire(timeout=0)  # released


def test_critical_cursor_write_failure_fails_closed_without_false_progress(tmp_path, fast_retry, monkeypatch):
    seed_v1(tmp_path)
    clock = Clock()
    api = Gitee(clock)
    flaky = FlakyReplace('cursor.json', failing=lambda: bool(api.calls))
    monkeypatch.setattr(g.os, 'replace', flaky)
    with pytest.raises(PermissionError):
        step(tmp_path, clock, api, max_pages=3)
    assert [n for n, _ in api.calls] == [22]  # no further page fetched after the failed commit
    assert cursor(tmp_path)['next_page'] == 22 and own_temps(tmp_path / g.MID, 'cursor.json') == []
    monkeypatch.setattr(g.os, 'replace', REAL_REPLACE)
    api2 = Gitee(clock)
    res = step(tmp_path, clock, api2, max_pages=1)
    assert [n for n, _ in api2.calls] == [22] and res['new_pages'] == [22]  # resumes from the durable cursor
    assert cursor(tmp_path)['next_page'] == 23
