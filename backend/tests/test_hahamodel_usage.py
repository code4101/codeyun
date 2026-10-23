import datetime as dt

import pytest

from backend.core import hahamodel_usage as haha

TZ = dt.timezone(dt.timedelta(hours=8))


def at(day, hour=0):
    # 2026-09-28 is a Monday.
    return dt.datetime(2026, 9, day, hour, tzinfo=TZ) if day <= 30 else dt.datetime(2026, 10, day - 30, hour, tzinfo=TZ)


def parsed(points):
    return [(dt.datetime.fromisoformat(p["at"]), p["value"]) for p in points]


def hours(*items):
    return {str(int(moment.timestamp())): usd for moment, usd in items}


def test_week_starts_on_local_monday():
    assert haha.week_start_of(at(30, 15)) == at(28)
    assert haha.week_start_of(at(28)) == at(28)
    assert haha.week_start_of(at(27, 23)) == at(21)


def test_running_week_quota_is_scaled_by_elapsed_share():
    # Previous week spent $50 in total; this week $30 over the first 3 days.
    history = hours((at(22, 10), 20.0), (at(25, 9), 30.0), (at(29, 10), 10.0), (at(30, 10), 20.0))
    window = haha.build_hahamodel_weekly_window(history, now=at(31))
    previous, running = window["weeks"]
    assert (previous["used"], previous["quota"], previous["complete"]) == (50.0, 50.0, True)
    assert running["used"] == 30.0 and running["quota"] == 70.0 and not running["complete"]
    assert window["max"] == 70.0
    assert dt.datetime.fromisoformat(window["window_start"]) == at(21)
    assert dt.datetime.fromisoformat(window["window_end"]) == at(35)
    assert [p["start_value"] for p in window["periods"]] == [50.0, 70.0]


def test_points_drop_within_the_spend_hour_and_break_between_weeks():
    history = hours((at(22, 10), 20.0), (at(25, 9), 30.0), (at(29, 10), 30.0))
    window = haha.build_hahamodel_weekly_window(history, now=at(31))
    points = window["points"]
    breaks = [i for i, p in enumerate(points) if p["value"] is None]
    assert len(breaks) == 1
    first_week, second_week = parsed(points[:breaks[0]]), parsed(points[breaks[0] + 1:])
    assert first_week[0] == (at(21), 50.0)
    assert (at(22, 10), 50.0) in first_week
    assert (at(22, 11), 30.0) in first_week
    assert first_week[-1] == (at(28), 0.0)
    # The running week ends exactly on its even-burn line at "now".
    assert second_week[-1] == (at(31), 40.0)


def test_axis_max_covers_older_heavier_weeks():
    history = hours((at(8, 10), 100.0), (at(29, 10), 10.0))
    window = haha.build_hahamodel_weekly_window(history, now=at(31))
    assert window["max"] == 100.0


def test_merge_replaces_fetched_range_only(tmp_path):
    path = tmp_path / "history.json"
    haha.merge_hahamodel_history(hours((at(1), 1.0), (at(20), 2.0)), start=at(1), end=at(21), path=path)
    merged = haha.merge_hahamodel_history(hours((at(21, 5), 3.0)), start=at(14), end=at(28), path=path)
    assert merged == hours((at(1), 1.0), (at(21, 5), 3.0))


def test_hourly_rows_sum_across_models():
    rows = [
        {"created_at": 7200, "quota": 250000, "model_name": "a"},
        {"created_at": 7200, "quota": 250000, "model_name": "b"},
        {"created_at": 3600, "quota": 500000},
    ]
    assert haha.sum_hourly_usd(rows, 500000.0) == {"7200": 1.0, "3600": 1.0}


def test_browser_failure_does_not_switch_to_key_or_http_password_login(monkeypatch):
    def fail(**kwargs):
        raise haha.HahaModelError('browser unavailable')
    monkeypatch.setattr(haha, 'fetch_hahamodel_browser_account', fail)
    monkeypatch.setattr(haha, 'read_hahamodel_token_config', lambda: pytest.fail('counter scope must stay account'))
    monkeypatch.setattr(haha, 'read_hahamodel_credentials', lambda: pytest.fail('must not password login'))
    payload = haha.read_hahamodel_usage(now=at(31))
    assert payload["configured"] is True and not payload["available"]


