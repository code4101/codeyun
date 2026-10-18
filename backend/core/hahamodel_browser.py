"""Read the account through its existing DP page; never create a login.

Run in a short-lived child process so all CDP sockets and threads are released
on success, failure or timeout. The browser and its durable login stay open.
Only account data leaves this process; authentication headers stay in memory.
"""
from __future__ import annotations

import datetime as dt
import json
from urllib.parse import urlparse


def fetch_browser_account(*, start: dt.datetime, end: dt.datetime) -> dict:
    from DrissionPage import Chromium, ChromiumOptions

    browser = Chromium(ChromiumOptions(read_file=False).set_address('127.0.0.1:9222').existing_only())
    matches = browser.get_tabs(url='https://hahamodel.com/profile')
    tabs = [tab for tab in matches if urlparse(tab.url).hostname == 'hahamodel.com'
            and urlparse(tab.url).path.rstrip('/') == '/profile']
    if not tabs:
        raise RuntimeError('请在常驻 DP 浏览器中打开并登录 https://hahamodel.com/profile')
    tab = tabs[0]
    try:
        tab.listen.start('/api/user/self')
        tab.refresh()
        packet = tab.listen.wait(timeout=15)
        if not packet or packet.response.status != 200:
            raise RuntimeError('DP 账户页面登录失效或读取超时，请在原页面重新登录')
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
            tab.disconnect()


if __name__ == '__main__':
    import sys
    try:
        result = fetch_browser_account(start=dt.datetime.fromisoformat(sys.argv[1]),
                                       end=dt.datetime.fromisoformat(sys.argv[2]))
        print(json.dumps({'account': result}, ensure_ascii=True))
    except Exception:
        # Never print browser exceptions or packets: they can contain credentials.
        print(json.dumps({'error': 'DP 账户采集失败，请检查常驻浏览器中的 hahamodel 登录页面'}, ensure_ascii=True))
        sys.exit(1)
