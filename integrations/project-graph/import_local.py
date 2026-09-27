"""Import a local PG tree through the authenticated library API.

Run: uv run --with playwright --with msgpack python integrations/project-graph/import_local.py
     --source <directory> --username <owner> [--claim-legacy-browser]
Uses the shipped editor's normal open/upgrade/export protocol in isolated Chrome.
Sources are never modified; same-path names skip before conversion; source bytes
are also retained in the server. No private editor/database APIs are used.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'xlproject' / 'src'))
import xlproject.loadenv  # noqa: E402,F401 -- deployment configuration authority
import msgpack  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402
from backend.core.access.auth import create_local_owner_session  # noqa: E402
from backend.core.project_graph.library import designate_legacy_browser_owner  # noqa: E402
from backend.core.temp_paths import codeyun_temp_root  # noqa: E402


HOST = '''<meta charset="utf-8"><style>html,body,iframe{width:100%;height:100%;margin:0;border:0}</style><iframe></iframe><script>
const frame=document.querySelector('iframe');
const encode=b=>{let s='';for(let i=0;i<b.length;i+=8192)s+=String.fromCharCode(...b.subarray(i,i+8192));return btoa(s)};
let source,session,exporting=false;
window.start=(data)=>{source=data;session=crypto.randomUUID();frame.src='/plugins/project-graph/embed.html?session='+session};
window.addEventListener('message',e=>{
 const m=e.data;
 if(e.source!==frame.contentWindow||e.origin!==location.origin||m?.channel!=='codeyun.project-graph'||m.session!==session)return;
 const send=(type,extra={})=>frame.contentWindow.postMessage({channel:m.channel,version:1,session,type,...extra},location.origin);
 if(m.type==='ready')send('response',{id:m.id,payload:{title:source.title,bytes:Uint8Array.from(atob(source.bytes),c=>c.charCodeAt(0))}});
 if(m.type==='write')send('response',{id:m.id,payload:{revision:1}});
 if(m.type==='status'&&!exporting){exporting=true;send('export')}
 if(m.type==='exported')window.result={bytes:encode(m.payload.bytes)};
 if(m.type==='error')window.result={error:m.payload.message};
});
</script>'''


def inspect_prg(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.testzip():
            raise ValueError('损坏的 PRG 压缩包')
        stage = msgpack.unpackb(archive.read('stage.msgpack'), raw=False, strict_map_key=False)
        metadata = msgpack.unpackb(archive.read('metadata.msgpack'), raw=False, strict_map_key=False) if 'metadata.msgpack' in archive.namelist() else {}
        attachments = sorted(hashlib.sha256(archive.read(n)).hexdigest() for n in archive.namelist() if n.startswith('attachments/') and not n.endswith('/'))
        ids = {str(obj['uuid']) for obj in stage if isinstance(obj, dict) and obj.get('uuid')}
        return {'objects': len(stage), 'version': metadata.get('version', '2.0.0'), 'attachments': attachments, 'ids': ids}


async def convert(browser, origin: str, title: str, data: bytes):
    page = await browser.new_page(viewport={'width':1400,'height':900})
    try:
        await page.route('**/__pg-local-import', lambda route: route.fulfill(content_type='text/html', body=HOST))
        await page.goto(origin + '/__pg-local-import')
        await page.evaluate('data=>window.start(data)', {'title':title, 'bytes':base64.b64encode(data).decode()})
        for _ in range(180):
            result = await page.evaluate('window.result')
            if result:
                if result.get('error'):
                    raise ValueError(result['error'])
                return base64.b64decode(result['bytes'])
            for frame in page.frames:
                if 'embed.html' not in frame.url:
                    continue
                # User explicitly authorized compatibility upgrades; use the
                # normal confirmation, never patch private upgrade methods.
                button = frame.get_by_role('button', name='确认升级', exact=True)
                if await button.count():
                    await button.click()
                if await frame.get_by_text('文件版本过新，无法打开', exact=True).count():
                    raise ValueError('文件来自较新的 PG，未导入')
                if await frame.get_by_text('文件解析失败', exact=True).count():
                    raise ValueError('PG 官方解析器拒绝此文件，未导入')
            await asyncio.sleep(.5)
        raise TimeoutError('PG 转换超时，未导入')
    finally:
        await page.close()


async def run(args):
    source = args.source.resolve(strict=True)
    session = create_local_owner_session(username=args.username)
    def api(path='', body=None):
        request = urllib.request.Request(args.backend.rstrip('/') + '/api/project-graph/files' + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization':'Bearer '+session['access_token'], 'Content-Type':'application/json'})
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.load(response)
    entries = api()['entries']
    folders = {(): 0}
    report = {'owner':args.username, 'source':str(source), 'files':[]}
    report_path = codeyun_temp_root('project-graph-import') / 'report.json'
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel='chrome', headless=True)
        try:
            for path in sorted(source.rglob('*.prg')):
                relative = path.relative_to(source)
                parent = 0
                for i, part in enumerate(relative.parts[:-1]):
                    key = relative.parts[:i+1]
                    if key not in folders:
                        folders[key] = api(body={'title':part,'kind':'folder','parentId':parent,'skipExisting':True})['id']
                    parent = folders[key]
                title = path.stem
                existing = next((r for r in entries if r['parentId'] == parent and r['kind'] == 'document' and r['title'].casefold() == title.casefold()), None)
                item = {'path':str(relative)}
                try:
                    if existing:
                        item.update(id=existing['id'], status='skipped')
                    else:
                        original = path.read_bytes()
                        before = inspect_prg(original)
                        upgraded = await convert(browser, args.url.rstrip('/'), path.name, original)
                        after = inspect_prg(upgraded)
                        if before['ids'] != after['ids'] or before['attachments'] != after['attachments'] or before['objects'] != after['objects']:
                            raise ValueError('转换前后对象或附件不一致，未导入')
                        saved = api(body={'title':title, 'parentId':parent,'skipExisting':True,
                            'content':base64.b64encode(upgraded).decode(), 'original':base64.b64encode(original).decode()})
                        persisted = api('/'+str(saved['id']))
                        if not saved['skipped'] and base64.b64decode(persisted['content']) != upgraded:
                            raise ValueError('保存后校验不一致')
                        entries.append(saved)
                        item.update(id=saved['id'], status='skipped' if saved['skipped'] else 'imported',
                            sourceVersion=before['version'], version=after['version'], objects=after['objects'],
                            attachments=len(after['attachments']), sha256=hashlib.sha256(upgraded).hexdigest())
                except Exception as error:
                    item.update(status='failed', error=str(error))
                report['files'].append(item)
                report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(item,ensure_ascii=False),flush=True)
        finally:
            await browser.close()
    if args.claim_legacy_browser:
        designate_legacy_browser_owner(args.username)
    print('Report:',report_path)
    if any(r['status']=='failed' for r in report['files']):
        raise SystemExit(1)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--username',required=True)
    parser.add_argument('--url',default='http://localhost:5173')
    parser.add_argument('--backend',default='http://127.0.0.1:8000')
    parser.add_argument('--claim-legacy-browser',action='store_true')
    asyncio.run(run(parser.parse_args()))