def test_credentials_fall_back_to_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv(haha.HAHAMODEL_USERNAME_ENV, raising=False)
    monkeypatch.delenv(haha.HAHAMODEL_PASSWORD_ENV, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('HAHAMODEL_USERNAME="u"\nHAHAMODEL_PASSWORD="p\\"w"\n', encoding="utf-8")
    assert haha.read_hahamodel_credentials(env_file=env_file) == ("u", 'p"w')


def test_token_config_only_accepts_hahamodel(monkeypatch, tmp_path):
    import json
    path = tmp_path / 'settings.json'
    for base, valid in [('https://hahamodel.com', True), ('https://other.example', False),
                        ('https://hahamodel.com.evil.example', False)]:
        path.write_text(json.dumps({'env': {'ANTHROPIC_BASE_URL': base,
                                         'ANTHROPIC_AUTH_TOKEN': 'test-key'}}))
        assert bool(haha.read_hahamodel_token_config(path=path)) is valid


def test_unlimited_token_reads_used_total_without_fake_balance(monkeypatch):
    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Http:
        proxies = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            assert kwargs['allow_redirects'] is False
            if url.endswith('/api/status'):
                return Response({'success': True, 'data': {'quota_per_unit': 500000}})
            assert kwargs['headers']['Authorization'] == 'Bearer test-key'
            return Response({'code': True, 'data': {'total_used': 909974189,
                            'total_available': -909974189, 'unlimited_quota': True, 'name': '261008'}})

    monkeypatch.setattr(haha.requests, 'Session', Http)
    result = haha.fetch_hahamodel_token_usage({'token': 'test-key'})
    assert result['used_total'] == 1819.9484
    assert result['balance'] is None and result['request_count'] is None


def test_token_collector_preserves_hourly_history_and_records_real_samples(monkeypatch, tmp_path):
    monkeypatch.setattr(haha, 'read_hahamodel_history', lambda: {})
    monkeypatch.setattr(haha, 'get_hahamodel_snapshot_path', lambda: tmp_path / 'snapshot.json')
    monkeypatch.setattr(haha, 'get_hahamodel_token_history_path', lambda: tmp_path / 'token.json')
    monkeypatch.setattr(haha, 'merge_hahamodel_history', lambda *a, **kw: pytest.fail('must not rewrite hourly data'))
    payload = {'configured': True, 'available': True, 'source': 'token', 'source_id': 'key-one',
               'used_total': 1819.95, 'balance': None, 'error': ''}
    monkeypatch.setattr(haha, 'read_hahamodel_usage', lambda **kw: dict(payload))
    first = haha.collect_hahamodel_usage_snapshot(now=at(30, 8))
    payload['used_total'] = 1820.95
    haha.collect_hahamodel_usage_snapshot(now=at(30, 9))
    history = haha.read_hahamodel_aligned_history(observed_at=at(30, 9).isoformat())
    assert sum(history.values()) == pytest.approx(1.0)
    assert 'source_id' not in first['payload']
    payload['available'] = False
    haha.collect_hahamodel_usage_snapshot(now=at(30, 10))
    assert haha.load_hahamodel_snapshot()['payload']['used_total'] == 1820.95
    payload.update(available=True, source_id='key-two', used_total=30.0)
    haha.collect_hahamodel_usage_snapshot(now=at(30, 11))
    assert haha.read_hahamodel_aligned_history(observed_at=at(30, 11).isoformat()) == {}
    payload['used_total'] = 2.0
    haha.collect_hahamodel_usage_snapshot(now=at(30, 12))
    assert haha.read_hahamodel_aligned_history(observed_at=at(30, 12).isoformat()) == {}


def test_token_response_keeps_original_weekly_chart(monkeypatch, tmp_path):
    from backend.api import codex_setup as api
    monkeypatch.setattr(haha, 'get_hahamodel_token_history_path', lambda: tmp_path / 'history.json')
    original = hours((at(29, 10), 30.0))
    monkeypatch.setattr(haha, 'read_hahamodel_history', lambda: dict(original))
    haha.save_hahamodel_usage_baseline(used_total=1800, observed_at=at(30, 8), source_id='key')
    haha.record_hahamodel_token_usage({'source_id': 'key', 'used_total': 1820.0}, at(30, 9))
    response = api._hahamodel_response({'available': True, 'source': 'token',
                                       'used_total': 1820.0, 'balance': None, 'request_count': None},
                                      at(30, 9).isoformat())
    assert response.weekly_window.weeks[-1].used == 50.0
    assert haha.read_hahamodel_history() == original
    assert response.balance is None


def test_alignment_fills_outage_delta_without_double_counting(monkeypatch, tmp_path):
    monkeypatch.setattr(haha, 'get_hahamodel_token_history_path', lambda: tmp_path / 'history.json')
    original = hours((at(29, 8), 10.0), (at(29, 9), 5.0))
    monkeypatch.setattr(haha, 'read_hahamodel_history', lambda: dict(original))
    haha.save_hahamodel_usage_baseline(used_total=183.1631, observed_at=at(29, 9), source_id='key')
    haha.record_hahamodel_token_usage({'source_id': 'key', 'used_total': 1823.7321}, at(31, 9))
    history = haha.read_hahamodel_aligned_history(observed_at=at(31, 9).isoformat())
    assert sum(history.values()) == pytest.approx(15 + 1823.7321 - 183.1631)
    assert history[str(int(at(29, 8).timestamp()))] == 10.0
    assert haha.read_hahamodel_aligned_history(observed_at=at(31, 9).isoformat()) == history
    assert haha.read_hahamodel_history() == original


def test_account_usage_covers_all_keys_and_reports_real_balance(monkeypatch):
    monkeypatch.setattr(haha, 'fetch_hahamodel_browser_account', lambda **kw: {
        'status': {'quota_per_unit': 500000},
        'profile': {'quota': 8437393976, 'used_quota': 1562606024, 'request_count': 39153},
        'hourly': [{'created_at': int(at(30, 8).timestamp()), 'quota': 500000}],
    })
    monkeypatch.setattr(haha, 'read_hahamodel_credentials', lambda: pytest.fail('must not log in'))
    result = haha.read_hahamodel_usage()
    assert result['source'] == 'account'
    assert result['used_total'] == 3125.212
    assert result['balance'] == 16874.788
    assert result['request_count'] == 39153
    assert sum(result['hourly'].values()) == 1.0


class FakeHttp:
    def __init__(self, *, reject_login=False, fail_read=False, fail_logout=False, probe_status=200, refresh_status=200):
        self.calls = []
        self.reject_login = reject_login
        self.fail_read = fail_read
        self.fail_logout = fail_logout
        self.closed = False
        self.cookies = haha.requests.cookies.RequestsCookieJar()
        self.probe_status = probe_status
        self.refresh_status = refresh_status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith('/login') or url.endswith('/refresh'):
            self.cookies.set('refresh', 'saved-cookie', domain='hahamodel.com', path='/')
            payload = {'success': not self.reject_login, 'data': {
                'user': {'id': 7}, 'access_token': 'test-token', 'session': {'sid': 'test-sid'},
            }}
        else:
            payload = {'success': not self.fail_logout}
        return self.response(payload, self.refresh_status if url.endswith('/refresh') else 200)

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.fail_read:
            raise haha.requests.Timeout('read timed out')
        return self.response({'success': True, 'data': [] if url.endswith('/data/self') else {}},
                             self.probe_status if url.endswith('/user/self') else 200)

    @staticmethod
    def response(payload, status=200):
        class Response:
            status_code = status

            def json(self):
                return payload
        return Response()


@pytest.mark.parametrize('fail_read', [False, True])
def test_collection_retains_login_even_when_read_fails(monkeypatch, tmp_path, fail_read):
    path = tmp_path / 'session.json'
    monkeypatch.setattr(haha, 'get_hahamodel_session_path', lambda: path)
    http = FakeHttp(fail_read=fail_read)
    monkeypatch.setattr(haha.requests, 'Session', lambda: http)
    if fail_read:
        with pytest.raises(haha.requests.Timeout):
            haha.fetch_hahamodel_account('u', 'p', start=at(28), end=at(31))
    else:
        haha.fetch_hahamodel_account('u', 'p', start=at(28), end=at(31))
    assert not any(url.endswith('/logout') for url, _ in http.calls)
    saved = haha._read_json(path)
    assert saved['headers'] == {
        'New-Api-User': '7', 'Authorization': 'Bearer test-token', 'X-Auth-Session': 'test-sid',
    }
    assert saved['cookies'][0]['value'] == 'saved-cookie'
    assert 'password' not in saved
    assert http.closed


def test_rejected_login_does_not_attempt_logout(monkeypatch, tmp_path):
    monkeypatch.setattr(haha, 'get_hahamodel_session_path', lambda: tmp_path / 'session.json')
    http = FakeHttp(reject_login=True)
    monkeypatch.setattr(haha.requests, 'Session', lambda: http)
    with pytest.raises(haha.HahaModelError, match='登录'):
        haha.fetch_hahamodel_account('u', 'p', start=at(28), end=at(31))
    assert len(http.calls) == 1 and http.closed


def test_logout_failure_is_reported(monkeypatch):
    http = FakeHttp(fail_logout=True)
    monkeypatch.setattr(haha.requests, 'Session', lambda: http)
    with pytest.raises(haha.HahaModelError, match='退出'):
        haha.logout_hahamodel(http, {'X-Auth-Session': 'test-sid'})


def seed_session(monkeypatch, tmp_path):
    path = tmp_path / 'session.json'
    monkeypatch.setattr(haha, 'get_hahamodel_session_path', lambda: path)
    haha._write_json(path, {
        'username': 'u', 'headers': {'Authorization': 'Bearer old-token', 'X-Auth-Session': 'test-sid'},
        'cookies': [{'name': 'refresh', 'value': 'old-cookie', 'domain': 'hahamodel.com', 'path': '/'}],
    })
    return path


def test_reuses_cookie_and_token_without_logging_in(monkeypatch, tmp_path):
    seed_session(monkeypatch, tmp_path)
    # Distinct HTTP instances model separate collector processes/restarts.
    for _ in range(2):
        http = FakeHttp()
        monkeypatch.setattr(haha.requests, 'Session', lambda: http)
        haha.fetch_hahamodel_account('u', 'p', start=at(28), end=at(31))
        assert http.cookies.get('refresh') == 'old-cookie'
        assert all('/login' not in url and '/logout' not in url for url, _ in http.calls)
        assert http.calls[0][1]['headers']['Authorization'] == 'Bearer old-token'


@pytest.mark.parametrize('refresh_status, expect_login', [(200, False), (401, True)])
def test_expired_access_token_refreshes_before_password_login(monkeypatch, tmp_path, refresh_status, expect_login):
    path = seed_session(monkeypatch, tmp_path)
    http = FakeHttp(probe_status=401, refresh_status=refresh_status)
    monkeypatch.setattr(haha.requests, 'Session', lambda: http)
    with haha.hahamodel_session('u', 'p') as (_, headers):
        assert headers['Authorization'] == 'Bearer test-token'
    urls = [url for url, _ in http.calls]
    assert urls[0].endswith('/user/self') and urls[1].endswith('/auth/refresh')
    assert any(url.endswith('/login') for url in urls) == expect_login
    assert haha._read_json(path)['headers']['Authorization'] == 'Bearer test-token'


@pytest.mark.parametrize('status', [403, 429, 500])
def test_unrelated_http_failure_never_logs_in(monkeypatch, tmp_path, status):
    path = seed_session(monkeypatch, tmp_path)
    old_state = path.read_text()
    http = FakeHttp(probe_status=status)
    monkeypatch.setattr(haha.requests, 'Session', lambda: http)
    with pytest.raises(haha.HahaModelError):
        with haha.hahamodel_session('u', 'p'):
            pytest.fail('failed session probe should not yield')
    assert len(http.calls) == 1 and path.read_text() == old_state


def test_timeout_preserves_cached_login(monkeypatch, tmp_path):
    path = seed_session(monkeypatch, tmp_path)
    old_state = path.read_text()
    http = FakeHttp(fail_read=True)
    monkeypatch.setattr(haha.requests, 'Session', lambda: http)
    with pytest.raises(haha.requests.Timeout):
        with haha.hahamodel_session('u', 'p'):
            pass
    assert len(http.calls) == 1 and path.read_text() == old_state


def test_concurrent_collectors_share_one_login(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    monkeypatch.setattr(haha, 'get_hahamodel_session_path', lambda: tmp_path / 'session.json')
    clients = []
    barrier = Barrier(4)

    def new_client():
        http = FakeHttp()
        clients.append(http)
        return http

    def collect():
        barrier.wait(timeout=5)
        return haha.fetch_hahamodel_account('u', 'p', start=at(28), end=at(31))

    monkeypatch.setattr(haha.requests, 'Session', new_client)
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: collect(), range(4)))
    assert len(results) == 4
    assert sum(url.endswith('/login') for http in clients for url, _ in http.calls) == 1


