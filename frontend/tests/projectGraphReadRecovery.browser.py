"""Read-only acceptance of a real graph after one transient gateway failure.

uv run --with playwright python frontend/tests/projectGraphReadRecovery.browser.py <resource-id> [origin]
All graph mutations are blocked in the isolated browser context.
"""
import asyncio
import json
import runpy
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import xlproject.loadenv
from backend.core.access.auth import create_local_owner_session
from playwright.async_api import async_playwright

OLD_STREAMS = runpy.run_path(str(Path(__file__).with_name('projectGraphZipCompatibility.browser.py')))['OLD_STREAMS']


async def main():
    resource_id = int(sys.argv[1])
    origin = sys.argv[2].rstrip('/') if len(sys.argv)>2 else 'https://code4101.com'
    session = create_local_owner_session(username='code4101',expires_minutes=15)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome',headless=True)
        try:
            context = await browser.new_context(viewport={'width':1440,'height':900})
            await context.add_init_script('localStorage.setItem("token",'+json.dumps(session['access_token'])+');'+OLD_STREAMS)
            page = await context.new_page()
            reads = 0
            blocked = []
            statuses = []
            async def graph_request(route):
                nonlocal reads
                request=route.request
                if request.method!='GET':
                    blocked.append(request.method)
                    await route.abort()
                    return
                if urlsplit(request.url).path==f'/api/project-graph/files/{resource_id}':
                    reads+=1
                    if reads==1:
                        await route.fulfill(status=502,content_type='text/html',body='<h1>Bad Gateway</h1>')
                        return
                await route.continue_()
            await page.route('**/api/project-graph/files**',graph_request)
            page.on('response',lambda r: statuses.append(r.status) if urlsplit(r.url).path==f'/api/project-graph/files/{resource_id}' else None)
            await page.goto(origin+f'/notes/project-graph?doc={resource_id}')
            await page.locator('iframe.project-graph-frame:not(.pending)').wait_for(timeout=90000)
            assert reads>=2 and 502 in statuses and 200 in statuses,(reads,statuses)
            assert not blocked,blocked
            print(json.dumps({'resource_id':resource_id,'legacy_raw_deflate':True,'injected_gateway_502':True,
                'read_attempts':reads,'response_statuses':statuses,'presented':True,'graph_mutations':len(blocked)}),flush=True)
        finally:
            await browser.close()


if __name__=='__main__':
    asyncio.run(main())
