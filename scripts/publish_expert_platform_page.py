"""发布专家研究页的精确公网路径，并以无登录请求验证内容。

页面仍由本机后端/static提供。只配置一个Nginx精确路径，防止落入SPA权限路由；
不开放其他后端静态文件。配置修改前备份，通过nginx -t才reload，失败自动恢复。
运行：uv run python scripts/publish_expert_platform_page.py
"""
from __future__ import annotations

import hashlib
import json
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'xlproject/src'))


def main() -> None:
    import xlproject.loadenv  # noqa: F401
    import paramiko
    from pyxllib.prog.xlenv import XlHosts
    from scripts.build_expert_platform_page import build_expert_platform_page

    page = build_expert_platform_page()
    expected = hashlib.sha256(page.read_bytes()).hexdigest()
    data = json.loads((ROOT / 'frontend/public/expert-platforms/data.json').read_text(encoding='utf-8'))
    parameters = json.dumps({'sha256': expected, 'xlsx_url': data['xlsx_url']})
    remote = '''
import datetime,hashlib,json,pathlib,subprocess,urllib.request
p=json.loads(PARAMETERS)
config=pathlib.Path('/etc/nginx/sites-available/code4101.com')
original=config.read_text()
block=''' + repr('''    # Public expert platform research page (exact path only).
    location = /static/expert-platforms.html {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        add_header Cache-Control "no-cache" always;
    }

''') + '''
changed=False
if 'location = /static/expert-platforms.html {' not in original:
    marker='    location ^~ /static/attachments/ {'
    assert original.count(marker)==1,'Expected attachments location not found'
    updated=original.replace(marker,block+marker,1)
    backup_dir=pathlib.Path('/etc/nginx/backups/expert-platforms');backup_dir.mkdir(parents=True,exist_ok=True)
    backup=backup_dir/('code4101.com-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.write_text(original)
    config.write_text(updated)
    try:
        subprocess.run(['nginx','-t'],check=True,capture_output=True)
        subprocess.run(['systemctl','reload','nginx'],check=True,capture_output=True)
    except Exception:
        config.write_text(original)
        subprocess.run(['nginx','-t'],check=True,capture_output=True)
        subprocess.run(['systemctl','reload','nginx'],check=True,capture_output=True)
        raise
    changed=True
checks=[]
for url,expected_sha in [('https://code4101.com/static/expert-platforms.html',p['sha256']),(p['xlsx_url'],None)]:
    # No cookies, authorization or session: validates guest access through public HTTPS.
    with urllib.request.urlopen(url,timeout=20) as response:
        content=response.read();sha=hashlib.sha256(content).hexdigest()
        if expected_sha:assert sha==expected_sha,'Public URL served a different page'
        else:assert content.startswith(b'PK'),'Excel URL is not a workbook'
        checks.append({'url':url,'status':response.status,'content_type':response.headers.get_content_type(),'bytes':len(content),'sha256':sha})
print(json.dumps({'changed':changed,'guest_checks':checks}))
'''
    remote = remote.replace('PARAMETERS', repr(parameters))
    connection = XlHosts.find_link('xlpr0')
    password = XlHosts.find_passwd('xlpr0', 'ssh', 'chenkunze')
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(connection['ip'], port=XlHosts.find_locator(connection,'ssh'),
                       username='chenkunze',password=password,timeout=20,allow_agent=False,look_for_keys=False)
        stdin, stdout, stderr = client.exec_command('sudo -S -p "" python3 -c '+shlex.quote(remote),timeout=60)
        stdin.write(password+'\n');stdin.flush();stdin.channel.shutdown_write()
        result, error = stdout.read().decode(), stderr.read().decode()
        if stdout.channel.recv_exit_status()!=0:
            raise RuntimeError(error or 'Public page verification failed')
        print(result.strip())
    finally:
        client.close()


if __name__ == '__main__':
    main()