def test_refresh_server_error_does_not_replace_login(monkeypatch, tmp_path):
    path = seed_session(monkeypatch, tmp_path)
    old_state = path.read_text()
    http = FakeHttp(probe_status=401, refresh_status=503)
    monkeypatch.setattr(haha.requests, 'Session', lambda: http)
    with pytest.raises(haha.HahaModelError):
        with haha.hahamodel_session('u', 'p'):
            pass
    assert len(http.calls) == 2 and path.read_text() == old_state


def test_cached_response_estimate_and_curve_stop_at_observation(monkeypatch):
    from backend.api import codex_setup as api

    observed = at(30, 9)
    history = hours((at(29, 10), 30.0))
    monkeypatch.setattr(api, 'read_hahamodel_history', lambda: history)
    response = api._hahamodel_response({'configured': True, 'available': True}, observed.isoformat())
    window = response.weekly_window
    expected = haha.build_hahamodel_weekly_window(history, now=observed)
    assert window.weeks[-1].quota == expected['weeks'][-1]['quota']
    assert window.points[-1].at == observed.isoformat()
    assert window.window_start == expected['window_start']


def test_missing_observation_does_not_invent_a_live_curve(monkeypatch):
    from backend.api import codex_setup as api

    monkeypatch.setattr(api, 'read_hahamodel_history', lambda: hours((at(29, 10), 30.0)))
    response = api._hahamodel_response({'configured': True, 'available': True}, '')
    assert response.weekly_window is None


