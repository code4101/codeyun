"""Isolated graph + body bridge reproduction using a PRG copy and memory storage.
uv run --with playwright python frontend/tests/projectGraphHover.browser.py file.prg
"""
import asyncio
import base64
from collections import Counter
import json
from pathlib import Path
import re
import statistics
import sys
import tempfile
import urllib.request
from playwright.async_api import async_playwright, expect


async def main():
    data = base64.b64encode(Path(sys.argv[1]).read_bytes()).decode()
    source = urllib.request.urlopen('http://localhost:5173/src/components/PlateEditor.vue').read().decode()
    vue = re.search(r'"([^" ]*/deps/vue.js[^" ]*)"', source).group(1)
    html = '''<div id="app"></div><script type="module">
    import {createApp,h,ref} from 'VUE_URL';
    import Graph from '/src/plugins/modules/project-graph/ProjectGraphEditor.vue';
    import Details from '/src/plugins/modules/project-graph/NodeDetailsTool.vue';
    localStorage.setItem('hover-fixture',JSON.stringify({x:-90,y:7260,scale:1}));
    window.boot=(bytes)=>{
      const graph=ref(),details=ref(null),storage={
        read:async()=>({id:'fixture',title:'fixture',bytes:Uint8Array.from(atob(bytes),c=>c.charCodeAt(0)),revision:1}),
        write:async(id,title,bytes,revision)=>({id,title,bytes,revision:revision+1})};
      window.events=[];
      createApp({setup:()=>()=>h('div',{style:'display:flex;height:900px'},[
        h('div',{style:'width:900px;flex-shrink:0'},[h(Graph,{ref:graph,documentId:'fixture',title:'fixture',storage,
          detailsActive:true,viewStateKey:'hover-fixture',onStatus:s=>window.status=s,
          onDetails:v=>{details.value=v;window.events.push({title:v?.title,time:performance.now()})},onError:e=>window.error=e})]),
        h(Details,{node:details.value,onChange:(id,v)=>graph.value.updateDetails(id,v)})])}).mount('#app');
    };</script>'''.replace('VUE_URL', vue)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            page = await browser.new_page(viewport={'width': 1300, 'height': 950})
            page.on('pageerror', lambda e: print('ERROR', str(e), flush=True))
            await page.route('**/__graph-hover-test', lambda r: r.fulfill(content_type='text/html', body=html))
            await page.goto('http://localhost:5173/__graph-hover-test')
            await page.evaluate('v=>window.boot(v)', data)
            await page.wait_for_function('window.status==="saved"', timeout=60000)
            canvas = page.frame_locator('iframe[title="ProjectGraph 编辑器"]').locator('canvas').first
            await canvas.wait_for()
            await page.wait_for_timeout(500)
            folder = Path(tempfile.gettempdir()) / 'codeyun' / 'plate-collapse'
            folder.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(folder/'graph-before-hover.png'))
            cdp = await page.context.new_cdp_session(page)
            await cdp.send('Profiler.enable')
            await cdp.send('Profiler.start')
            print('hovering target', flush=True)
            action = asyncio.create_task(canvas.click(position={'x': 450, 'y': 450}, timeout=10000))
            await asyncio.sleep(2)
            profile = (await cdp.send('Profiler.stop'))['profile']
            (folder/'hover-profile.json').write_text(json.dumps(profile), encoding='utf-8')
            counts = Counter(profile.get('samples', []))
            nodes = {n['id']: n for n in profile['nodes']}
            for node_id, count in counts.most_common(12):
                frame = nodes[node_id]['callFrame']
                print(count, frame['functionName'], frame['url'].split('/')[-1], frame['lineNumber'], frame['columnNumber'], flush=True)
            await action
            print('selection', await page.evaluate('window.events'), flush=True)
            body = page.frame_locator('iframe[title="Plate 正文编辑器"]')
            await expect(body.locator('img')).to_have_count(4)
            times = []
            for i in range(10):
                await canvas.click(position={'x': 100, 'y': 545})
                await expect(body.locator('img')).to_have_count(0)
                start = asyncio.get_running_loop().time()
                await canvas.click(position={'x': 450, 'y': 450})
                await expect(body.locator('img')).to_have_count(4)
                times.append((asyncio.get_running_loop().time()-start)*1000)
            print(f'click-to-body: {statistics.mean(times):.1f} ± {statistics.stdev(times):.1f} ms (n=10)', flush=True)
            await page.screenshot(path=str(folder/'graph-after-hover.png'))
            await body.get_by_role('button', name='插入折叠块', exact=True).click()
            block = body.locator('.codeyun-collapse').last
            await expect(block).to_be_in_viewport()
            await page.keyboard.type('new collapse body')
            await expect(block).to_contain_text('new collapse body')
            await page.screenshot(path=str(folder/'graph-insert-collapse.png'))
            print('PASS unfocused insertion in actual PG/body bridge is visible and editable', flush=True)
        finally:
            await browser.close()


asyncio.run(main())
