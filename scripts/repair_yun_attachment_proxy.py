"""Repair attachment routing on yun without publishing or replacing the frontend.

Back up the exact Nginx site, compare before installation, validate, and reload.
Only the two public attachment URL prefixes are forwarded to the local backend.
"""
import hashlib
import shlex
import time


def attachment_proxy_config(config: str) -> str:
    missing = [p for p in ('/static/attachments/', '/static/uploads/')
               if f'location ^~ {p}' not in config and f'location {p}' not in config]
    if not missing:
        return config
    anchor = '    location /api {'
    if config.count(anchor) != 1:
        raise ValueError('Expected exactly one CodeYun API upstream block')
    blocks = ''.join(f'''    location ^~ {prefix} {{
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}

''' for prefix in missing)
    return config.replace(anchor, blocks + anchor)


def main():
    import xlproject.loadenv  # noqa: F401
    import paramiko
    from pyxllib.prog.xlenv import XlHosts
    link = XlHosts.find_link('xlpr0')
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(link['ip'], port=XlHosts.find_locator(link, 'ssh'),
                   username='chenkunze', password=XlHosts.find_passwd('xlpr0', 'ssh', 'chenkunze'),
                   timeout=20, allow_agent=False, look_for_keys=False)
    site = '/etc/nginx/sites-available/code4101.com'
    try:
        with client.open_sftp() as sftp:
            with sftp.open(site, 'rb') as f:
                original = f.read()
            updated = attachment_proxy_config(original.decode()).encode()
            if updated == original:
                print('Attachment proxy already configured')
                return
            stamp = time.strftime('%Y%m%d%H%M%S')
            staged = f'/tmp/codeyun-attachment-proxy-{stamp}.conf'
            backup = site + '.backup-attachments-' + stamp
            with sftp.open(staged, 'wb') as f:
                f.write(updated)
        # Fixed paths, pre-install checksum, rollback on either validation or reload failure.
        expected = hashlib.sha256(original).hexdigest()
        command = f'''set -eu
test "$(sha256sum {shlex.quote(site)} | cut -d ' ' -f 1)" = {shlex.quote(expected)}
sudo -n cp {shlex.quote(site)} {shlex.quote(backup)}
sudo -n cp {shlex.quote(staged)} {shlex.quote(site)}
if sudo -n nginx -t && sudo -n systemctl reload nginx; then
    echo {shlex.quote('Configured; backup: ' + backup)}
else
    sudo -n cp {shlex.quote(backup)} {shlex.quote(site)}
    sudo -n nginx -t && sudo -n systemctl reload nginx
    exit 1
fi
'''
        stdin, out, err = client.exec_command("sudo -S -p '' sh -c " + shlex.quote(command), timeout=30)
        stdin.write(XlHosts.find_passwd('xlpr0', 'ssh', 'chenkunze') + '\n')
        stdin.flush()
        stdin.channel.shutdown_write()
        stdout, stderr = out.read().decode(), err.read().decode()
        code = out.channel.recv_exit_status()
        print(stdout.strip())
        if code:
            raise RuntimeError(stderr)
        print(stderr.strip())
    finally:
        client.close()


if __name__ == '__main__':
    main()