def test_browser_worker_timeout_preserves_snapshot_and_releases_lock(monkeypatch, tmp_path):
    import subprocess
    monkeypatch.setattr(haha, 'get_hahamodel_session_path', lambda: tmp_path / 'session.json')
    def timeout(*args, **kwargs):
        assert kwargs['timeout'] == 90
        raise subprocess.TimeoutExpired('worker', 90)
    monkeypatch.setattr(haha.subprocess, 'run', timeout)
    with pytest.raises(haha.HahaModelError, match='超时'):
        haha.fetch_hahamodel_browser_account(start=at(28), end=at(31))
    with haha.FileLock(str(tmp_path / 'session.browser.lock'), timeout=0):
        pass


@pytest.mark.parametrize('fail_details', [False, True])
@pytest.mark.parametrize('existing_page', [False, True])
@pytest.mark.parametrize('expired', [False, True, 'persistent'])
def test_browser_worker_reuses_page_and_cleans_connections(monkeypatch, fail_details, existing_page, expired):
    import sys
    from types import SimpleNamespace
    from backend.core import hahamodel_browser as worker
    monkeypatch.setattr('backend.core.dp_browser.resolve_dp_browser_debug_address', lambda: '127.0.0.1:9333')
    events = []
    def restore(tab):
        events.append('login')
    monkeypatch.setattr(worker, 'restore_browser_login', restore)
    class Options:
        def __init__(self, **kw): pass
        def set_address(self, value):
            assert value == '127.0.0.1:9333'
            return self
        def existing_only(self):
            events.append('existing_only')
            return self
    class Listener:
        calls = 0
        def start(self, target): events.append('listen')
        def wait(self, **kw):
            self.calls += 1
            if expired and (self.calls == 1 or expired == 'persistent'):
                return SimpleNamespace(response=SimpleNamespace(status=401))
            return SimpleNamespace(response=SimpleNamespace(status=200, body={
                'success': True, 'data': {'id': 346, 'used_quota': 10}}),
                request=SimpleNamespace(headers={'Authorization': 'secret'}))
        def stop(self): events.append('stop')
    class Tab:
        url = 'https://hahamodel.com/profile'
        listen = Listener()
        def refresh(self): events.append('refresh')
        def get(self, url):
            assert url == self.url
            events.append('navigate')
        def run_js(self, js, path, headers):
            assert headers == {'Authorization': 'secret'}
            if fail_details:
                raise RuntimeError('network failure')
            return {'status': 200, 'body': {'success': True, 'data': []}}
        def disconnect(self): events.append('disconnect')
        def close(self): events.append('close')
    def new_tab():
        events.append('new_tab')
        return Tab()
    browser = SimpleNamespace(get_tabs=lambda **kw: [Tab()] if existing_page else [], new_tab=new_tab)
    monkeypatch.setitem(sys.modules, 'DrissionPage', SimpleNamespace(
        Chromium=lambda options: browser, ChromiumOptions=Options))
    if fail_details or expired == 'persistent':
        with pytest.raises(RuntimeError):
            worker.fetch_browser_account(start=at(28), end=at(31))
    else:
        assert worker.fetch_browser_account(start=at(28), end=at(31))['profile']['id'] == 346
    expected = (['listen', 'refresh', 'stop', 'disconnect'] if existing_page
                else ['new_tab', 'listen', 'navigate', 'stop', 'close'])
    if expired:
        expected[-2:-2] = ['login', 'listen', 'navigate']
    assert events == ['existing_only', *expected]


