"""Against running Vite + built PG assets: verify cold iframe paint and node switches.
Run: uv run --with playwright python frontend/tests/plateTheme.browser.py
Uses an isolated in-memory host, never user documents.
"""
import asyncio
import re
import urllib.request
from playwright.async_api import async_playwright

async def main():
    source = urllib.request.urlopen('http://localhost:5173/src/components/PlateEditor.vue').read().decode()
    vue = re.search(r'"([^" ]*/deps/vue.js[^" ]*)"', source).group(1)
    html = f"""<style>html,body,#app{{height:100%;margin:0}}.host{{height:100%;display:flex;flex-direction:column;--reader-content:#1c2127;--reader-text:#e4e7ed;--reader-panel:#252a32;--reader-border:#414751;background:var(--reader-content)}}</style>
    <div id="app"></div><script type="module">
    import {{createApp,ref,h}} from '{vue}';
    import Tool from '/src/plugins/modules/project-graph/NodeDetailsTool.vue';
    window.changes=[];window.badPaints=[];window.visibleFrames=0;
    createApp({{setup(){{const id=ref('A');return()=>h('div',{{class:'host is-reader-theme-dark'}},[
      h('button',{{onClick:()=>id.value=id.value==='A'?'B':id.value==='B'?'C':'A'}},'switch'),
      h(Tool,{{key:id.value,node:{{id:id.value,title:id.value,value:id.value==='C'?[]:[{{type:'p',children:[{{text:'Body '+id.value}}]}}]}},onChange:(id,value)=>window.changes.push({{id,value}})}})
    ])}}}}).mount('#app');
    function inspect(){{const frame=document.querySelector('iframe');if(frame&&getComputedStyle(frame).visibility==='visible'){{
      window.visibleFrames++;
      const root=frame.contentDocument?.documentElement;
      if(!root||getComputedStyle(root).getPropertyValue('--background').trim()!=='#1c2127'||!frame.contentDocument.querySelector('[contenteditable]'))window.badPaints.push('unthemed or uncommitted frame');
    }}requestAnimationFrame(inspect)}}requestAnimationFrame(inspect);
    </script>"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            page = await browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/__plate-theme-test', lambda route: route.fulfill(content_type='text/html', body=html))
            async def delayed(route):
                await asyncio.sleep(.4)
                await route.continue_()
            await page.route('**/plate.html?*', delayed)
            await page.goto('http://localhost:5173/__plate-theme-test')
            for node in ['A', 'B', 'C', 'A']:
                await page.locator('iframe:not(.blocked)').wait_for(timeout=60000)
                editor = page.frame_locator('iframe').locator('[contenteditable=true]')
                if node == 'C':
                    assert not any(text.strip() for text in await editor.locator('[data-slate-string]').all_text_contents())
                else:
                    await editor.filter(has_text='Body '+node).wait_for()
                await editor.fill('Edited '+node)
                await page.wait_for_function('id => window.changes.at(-1)?.id === id', arg=node)
                assert await page.evaluate('window.changes.at(-1).id') == node
                assert await page.evaluate('window.badPaints') == []
                await page.get_by_role('button', name='switch', exact=True).click()
                await page.locator('iframe.blocked').wait_for(state='attached')
            assert await page.evaluate('window.visibleFrames') > 0
            assert not errors, errors
            print('PASS: delayed cold load and keyed populated/empty node switches never display an unthemed iframe; edits retain node identity')
        finally:
            await browser.close()

asyncio.run(main())
