"""Load and save the shipped PRG editor when native raw DEFLATE is rejected.

uv run --with playwright python frontend/tests/projectGraphZipCompatibility.browser.py [origin]
Uses isolated browser state and in-memory PRG bytes, never user documents.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.core.project_graph.codec import import_prg
from playwright.async_api import async_playwright

HTML = '''<style>html,body,iframe{width:100%;height:100%;margin:0;border:0}</style>
<iframe src="/plugins/project-graph/embed.html?session=zip-test"></iframe><script>
window.errors=[];window.presented=false;window.saved=null;window.exported=null;
addEventListener('message', e=>{
 const m=e.data;if(m.channel!=='codeyun.project-graph'||m.session!=='zip-test')return;
 const respond=payload=>e.source.postMessage({...m,type:'response',payload},location.origin);
 if(m.type==='ready')respond({title:'zip-test.prg',bytes:window.fixture?new Uint8Array(window.fixture):null,readOnly:false});
 if(m.type==='write'){window.saved=Array.from(m.payload.bytes);respond({revision:1});}
 if(m.type==='exported')window.exported=Array.from(m.payload.bytes);
 if(m.type==='presented')window.presented=true;
 if(m.type==='error')window.errors.push(m.payload.message);
});</script>'''
OLD_STREAMS = '''for(const name of ['CompressionStream','DecompressionStream']){
 const Native=globalThis[name];globalThis[name]=class extends Native{
 constructor(format){if(format==='deflate-raw')throw new TypeError("Unsupported compression format: 'deflate-raw'");super(format);}
};}'''


async def main():
    origin = sys.argv[1].rstrip('/') if len(sys.argv) > 1 else 'http://localhost:5173'
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            fixture = None
            for legacy in (False, True):
                context = await browser.new_context(viewport={'width':1024,'height':768})
                if legacy:
                    await context.add_init_script(OLD_STREAMS)
                await context.add_init_script('window.fixture='+json.dumps(fixture))
                page = await context.new_page()
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                await page.route('**/__pg-zip-test', lambda route: route.fulfill(content_type='text/html', body=HTML))
                await page.goto(origin+'/__pg-zip-test')
                await page.wait_for_function('window.presented', timeout=60000)
                frame = page.frame_locator('iframe')
                await frame.locator('canvas').first.dblclick(position={'x':300,'y':200})
                await frame.locator('textarea').fill('ZIP兼容性节点旧内核写入' if legacy else 'ZIP兼容性节点')
                await page.keyboard.press('Escape')
                await page.evaluate("document.querySelector('iframe').contentWindow.postMessage({channel:'codeyun.project-graph',version:1,session:'zip-test',type:'save'},location.origin)")
                await page.wait_for_function('window.saved !== null', timeout=30000)
                fixture = await page.evaluate('window.saved')
                objects = import_prg(bytes(fixture))
                assert 'ZIP兼容性节点' in json.dumps(objects, ensure_ascii=False), objects
                if legacy:
                    assert '旧内核写入' in json.dumps(objects, ensure_ascii=False), objects
                assert not errors and not await page.evaluate('window.errors'), errors
                print(json.dumps({'legacy_raw_deflate':legacy,'presented':True,'saved_bytes':len(fixture),'node_preserved':True}),flush=True)
                await context.close()
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
