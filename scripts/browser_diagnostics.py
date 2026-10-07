"""管理远端浏览器诊断（默认关闭）。

uv run python scripts/browser_diagnostics.py enable --minutes 10
uv run python scripts/browser_diagnostics.py status
uv run python scripts/browser_diagnostics.py export
uv run python scripts/browser_diagnostics.py disable

开启后自动匹配 UA；隐藏品牌的设备在原链接中添加 browserDebug=qianxin。
启用/关闭均调用公共 API；本机管理会话不输出到终端。
"""
import argparse
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'xlproject' / 'src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['enable', 'disable', 'status', 'export'])
    parser.add_argument('--minutes', type=int, default=10, choices=range(1, 31), metavar='1..30')
    parser.add_argument('--user-agent-contains', action='append', default=[])
    parser.add_argument('--owner', default='code4101')
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    args = parser.parse_args()
    import xlproject.loadenv  # noqa: F401
    from backend.core.access.auth import create_local_owner_session
    from backend.core.temp_paths import codeyun_temp_root
    session = create_local_owner_session(username=args.owner, expires_minutes=5)
    url = args.base_url.rstrip('/') + '/api/browser-diagnostics'
    headers = {'Authorization': 'Bearer ' + session['access_token']}
    body = None
    if args.action in ['enable', 'disable']:
        url += '/control'
        body = json.dumps({'enabled': args.action == 'enable', 'ttl_seconds': args.minutes * 60,
                           'user_agent_contains': args.user_agent_contains}).encode()
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, data=body, headers=headers, method='PUT' if body else 'GET')
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.load(response)
    if args.action == 'export':
        destination = codeyun_temp_root('browser-diagnostics') / f"{result['run_id'] or 'disabled'}.json"
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(destination)
    else:
        result['event_count'] = len(result.pop('events', []))
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