def test_key_rotation_updates_mask_without_changing_account_history(monkeypatch):
    from backend.api import codex_setup as api

    observed = at(30, 9)
    history = hours((at(29, 10), 30.0))
    monkeypatch.setattr(api, 'read_hahamodel_history', lambda: history)
    token = {'token': 'sk-old-secret-credential-1111'}
    monkeypatch.setattr(api, 'read_hahamodel_token_config', lambda: token)
    payload = {'available': True, 'source': 'account', 'used_total': 1234.0}
    old = api._hahamodel_response(payload, observed.isoformat(), now=observed)
    token['token'] = 'sk-new-secret-credential-2222'
    new = api._hahamodel_response(payload, observed.isoformat(), now=at(30, 12))
    assert old.active_key == 'sk-old-…1111' and new.active_key == 'sk-new-…2222'
    assert not old.stale and new.stale
    assert old.weekly_window == new.weekly_window
    assert new.used_total == 1234.0
    assert token['token'] not in new.model_dump_json()


def test_monday_refresh_rolls_account_window_without_losing_previous_week(monkeypatch, tmp_path):
    from backend.api import codex_setup as api

    monkeypatch.setattr(haha, 'get_hahamodel_snapshot_path', lambda: tmp_path / 'snapshot.json')
    monkeypatch.setattr(haha, 'get_hahamodel_history_path', lambda: tmp_path / 'hours.json')
    history = hours((at(27, 23), 30.0), (at(28, 1), 2.0))
    monkeypatch.setattr(haha, 'fetch_hahamodel_browser_account', lambda **kw: {
        'status': {'quota_per_unit': 500000},
        'profile': {'quota': 100000000, 'used_quota': 16000000},
        'hourly': [{'created_at': int(k), 'quota': v * 500000} for k, v in history.items()],
    })
    now = at(28, 2)
    result = haha.collect_hahamodel_usage_snapshot(now=now)
    response = api._hahamodel_response(result['payload'], result['observed_at'], now=now)
    previous, running = response.weekly_window.weeks
    assert previous.used == 30.0 and previous.complete
    assert running.week_start == at(28).isoformat() and running.used == 2.0
    assert response.weekly_window.window_end == at(35).isoformat()
    assert not response.stale


