"""Forward the two Dots OAuth discovery URLs through yun's existing API tunnel.

Only exact metadata URLs are added. Save the original nginx config, validate
before reload, and restore it on validation failure. No backend/data deployment.
"""
from pathlib import Path
import shlex
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

REMOTE = '''
from pathlib import Path
import subprocess, time
p=Path('/etc/nginx/sites-available/code4101.com')
original=p.read_text()
marker='# CodeYun Dots OAuth discovery'
if marker in original:
    print('already_configured')
else:
    anchor='    location /api {'
    assert original.count(anchor)==1, 'Unexpected nginx topology'
    additions='    '+marker+'\\n'
    for route in ('/.well-known/oauth-protected-resource/api/wechat-dots/mcp',
                  '/.well-known/oauth-authorization-server/api/wechat-dots/oauth'):
        additions+='    location = '+route+' {\\n'
        additions+='        proxy_pass http://127.0.0.1:8000;\\n'
        additions+='        proxy_set_header Host $host;\\n'
        additions+='        proxy_set_header X-Forwarded-Proto $scheme;\\n'
        additions+='    }\\n'
    backup=p.with_name(p.name+'.dots-backup-'+str(int(time.time())))
    backup.write_text(original)
    p.write_text(original.replace(anchor, additions+anchor))
    checked=subprocess.run(['nginx','-t'],capture_output=True,text=True)
    if checked.returncode:
        p.write_text(original)
        raise RuntimeError('nginx validation failed; original restored: '+checked.stderr)
    reloaded=subprocess.run(['systemctl','reload','nginx'],capture_output=True,text=True)
    if reloaded.returncode:
        p.write_text(original)
        raise RuntimeError('nginx reload failed; original restored: '+reloaded.stderr)
    print('configured; backup='+str(backup))
'''


def main():
    from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable
    ensure_attendance_engine_importable()
    import xlproject.loadenv
    import paramiko
    from pyxllib.prog.xlenv import XlHosts
    link=XlHosts.find_link('xlpr0')
    password=XlHosts.find_passwd('xlpr0','ssh','chenkunze')
    client=paramiko.SSHClient(); client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(link['ip'],port=XlHosts.find_locator(link,'ssh'),username='chenkunze',password=password,
                   timeout=15,allow_agent=False,look_for_keys=False)
    try:
        stdin,stdout,stderr=client.exec_command("sudo -S -p '' python3 -c "+shlex.quote(REMOTE),timeout=30)
        stdin.write(password+'\n'); stdin.flush(); stdin.channel.shutdown_write()
        output=stdout.read().decode(); error=stderr.read().decode()
        if stdout.channel.recv_exit_status():
            raise RuntimeError('Dots proxy configuration failed: '+error)
        print(output.strip())
    finally:
        client.close()


if __name__=='__main__':
    main()
