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


def test_missing_credentials_report_not_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(haha, 'read_hahamodel_token_config', lambda: {})
    monkeypatch.delenv(haha.HAHAMODEL_USERNAME_ENV, raising=False)
    monkeypatch.delenv(haha.HAHAMODEL_PASSWORD_ENV, raising=False)
    monkeypatch.setattr(haha, "ROOT_DIR", tmp_path)
    payload = haha.read_hahamodel_usage(now=at(31))
    assert payload["configured"] is False and not payload["available"]


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


def test_token_failure_does_not_attempt_password_login(monkeypatch):
    monkeypatch.setattr(haha, 'read_hahamodel_token_config', lambda: {'token': 'test-key'})
    def fail(config):
        raise haha.requests.RequestException('secret request information')
    monkeypatch.setattr(haha, 'fetch_hahamodel_token_usage', fail)
    monkeypatch.setattr(haha, 'read_hahamodel_credentials', lambda: pytest.fail('must not log in'))
    result = haha.read_hahamodel_usage()
    assert result['available'] is False
    assert 'secret' not in result['error']


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
