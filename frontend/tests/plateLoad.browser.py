"""Repeated PG-body loads through the public Vue bridge; optional JSON fixture.

uv run --with playwright python frontend/tests/plateLoad.browser.py [body.json]
Never writes a server document. Reports elapsed mean/stddev and unsolicited edits.
"""
import asyncio
import json
from pathlib import Path
import re
import statistics
import sys
import urllib.request
from playwright.async_api import async_playwright


async def main():
    value = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')) if len(sys.argv) > 1 else [
        {'type': 'p', 'children': [{'text': '正文 ' * 100}]} for _ in range(25)
    ]
    source = urllib.request.urlopen('http://localhost:5173/src/components/PlateEditor.vue').read().decode()
    vue = re.search(r'"([^" ]*/deps/vue.js[^" ]*)"', source).group(1)
    html = '''<div id="app" style="height:900px"></div><script type="module">
    import {createApp,h,ref} from 'VUE_URL';
    import Details from '/src/plugins/modules/project-graph/NodeDetailsTool.vue';
    const selected=ref(null);window.loads=0;window.changes=0;
    window.load=(value,id)=>selected.value={id,title:id,value};
    window.addEventListener('message',e=>{if(e.data.type==='presented')window.loads++});
    createApp({setup:()=>()=>h(Details,{node:selected.value,onChange:()=>window.changes++})}).mount('#app');
    </script>'''.replace('VUE_URL', vue)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            page = await browser.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.route('**/__plate-load-test', lambda r: r.fulfill(content_type='text/html', body=html))
            await page.goto('http://localhost:5173/__plate-load-test')
            await page.wait_for_function('window.loads>0')
            times = []
            for i in range(10):
                count = await page.evaluate('window.loads')
                start = asyncio.get_running_loop().time()
                body_value = value if i % 2 == 0 else [{'type': 'p', 'children': [{'text': ''}]}]
                await page.evaluate('([value,id])=>window.load(value,id)', [body_value, str(i)])
                await page.wait_for_function('n=>window.loads>n', arg=count, timeout=10000)
                times.append((asyncio.get_running_loop().time()-start)*1000)
                print(f'load {i}: {times[-1]:.1f} ms', flush=True)
                body = page.frame_locator('iframe').locator('[data-slate-editor]')
                await body.click(position={'x': 12, 'y': 20})
                await body.press('Control+a')
            await page.wait_for_timeout(300)
            print(f'{statistics.mean(times):.1f} ± {statistics.stdev(times):.1f} ms (n=10); '
                  f'unsolicited changes={await page.evaluate("window.changes")}', flush=True)
            assert not errors, errors
        finally:
            await browser.close()


asyncio.run(main())
