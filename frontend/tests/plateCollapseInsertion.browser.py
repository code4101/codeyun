"""Insertion must be visible without a pre-existing caret, even in a long body.
uv run --with playwright python frontend/tests/plateCollapseInsertion.browser.py
"""
import asyncio
import re
import urllib.request
from playwright.async_api import async_playwright, expect


async def main():
    source = urllib.request.urlopen('http://localhost:5173/src/components/PlateEditor.vue').read().decode()
    vue = re.search(r'"([^" ]*/deps/vue.js[^" ]*)"', source).group(1)
    html = '''<div id="app" style="height:550px;width:400px"></div><script type="module">
    import {createApp,h,ref} from 'VUE_URL';
    import Editor from '/src/components/PlateEditor.vue';
    const content=ref(''),generation=ref(0);
    window.load=value=>{content.value=JSON.stringify({schema:'codeyun.plate',version:1,value});generation.value++};
    window.load(Array.from({length:40},(_,i)=>({type:'p',children:[{text:'Paragraph '+i+' text '.repeat(20)}]})));
    createApp({setup:()=>()=>h(Editor,{key:generation.value,modelValue:content.value,
      onChange:v=>{window.saved=v}})}).mount('#app');</script>'''.replace('VUE_URL', vue)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            page = await browser.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.route('**/__collapse-insertion-test', lambda r: r.fulfill(content_type='text/html', body=html))
            await page.goto('http://localhost:5173/__collapse-insertion-test')
            frame = page.frame_locator('iframe')
            button = frame.get_by_role('button', name='插入折叠块', exact=True)
            await button.click()
            block = frame.locator('.codeyun-collapse')
            await expect(block).to_have_count(1)
            await expect(block).to_be_in_viewport()
            await page.keyboard.type('inside new block')
            await expect(block).to_contain_text('inside new block')
            print('PASS long document: visible insertion and correct typing destination', flush=True)
            await page.evaluate("window.load([{type:'p',children:[{text:''}]}])")
            await button.click()
            await expect(block).to_be_in_viewport()
            await page.keyboard.type('empty document block')
            await expect(block).to_contain_text('empty document block')
            assert not errors, errors
            print('PASS empty document insertion', flush=True)
        finally:
            await browser.close()


asyncio.run(main())
