"""uv run --with playwright python frontend/tests/plateCollapse.browser.py

Isolated shared-editor acceptance against Vite and the built PG runtime.
Uses only in-memory fixture documents; never opens or changes a user's note/PRG.
"""
import asyncio
import base64
import json
import re
import tempfile
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright, expect

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII='


async def main():
    source = urllib.request.urlopen('http://localhost:5173/src/components/PlateEditor.vue').read().decode()
    vue = re.search(r'"([^" ]*/deps/vue.js[^" ]*)"', source).group(1)
    html = """<div id="app" style="height:850px"></div><script type="module">
    import {createApp,h,ref} from 'VUE_URL';
    import Editor from '/src/components/PlateEditor.vue';
    window.saved=JSON.stringify({schema:'codeyun.plate',version:1,value:
      ['before','wrap A','wrap B','after'].map(text=>({type:'p',children:[{text}]}))});
    window.changes=0;
    createApp({setup(){const generation=ref(0),readonly=ref(false);return()=>h('div',{style:'height:100%'},[
      h('button',{onClick:()=>generation.value++},'reload'),
      h('button',{onClick:()=>readonly.value=true},'readonly'),
      h(Editor,{key:generation.value,modelValue:window.saved,readOnly:readonly.value,
        onChange:value=>{window.saved=value;window.changes++}})
    ])}}).mount('#app');</script>""".replace('VUE_URL', vue)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            page = await browser.new_page(viewport={'width': 1200, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/__plate-collapse-test', lambda r: r.fulfill(content_type='text/html', body=html))
            await page.goto('http://localhost:5173/__plate-collapse-test')
            frame = page.frame_locator('iframe')
            body = frame.locator('[data-slate-editor]')
            await body.wait_for(timeout=60000)
            # Select complete siblings through the DOM, just as mouse selection does.
            await body.evaluate("""el=>{
              el.focus();
              const nodes=[...el.querySelectorAll('[data-slate-string]')];
              const a=nodes.find(n=>n.textContent==='wrap A').firstChild;
              const b=nodes.find(n=>n.textContent==='wrap B').firstChild;
              const range=document.createRange();range.setStart(a,0);range.setEnd(b,b.length);
              const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
              document.dispatchEvent(new Event('selectionchange'));
            }""")
            await expect(body).to_be_focused()
            await frame.get_by_role('button', name='插入折叠块', exact=True).click()
            outer = body.locator('.codeyun-collapse').first
            await expect(outer).to_contain_text('wrap A')
            await expect(outer).to_contain_text('wrap B')
            await page.wait_for_function("JSON.parse(window.saved).value[1].type==='codeyun-collapse'")
            value = json.loads(await page.evaluate('window.saved'))['value']
            assert len(value) == 3 and len(value[1]['children']) == 2, value
            # The structural wrap is one undo operation, not many partial changes.
            await body.press('Control+z')
            await expect(body.locator('.codeyun-collapse')).to_have_count(0)
            await body.press('Control+Shift+z')
            await expect(body.locator('.codeyun-collapse')).to_have_count(1)
            title = outer.get_by_role('textbox', name='折叠块标题', exact=True)
            await title.fill('外层标题')
            await title.press('Enter')
            await expect(body).to_be_focused()
            # Backspace at the first child must not destroy the container boundary.
            await body.press('Backspace')
            await expect(body.locator('.codeyun-collapse')).to_have_count(1)
            # Insert a nested container into the exact same editable tree.
            await frame.get_by_role('button', name='插入折叠块', exact=True).click()
            nested = outer.locator('.codeyun-collapse')
            await expect(nested).to_have_count(1)
            await nested.get_by_role('textbox', name='折叠块标题').fill('内层标题')
            await nested.get_by_role('textbox', name='折叠块标题').press('Enter')
            await expect(body).to_be_focused()
            await page.keyboard.type('recursive body')
            await expect(nested).to_contain_text('recursive body')
            async with page.expect_file_chooser() as chooser:
                await frame.get_by_role('button', name='插入图片', exact=True).click()
            await (await chooser.value).set_files({'name': 'nested.png', 'mimeType': 'image/png', 'buffer': base64.b64decode(PNG)})
            try:
                await expect(nested.locator('img')).to_have_count(1)
            except AssertionError:
                print('image insertion value:', await page.evaluate('window.saved'), errors)
                raise
            await page.wait_for_function('window.saved.includes("data:image/png;base64,")')
            # The existing table menu must insert into the nested body too.
            await nested.get_by_role('textbox', name='折叠块标题').press('Enter')
            await body.press('End')
            await body.press('Enter')
            await frame.locator('button:has(svg.lucide-plus)').first.click()
            await frame.get_by_role('menuitem', name='Table', exact=True).click()
            await expect(nested.locator('table')).to_have_count(1)
            await nested.locator('td[data-slate-node="element"]').first.click()
            await page.keyboard.type('nested table')
            await expect(nested.locator('table')).to_contain_text('nested table')
            # Closing an ancestor preserves all child values, including media.
            await outer.locator(':scope > .codeyun-collapse-heading').get_by_role('button', name='收起折叠块', exact=True).click()
            await expect(nested).to_be_hidden()
            await page.wait_for_function('JSON.parse(window.saved).value[1].collapsed===true')
            saved = json.loads(await page.evaluate('window.saved'))
            old_src = await page.locator('iframe').get_attribute('src')
            await page.get_by_role('button', name='reload', exact=True).click()
            await page.wait_for_function('src=>document.querySelector("iframe").getAttribute("src")!==src', arg=old_src)
            await expect(outer.get_by_role('textbox', name='折叠块标题').first).to_have_value('外层标题')
            await expect(nested).to_be_hidden()
            await outer.get_by_role('button', name='展开折叠块', exact=True).click()
            await expect(nested.locator('img')).to_have_count(1)
            await expect(nested).to_contain_text('recursive body')
            await expect(nested.locator('table')).to_contain_text('nested table')
            assert saved['value'][1]['children'][1]['type'] == 'codeyun-collapse', saved
            # Explicit exit puts the caret outside this container; unwrapping
            # retains both title and body and can itself be undone.
            await nested.get_by_role('button', name='取消折叠，保留正文', exact=True).click()
            await expect(outer.locator('.codeyun-collapse')).to_have_count(0)
            await expect(outer).to_contain_text('内层标题')
            await expect(outer.locator('img')).to_have_count(1)
            await body.press('Control+z')
            await expect(nested).to_have_count(1)
            await nested.get_by_role('textbox', name='折叠块标题').press('Enter')
            await body.press('Control+Enter')
            await body.press_sequentially('outside nested')
            await expect(outer).to_contain_text('outside nested')
            await expect(nested).not_to_contain_text('outside nested')
            screenshot = Path(tempfile.gettempdir()) / 'codeyun' / 'plate-collapse' / 'acceptance.png'
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(screenshot))
            # Read-only expansion is presentation only, with zero persistence.
            await page.get_by_role('button', name='readonly', exact=True).click()
            await frame.get_by_role('button', name='插入折叠块', exact=True).wait_for(state='hidden')
            count = await page.evaluate('window.changes')
            await outer.locator(':scope > .codeyun-collapse-heading').get_by_role('button', name='收起折叠块', exact=True).click()
            await expect(nested).to_be_hidden()
            await outer.get_by_role('button', name='展开折叠块', exact=True).click()
            await expect(nested).to_be_visible()
            assert await page.evaluate('window.changes') == count
            assert not errors, errors
            print('PASS: wrap/undo, recursive editing/images/tables, closed reload, unwrap/undo, exit and readonly expansion')
            print(f'Screenshot: {screenshot}')
        finally:
            await browser.close()


asyncio.run(main())
