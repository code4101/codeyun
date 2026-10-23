"""Read account usage through DP, renewing a rejected login once per collection.

Run in a short-lived child process so all CDP sockets and threads are released
on success, failure or timeout. The browser and its durable login stay open.
Only account data leaves this process; authentication headers stay in memory.
"""
from __future__ import annotations

import datetime as dt
import json
from urllib.parse import urlparse


class BrowserCollectionError(RuntimeError):
    """A safe diagnostic code; browser exceptions and packets stay private."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def restore_browser_login(tab) -> None:
    """Submit configured credentials in the same browser after confirmed expiry.

    Let the site's login handler persist tokens/cookies and finish navigating
    before reading the account. Never login on network errors or server errors.
    The caller's cross-process collection lock serializes session replacement.
    """
    from backend.core.hahamodel_usage import read_hahamodel_credentials

    username, password = read_hahamodel_credentials()
    if not username or not password:
        raise BrowserCollectionError('credentials_missing')
    tab.get('https://hahamodel.com/sign-in')
    field = tab.ele('@name=username', timeout=10)
    if not field or urlparse(tab.url).hostname != 'hahamodel.com':
        raise BrowserCollectionError('login_unavailable')
    field.input(username)
    tab.ele('@name=password').input(password)
    tab.listen.start('/api/user/login')
    tab.ele('tag:button@type=submit').click()
    packet = tab.listen.wait(timeout=15)
    if not packet:
        raise BrowserCollectionError('login_timeout')
    body = packet.response.body
    if packet.response.status != 200 or not isinstance(body, dict) or not body.get('success'):
        raise BrowserCollectionError('login_rejected')
    data = body.get('data') or {}
    if isinstance(data, dict) and data.get('require_2fa'):
        raise BrowserCollectionError('login_verification_required')
    if not tab.wait.url_change('/sign-in', exclude=True, timeout=10):
        raise BrowserCollectionError('login_verification_required')


def read_profile_packet(tab):
    """Return one authenticated profile packet, classifying only safe failures."""
    packet = tab.listen.wait(timeout=15)
    if not packet or packet.response.status != 200:
        signed_out = urlparse(tab.url).path.rstrip('/') in {'/sign-in', '/login'}
        if (not packet and signed_out) or (packet and packet.response.status == 401):
            raise BrowserCollectionError('authentication_expired')
        raise BrowserCollectionError('profile_timeout')
    return packet


def fetch_browser_account(*, start: dt.datetime, end: dt.datetime) -> dict:
    from DrissionPage import Chromium, ChromiumOptions
    from backend.core.dp_browser import resolve_dp_browser_debug_address

    browser = Chromium(ChromiumOptions(read_file=False).set_address(resolve_dp_browser_debug_address()).existing_only())
    matches = browser.get_tabs(url='https://hahamodel.com/profile')
    tabs = [tab for tab in matches if urlparse(tab.url).hostname == 'hahamodel.com'
            and urlparse(tab.url).path.rstrip('/') == '/profile']
    # Cookies belong to the browser profile, not a particular open tab. A
    # closed profile page must not disable hourly collection. Own and close
    # only the temporary tab we create; leave user-owned tabs in place.
    temporary = not tabs
    tab = browser.new_tab() if temporary else tabs[0]
    try:
        tab.listen.start('/api/user/self')
        if temporary:
            tab.get('https://hahamodel.com/profile')
        else:
            tab.refresh()
        try:
            packet = read_profile_packet(tab)
        except BrowserCollectionError as exc:
            if exc.code != 'authentication_expired':
                raise
            restore_browser_login(tab)
            tab.listen.start('/api/user/self')
            tab.get('https://hahamodel.com/profile')
            # One recovery only: rejected credentials or continued expiry fail
            # this attempt instead of creating an unbounded login loop.
            packet = read_profile_packet(tab)
        profile = packet.response.body
        if not isinstance(profile, dict) or not profile.get('success'):
            raise RuntimeError('DP 账户读取失败')
        # Reuse only this page's account authorization, for this exact origin.
        headers = {k: v for k, v in packet.request.headers.items()
                   if k.lower() in {'authorization', 'new-api-user', 'x-auth-session'}}
        def get(path: str):
            response = tab.run_js(
                "return fetch(arguments[0],{headers:arguments[1],credentials:'include',"
                "signal:AbortSignal.timeout(10000)}).then(async r=>"
                "({status:r.status,body:await r.json()}));", path, headers)
            body = response.get('body') or {}
            if response.get('status') != 200 or not body.get('success'):
                raise RuntimeError('DP 账户配置或消耗明细读取失败')
            return body.get('data')

        status = get('/api/status')
        hourly = get(f'/api/data/self?start_timestamp={int(start.timestamp())}'
                     f'&end_timestamp={int(end.timestamp())}')
        return {'status': status or {}, 'profile': profile['data'], 'hourly': hourly or []}
    finally:
        try:
            tab.listen.stop()
        finally:
            if temporary:
                tab.close()
            else:
                tab.disconnect()


if __name__ == '__main__':
    import sys
    try:
        result = fetch_browser_account(start=dt.datetime.fromisoformat(sys.argv[1]),
                                       end=dt.datetime.fromisoformat(sys.argv[2]))
        print(json.dumps({'account': result}, ensure_ascii=True))
    except BrowserCollectionError as exc:
        print(json.dumps({'error_code': exc.code}, ensure_ascii=True))
        sys.exit(1)
    except Exception:
        # Never print browser exceptions or packets: they can contain credentials.
        print(json.dumps({'error': 'DP 账户采集失败，请检查常驻浏览器中的 hahamodel 登录页面'}, ensure_ascii=True))
        sys.exit(1)