def test_account_to_key_transition_never_anchors_different_counters(monkeypatch, tmp_path):
    monkeypatch.setattr(haha, 'get_hahamodel_snapshot_path', lambda: tmp_path / 'snapshot.json')
    monkeypatch.setattr(haha, 'get_hahamodel_token_history_path', lambda: tmp_path / 'token.json')
    haha.save_hahamodel_snapshot({'available': True, 'used_total': 3125.0, 'source': 'account'}, at(30))
    monkeypatch.setattr(haha, 'read_hahamodel_usage', lambda **kw: {
        'available': True, 'source': 'token', 'source_id': 'new-key', 'used_total': 0.2})
    haha.collect_hahamodel_usage_snapshot(now=at(31))
    assert 'baseline' not in haha._read_json(tmp_path / 'token.json')


def test_scheduled_failure_is_visible_without_replacing_counters(monkeypatch, tmp_path):
    path = tmp_path / 'snapshot.json'
    monkeypatch.setattr(haha, 'get_hahamodel_snapshot_path', lambda: path)
    monkeypatch.setattr(haha, 'get_hahamodel_history_path', lambda: tmp_path / 'hours.json')
    haha.save_hahamodel_snapshot({'available': True, 'source': 'account', 'used_total': 123}, at(30))
    monkeypatch.setattr(haha, 'read_hahamodel_usage', lambda **kw: {
        'available': False, 'error': '登录已过期'})
    haha.collect_hahamodel_usage_snapshot(now=at(31))
    cached = haha.load_hahamodel_snapshot()
    assert cached['observed_at'] == at(30).isoformat()
    assert cached['payload']['used_total'] == 123
    assert cached['payload']['error'] == '登录已过期'
    monkeypatch.setattr(haha, 'read_hahamodel_usage', lambda **kw: {
        'available': True, 'source': 'account', 'used_total': 130,
        'hourly': {}, 'fetch_start': at(28).isoformat(), 'error': ''})
    haha.collect_hahamodel_usage_snapshot(now=at(31, 1))
    recovered = haha.load_hahamodel_snapshot()
    assert recovered['observed_at'] == at(31, 1).isoformat()
    assert recovered['payload']['used_total'] == 130
    assert recovered['payload']['error'] == ''


