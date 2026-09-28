"""uv run --with playwright python frontend/tests/plateImages.browser.py

Exercises the shared PG/notes body editor against built assets and Vite.
Uses an isolated host and in-memory values; never changes user documents.
"""
import asyncio
import base64
import re
import urllib.request
from playwright.async_api import async_playwright

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII='

async def main():
    source = urllib.request.urlopen('http://localhost:5173/src/components/PlateEditor.vue').read().decode()
    vue = re.search(r'"([^" ]*/deps/vue.js[^" ]*)"', source).group(1)
    html = """<div id="app"></div><script type="module">
    import {createApp,h,ref} from 'VUE_URL';
    import Editor from '/src/components/PlateEditor.vue';
    window.saved=JSON.stringify({schema:'codeyun.plate',version:1,value:[{type:'p',children:[{text:'Images'}]}]});
    createApp({setup(){const generation=ref(0),readonly=ref(false);return()=>h('div',[
      h('button',{onClick:()=>generation.value++},'reload'),
      h('button',{onClick:()=>readonly.value=true},'readonly'),
      h(Editor,{key:generation.value,modelValue:window.saved,readOnly:readonly.value,
        onChange:value=>window.saved=value})
    ])}}).mount('#app');</script>""".replace('VUE_URL', vue)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            page = await browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/__plate-images-test', lambda r: r.fulfill(content_type='text/html', body=html))
            await page.goto('http://localhost:5173/__plate-images-test')
            frame = page.frame_locator('iframe')
            body = frame.locator('[role=textbox]')
            await body.wait_for(timeout=60000)
            await body.locator('[data-slate-string]').first.click()
            async with page.expect_file_chooser() as chooser:
                await frame.get_by_role('button', name='插入图片', exact=True).click()
            await (await chooser.value).set_files({'name':'test.png','mimeType':'image/png','buffer':base64.b64decode(PNG)})
            await frame.locator('img[src^="data:image/png;base64,"]').wait_for()
            await page.wait_for_function('window.saved.includes("data:image/png;base64,")')
            previous_frame = await page.locator('iframe').get_attribute('src')
            await page.get_by_role('button', name='reload', exact=True).click()
            await page.wait_for_function('previous => document.querySelector("iframe")?.getAttribute("src") !== previous', arg=previous_frame)
            await frame.locator('img[src^="data:image/png;base64,"]').wait_for()
            await body.locator('[data-slate-string]').first.click()
            await body.evaluate("""(element, data) => {
              const bytes=Uint8Array.from(atob(data),c=>c.charCodeAt(0));
              const clipboardData=new DataTransfer();
              clipboardData.items.add(new File([bytes],'paste.png',{type:'image/png'}));
              element.dispatchEvent(new ClipboardEvent('paste',{clipboardData,bubbles:true,cancelable:true}));
            }""", PNG)
            try:
                await page.wait_for_function('(window.saved.match(/data:image\\/png;base64,/g)||[]).length===2')
            except Exception:
                print(await page.evaluate('window.saved'), errors)
                raise
            await page.get_by_role('button', name='readonly', exact=True).click()
            await frame.get_by_role('button', name='插入图片', exact=True).wait_for(state='hidden')
            assert not errors, errors
            print('PASS: file picker and image paste persist Base64 in body; remount restores images')
        finally:
            await browser.close()

asyncio.run(main())
