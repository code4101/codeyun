"""uv run --with playwright python frontend/tests/projectGraphMenu.browser.py [upstream-checkout]

Public Vue menu/editor bridge with in-memory storage; no user documents are read
or written. Optional checkout verifies the browser-appropriate upstream menu IDs.
"""
import asyncio
import base64
import json
import os
from pathlib import Path
import re
import sys
import urllib.request
from playwright.async_api import async_playwright, expect

async def main():
    source = urllib.request.urlopen('http://localhost:5173/src/components/PlateEditor.vue').read().decode()
    vue = re.search(r'"([^" ]*/deps/vue.js[^" ]*)"', source).group(1)
    html = '''<style>html,body,#app{height:100%;margin:0}.host{height:100%;display:flex;flex-direction:column;--reader-content:#fff;--reader-panel:#f4f4f4;--reader-text:#111;--reader-border:#ddd}</style>
    <div id="app"></div><script type="module">
    import {createApp,h,ref} from 'VUE_URL';
    import Menu from '/src/components/editor-workspace/WorkspaceMenu.vue';
    import Editor from '/src/plugins/modules/project-graph/ProjectGraphEditor.vue';
    import '/node_modules/element-plus/dist/index.css';
    window.errors=[];window.commands=[];window.writes=0;window.doc=undefined;
    const storage={read:async()=>window.doc,write:async(id,title,bytes,revision)=>{
      window.writes++;return window.doc={id,title,bytes,revision:revision+1,updatedAt:Date.now()};}};
    createApp({setup(){const editor=ref(),menu=ref([]),generation=ref(0);
      window.run=id=>editor.value.executeMenu(id);window.flush=()=>editor.value.flush();
      window.reload=()=>generation.value++;window.canvas=()=>editor.value.focusAuxiliary('');
      return()=>h('div',{class:'host'},[
        h(Menu,{items:menu.value,onSelect:window.run,onRefresh:()=>editor.value?.refreshMenu()}),
        h('div',{style:'flex:1;min-height:0'},[h(Editor,{ref:editor,key:generation.value,documentId:'menu-test',title:'menu-test',storage,
          onMenu:items=>{menu.value=items;window.menu=items},onCommand:id=>window.commands.push(id),onError:error=>window.errors.push(error)})])
      ]);
    }}).mount('#app');</script>'''.replace('VUE_URL', vue)
    output = Path(os.environ['TEMP']) / 'codeyun' / 'pg-menu-parity'
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            page = await browser.new_page(viewport={'width':1400,'height':900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/__pg-menu-test', lambda r:r.fulfill(content_type='text/html', body=html))
            await page.goto('http://localhost:5173/__pg-menu-test')
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            menu = await page.evaluate('window.menu')
            assert [item['id'] for item in menu] == ['file','view','actions','settings','ai','window','extensions','about']
            def flatten(items):
                return [node for item in items for node in [item, *flatten(item.get('children') or [])]]
            by_id = {item['id']:item for item in flatten(menu)}
            assert 'canvas-settings' in by_id
            assert not by_id['canvas-setting:showQuickSettingsToolbar']['label'].startswith('✓')
            await page.evaluate("window.run('canvas-setting:showQuickSettingsToolbar')")
            frame = page.frame_locator('iframe')
            await page.frames[1].wait_for_function("JSON.parse(localStorage.getItem('codeyun.pg.settings.settings.json.showQuickSettingsToolbar')) === true")
            await page.evaluate('window.reload()')
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            await page.frames[1].wait_for_function("JSON.parse(localStorage.getItem('codeyun.pg.settings.settings.json.showQuickSettingsToolbar')) === true")
            await page.evaluate("window.run('canvas-setting:showQuickSettingsToolbar')")
            await page.frames[1].wait_for_function("JSON.parse(localStorage.getItem('codeyun.pg.settings.settings.json.showQuickSettingsToolbar')) === false")
            if len(sys.argv) > 1:
                settings = (Path(sys.argv[1]) / 'app/src/core/service/Settings.tsx').read_text(encoding='utf-8')
                settings = settings[settings.index('// ===================== 文件'):settings.index('// ===================== 不稳定版本')]
                expected = set(re.findall(r'id: "([^"]+)"', settings))
                hidden = {'openConfigFolder', 'openCacheFolder', 'openCustomBackupFolder',
                          'openDefaultBackupFolder', 'openExtensionFolder', 'checkoutWindowOpacityMode',
                          'windowOpacityAlphaDecrease', 'windowOpacityAlphaIncrease', 'checkoutProtectPrivacy',
                          'windowOpacitySub'}  # Empty submenu disappears with its desktop-only children.
                assert not (hidden & by_id.keys())
                expected = {id for id in expected if not id.startswith('sep-')} - hidden
                assert expected <= by_id.keys(), f'Missing upstream menus: {expected-by_id.keys()}'
            for id in ['importImages','importSvg','importTextFile','exportSvgAll','exportSvgSelected','openAttachmentsWindow','openAITools','newDraft']:
                assert not by_id[id]['disabled'], id
            for id in ['openAIPanel','openExtensionsWindow','openReferencesWindow']:
                assert by_id[id]['disabled'] and by_id[id]['disabledReason'], id
            await page.get_by_role('menuitem', name='AI', exact=True).click()
            await page.get_by_role('img', name='尚未接入网页 AI 会话与模型服务', exact=False).hover()
            await expect(page.get_by_text('尚未接入网页 AI 会话与模型服务', exact=True)).to_be_visible()
            await page.screenshot(path=str(output / 'ai-menu.png'))
            await page.get_by_role('menuitem', name='AI 工具（内置工具目录）', exact=True).click()
            frame = page.frame_locator('iframe')
            await expect(frame.get_by_text('AI 工具 · 内置工具目录', exact=True)).to_be_visible()
            await frame.get_by_role('textbox', name='搜索 AI 内置工具').fill('node')
            assert await frame.locator('details').count() > 0
            await page.evaluate('window.canvas()')
            async def run(id):
                await page.evaluate('id=>window.run(id)', id)
            async def upload(id, file):
                async with page.expect_file_chooser() as choice:
                    await run(id)
                await (await choice.value).set_files(file)
            await upload('importTextFile', {'name':'sample.txt','mimeType':'text/plain','buffer':'菜单回归文本'.encode()})
            await page.wait_for_function('window.writes>0')
            async with page.expect_download() as download:
                await run('exportSvgAll')
            path = output / 'text.svg'
            await (await download.value).save_as(path)
            assert '菜单回归文本' in path.read_text(encoding='utf-8')
            previous_writes = await page.evaluate('window.writes')
            png = await page.evaluate("""()=>{const canvas=document.createElement('canvas');canvas.width=80;canvas.height=50;
              const ctx=canvas.getContext('2d');ctx.fillStyle='#409eff';ctx.fillRect(0,0,80,50);return canvas.toDataURL().split(',')[1]}""")
            await upload('importImages', {'name':'pixel.png','mimeType':'image/png','buffer':base64.b64decode(png)})
            await page.wait_for_function('n=>window.writes>n||window.errors.length', arg=previous_writes)
            assert await page.evaluate('window.errors') == [], await page.evaluate('window.errors')
            async with page.expect_download() as download:
                await run('exportSvgAll')
            path = output / 'image.svg'
            await (await download.value).save_as(path)
            assert 'data:image/' in path.read_text(encoding='utf-8'), 'SVG must embed images'
            previous_writes = await page.evaluate('window.writes')
            await upload('importSvg', {'name':'vector.svg','mimeType':'image/svg+xml','buffer':b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30"><rect width="40" height="30" fill="red"/></svg>'})
            await page.wait_for_function('n=>window.writes>n||window.errors.length', arg=previous_writes)
            assert await page.evaluate('window.errors') == [], await page.evaluate('window.errors')
            async with page.expect_download() as download:
                await run('exportSvgAll')
            path = output / 'vector.svg'
            await (await download.value).save_as(path)
            assert 'data:image/svg+xml;base64,' in path.read_text(encoding='utf-8'), 'SVG export must retain vector nodes'
            async with page.expect_download() as download:
                await run('exportPngLegacy')
            path = output / 'canvas.png'
            await (await download.value).save_as(path)
            assert path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
            await frame.locator('canvas').first.click(position={'x':30,'y':30})
            await page.keyboard.press('Control+a')
            async with page.expect_download() as download:
                await run('exportSvgSelected')
            path = output / 'selected.svg'
            await (await download.value).save_as(path)
            selected_svg = path.read_text(encoding='utf-8')
            assert '菜单回归文本' in selected_svg and 'data:image/' in selected_svg
            await run('openAttachmentsWindow')
            await expect(frame.get_by_role('heading', name='附件管理器', exact=True)).to_be_visible()
            await expect(frame.get_by_alt_text('附件预览')).to_have_count(2)
            async with page.expect_file_chooser() as choice:
                await frame.get_by_role('button', name='添加附件', exact=True).click()
            await (await choice.value).set_files({'name':'note.txt','mimeType':'text/plain','buffer':b'attachment-body'})
            await expect(frame.get_by_role('button', name='下载', exact=True)).to_have_count(3)
            async with page.expect_download() as download:
                await frame.get_by_role('button', name='下载', exact=True).nth(2).click()
            path = output / 'attachment.txt'
            await (await download.value).save_as(path)
            assert path.read_bytes() == b'attachment-body'
            await page.evaluate("localStorage.setItem('codeyun.pg.settings.menu-test.keep','true')")
            await run('resetAllKeyBinds')
            await frame.get_by_role('button', name='确定', exact=True).click()
            await expect(frame.get_by_text('快捷键已重置', exact=True)).to_be_visible()
            assert await page.evaluate("localStorage.getItem('codeyun.pg.settings.menu-test.keep')") == 'true'
            await page.evaluate('window.flush()')
            previous = await page.locator('iframe').get_attribute('src')
            await page.evaluate('window.reload()')
            await page.wait_for_function('src=>document.querySelector("iframe").src!==src', arg='http://localhost:5173'+previous)
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            await run('openAttachmentsWindow')
            await expect(frame.get_by_role('button', name='下载', exact=True)).to_have_count(3)
            assert not errors, errors
            assert await page.evaluate('window.errors') == [], await page.evaluate('window.errors')
            print(f'PASS: {len(by_id)} menu entries; all upstream groups retained; AI catalog, text/image import, SVG, attachments and PRG reload; {output}')
        finally:
            await browser.close()


asyncio.run(main())