def test_worker_auth_diagnostic_is_safe_and_actionable(monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr(haha, 'get_hahamodel_session_path', lambda: tmp_path / 'session.json')
    monkeypatch.setattr(haha.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout='{"error_code":"authentication_expired"}'))
    with pytest.raises(haha.HahaModelError, match='自动登录后会话仍失效'):
        haha.fetch_hahamodel_browser_account(start=at(28), end=at(31))


@pytest.mark.parametrize('failure', ['profile_timeout', 'authentication_expired'])
def test_profile_failure_classification(monkeypatch, failure):
    from types import SimpleNamespace
    from backend.core import hahamodel_browser as worker
    tab = SimpleNamespace(url='https://hahamodel.com/sign-in' if failure == 'authentication_expired'
                          else 'https://hahamodel.com/profile',
                          listen=SimpleNamespace(wait=lambda **kw: None))
    with pytest.raises(worker.BrowserCollectionError) as exc:
        worker.read_profile_packet(tab)
    assert exc.value.code == failure


@pytest.mark.parametrize('result', ['success', 'rejected', 'timeout', '2fa', 'missing'])
def test_browser_login_submission_and_verification(monkeypatch, result):
    from types import SimpleNamespace
    from backend.core import hahamodel_browser as worker
    events = []
    monkeypatch.setattr(haha, 'read_hahamodel_credentials', lambda: ('u', 'p') if result != 'missing' else ('', ''))
    body = {'success': result != 'rejected', 'data': {'require_2fa': result == '2fa'}}
    packet = None if result == 'timeout' else SimpleNamespace(response=SimpleNamespace(status=200, body=body))
    class Field:
        def input(self, value): events.append('input')
        def click(self): events.append('submit')
    tab = SimpleNamespace(url='https://hahamodel.com/sign-in',
        get=lambda url: events.append('navigate'), ele=lambda *a, **kw: Field(),
        listen=SimpleNamespace(start=lambda path: events.append('listen'), wait=lambda **kw: packet),
        wait=SimpleNamespace(url_change=lambda *a, **kw: events.append('persisted') or True))
    if result == 'success':
        worker.restore_browser_login(tab)
        assert events == ['navigate', 'input', 'input', 'listen', 'submit', 'persisted']
    else:
        code = {'rejected': 'login_rejected', 'timeout': 'login_timeout',
                '2fa': 'login_verification_required', 'missing': 'credentials_missing'}[result]
        with pytest.raises(worker.BrowserCollectionError) as exc:
            worker.restore_browser_login(tab)
        assert exc.value.code == code
        assert 'persisted' not in events


def test_server_error_on_sign_in_does_not_trigger_login():
    from types import SimpleNamespace
    from backend.core import hahamodel_browser as worker
    tab = SimpleNamespace(url='https://hahamodel.com/sign-in', listen=SimpleNamespace(
        wait=lambda **kw: SimpleNamespace(response=SimpleNamespace(status=503))))
    with pytest.raises(worker.BrowserCollectionError) as exc:
        worker.read_profile_packet(tab)
    assert exc.value.code == 'profile_timeout'
