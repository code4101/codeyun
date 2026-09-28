"""uv run --with playwright python frontend/tests/plateImageSelection.browser.py

Click real images in the shared editor under light, dark and blue themes.
Only an isolated in-memory document is used; no user documents are changed.
"""
import asyncio
import os
from pathlib import Path
import re
import urllib.request

from playwright.async_api import async_playwright, expect


async def main():
    source = urllib.request.urlopen('http://localhost:5173/src/components/PlateEditor.vue').read().decode()
    vue = re.search(r'"([^" ]*/deps/vue.js[^" ]*)"', source).group(1)
    html = """<style>html,body,#app{height:100%;margin:0}.host{height:100%;display:flex;flex-direction:column}</style>
    <div id="app"></div><script type="module">
    import {createApp,h,ref} from 'VUE_URL';
    import Editor from '/src/components/PlateEditor.vue';
    const image=color=>({type:'img',url:'data:image/svg+xml,'+encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" width="220" height="100"><rect width="220" height="100" fill="${color}"/></svg>`),children:[{text:''}]});
    const value=JSON.stringify({schema:'codeyun.plate',version:1,value:[
      {type:'p',children:[{text:'Click here to deselect'}]},image('#fff'),image('#111')
    ]});
    window.changes=[];
    createApp({setup(){const theme=ref({background:'#fff',text:'#111',dark:false}),readonly=ref(false);
      window.setTheme=next=>theme.value=next;window.setReadonly=next=>readonly.value=next;
      return()=>h('div',{class:['host',{'is-reader-theme-dark':theme.value.dark}],style:{
        '--reader-content':theme.value.background,'--reader-panel':theme.value.background,
        '--reader-text':theme.value.text,'--reader-border':theme.value.text
      }},[h(Editor,{modelValue:value,readOnly:readonly.value,onChange:value=>window.changes.push(value)})]);
    }}).mount('#app');</script>""".replace('VUE_URL', vue)
    output = Path(os.environ['TEMP']) / 'codeyun' / 'plate-image-selection'
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            page = await browser.new_page(viewport={'width': 900, 'height': 700})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/__plate-image-selection-test', lambda r: r.fulfill(content_type='text/html', body=html))
            await page.goto('http://localhost:5173/__plate-image-selection-test')
            await page.locator('iframe:not(.blocked)').wait_for(timeout=60000)
            frame = page.frame_locator('iframe')
            images = frame.locator('[data-slate-editor] img')
            text = frame.get_by_text('Click here to deselect', exact=True)
            await expect(images).to_have_count(2)
            await text.click()
            # Plate normalizes the initial value (for example node IDs) on mount.
            baseline_changes = await page.evaluate('window.changes.length')
            for name, background, foreground, dark in [
                ('light', '#ffffff', '#111111', False),
                ('dark', '#111111', '#ffffff', True),
                ('blue', '#409eff', '#111111', False),
            ]:
                await page.evaluate('theme=>window.setTheme(theme)', {'background':background,'text':foreground,'dark':dark})
                await expect(frame.locator('html')).to_have_css('--background', background)
                for token in ['--primary', '--ring', '--brand']:
                    await expect(frame.locator('html')).to_have_css(token, foreground)
                await expect(frame.locator('html')).to_have_css('--primary-foreground', background)
                for index in range(2):
                    image = images.nth(index)
                    indicator = image.locator('..').locator('[data-image-selection]')
                    await text.click()
                    await expect(frame.locator('[data-image-selection]')).to_have_count(0)
                    before = await image.bounding_box()
                    await image.click()
                    await expect(indicator).to_be_visible()
                    await expect(indicator).to_have_css('outline-color', 'rgb(17, 17, 17)')
                    await expect(indicator).to_have_css('outline-width', '3px')
                    await expect(indicator).to_have_css('box-shadow', 'rgb(255, 255, 255) 0px 0px 0px 2px inset')
                    assert await image.bounding_box() == before, 'Selection must not change layout'
                    assert await indicator.bounding_box() == before, 'Keep the entire ring inside the image'
                    await expect(frame.locator('[data-image-selection]')).to_have_count(1)
                    await page.screenshot(path=str(output / f'{name}-{index}.png'))
            assert await page.evaluate('window.changes.length') == baseline_changes, 'Selection must not save changes'
            await page.evaluate('window.setReadonly(true)')
            await frame.locator('[data-slate-editor][contenteditable=false]').wait_for()
            await expect(frame.locator('[data-image-selection]')).to_have_count(0)
            assert not errors, errors
            print(f'PASS: image selection, deselection, readonly and layout across themes; screenshots: {output}')
        finally:
            await browser.close()


asyncio.run(main())
