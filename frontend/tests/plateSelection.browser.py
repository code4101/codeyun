"""uv run --with playwright python frontend/tests/plateSelection.browser.py

Isolated public Vue/iframe harness. No user documents or server writes.
Compare repeated cold-runtime selection with the retained runtime, and verify
late messages keep their source identity and stale loads cannot replace input.
"""
import asyncio
import json
import re
import statistics
import urllib.request
from playwright.async_api import async_playwright


async def main():
    source = urllib.request.urlopen('http://localhost:5173/src/components/PlateEditor.vue').read().decode()
    vue = re.search(r'"([^" ]*/deps/vue.js[^" ]*)"', source).group(1)
    html = '''<div id="app" style="height:800px"></div><script type="module">
    import {createApp,h,ref} from 'VUE_URL';
    import Details from '/src/plugins/modules/project-graph/NodeDetailsTool.vue';
    const selected=ref('a'), cold=ref(false);
    const nodes=Object.fromEntries(['a','b'].map(id=>[id,{id,title:id,value:[{type:'p',children:[{text:id}]}]}]));
    window.changes=[]; window.loads=[];
    window.select=id=>selected.value=id; window.cold=v=>cold.value=v;
    window.addEventListener('message',e=>{if(e.data.type==='change')window.lastEdit=e.data});
    createApp({setup:()=>()=>h(Details,{key:cold.value?selected.value:'warm',node:nodes[selected.value]??null,
      onChange:(id,value)=>{window.changes.push({id,value});nodes[id].value=value;}})}).mount('#app');
    </script>'''.replace('VUE_URL', vue)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            page = await browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/__plate-selection-test', lambda r: r.fulfill(content_type='text/html', body=html))
            await page.goto('http://localhost:5173/__plate-selection-test')
            frame = page.frame_locator('iframe')
            body = frame.locator('[role=textbox]')
            await body.wait_for(timeout=60000)
            for cold in (True, False):
                await page.evaluate('(v)=>window.cold(v)', cold)
                await body.wait_for(timeout=60000)
                times = []
                for i in range(10):
                    key = 'b' if i % 2 == 0 else 'a'
                    start = asyncio.get_running_loop().time()
                    await page.evaluate('(id)=>window.select(id)', key)
                    await body.get_by_text(key, exact=True).wait_for(timeout=60000)
                    times.append((asyncio.get_running_loop().time() - start) * 1000)
                print(('remount' if cold else 'reuse'), f'{statistics.mean(times):.1f} ± {statistics.stdev(times):.1f} ms (n=10)')
            src = await page.locator('iframe').get_attribute('src')
            await body.click()
            await body.press('ControlOrMeta+a')
            await body.press_sequentially('edited A')
            await page.wait_for_function('window.changes.at(-1)?.value[0].children[0].text==="edited A"')
            old_edit = await page.evaluate('window.lastEdit')
            await page.evaluate('window.select("b")')
            await body.get_by_text('b', exact=True).wait_for()
            # Deliver a message that was queued by A immediately before selection.
            child = next(f for f in page.frames if 'plate.html' in f.url)
            await child.evaluate('(message)=>parent.postMessage(message,location.origin)', old_edit)
            await page.wait_for_function('window.changes.length>=2')
            assert await page.evaluate('window.changes.at(-1).id') == 'a'
            assert await body.inner_text() == 'b'
            await body.click()
            await body.press('ControlOrMeta+a')
            await body.press_sequentially('edited B')
            await page.wait_for_function('window.changes.at(-1)?.id==="b"')
            await page.evaluate('window.select(null)')
            await body.wait_for(state='hidden')
            await page.evaluate('window.select("a")')
            await body.get_by_text('edited A', exact=True).wait_for()
            assert await page.locator('iframe').get_attribute('src') == src
            count = await page.evaluate('window.changes.length')
            # Same identity, but an old load generation: must not write anything.
            await child.evaluate('(message)=>parent.postMessage(message,location.origin)', old_edit)
            await page.wait_for_timeout(200)
            assert await page.evaluate('window.changes.length') == count
            assert not errors, errors
            print('PASS retained iframe, source-scoped late edit, stale-load rejection, deselection and restoration')
        finally:
            await browser.close()


asyncio.run(main())
