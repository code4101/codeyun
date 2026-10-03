"""Real keyboard clipboard acceptance on the built PG and host, with two in-memory PRGs.
Run: uv run --with playwright python frontend/tests/projectGraphClipboard.browser.py
No application accounts or user files are changed.
"""
import asyncio
import base64
import io
import re
import sys
import urllib.request
import zipfile
import msgpack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.core.project_graph.codec import export_prg, import_prg
from playwright.async_api import async_playwright


def fixture():
    ids = [f'{i:08d}-1111-4111-8111-111111111111' for i in range(1, 6)]
    a, b, edge, group, asset = ids
    def rect(x, y, width=90, height=40):
        return {'_': 'CollisionBox', 'shapes': [{'_': 'Rectangle', 'location': {'_': 'Vector', 'x': x, 'y': y}, 'size': {'_': 'Vector', 'x': width, 'y': height}}]}
    png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a/YQAAAAASUVORK5CYII='
    template = io.BytesIO()
    with zipfile.ZipFile(template, 'w') as archive:
        archive.writestr('stage.msgpack', msgpack.packb([]))
        archive.writestr('metadata.msgpack', msgpack.packb({'version': '2.7.0'}))
    return export_prg(template.getvalue(), {
        a: {'_': 'TextNode', 'uuid': a, 'text': '跨文件中文节点', 'collisionBox': rect(-120, -20), 'details': [{'type': 'p', 'children': [{'text': '富文本正文', 'bold': True}]}]},
        b: {'_': 'ImageNode', 'uuid': b, 'attachmentId': asset, 'scale': 40, 'collisionBox': rect(100, -20, 40, 40)},
        edge: {'_': 'LineEdge', 'uuid': edge, 'text': '关系', 'associationList': [{'$graphRef': a}, {'$graphRef': b}]},
        group: {'_': 'Section', 'uuid': group, 'text': '测试分组', 'locked': True, '_collisionBoxNormal': rect(-160, -100, 360, 200), 'children': [{'$graphRef': a}, {'$graphRef': b}]},
        '@order': {'value': [a, b, edge, group]}, '@metadata': {'value': {'version': '2.7.0'}}, '@tags': {'value': ['待办']}, '@attachment:' + asset + '.png': {'value': png},
    })


async def main():
    source = urllib.request.urlopen('http://localhost:5173/src/components/PlateEditor.vue').read().decode()
    vue = re.search(r'"([^" ]*/deps/vue.js[^" ]*)"', source).group(1)
    html = '''<style>html,body,#app{height:100%;margin:0}</style><div id="app"></div><script type="module">
    import {createApp,h,ref} from 'VUE_URL';
    import Editor from '/src/plugins/modules/project-graph/ProjectGraphEditor.vue';
    window.errors=[];window.gallery=null;window.docs={source:{id:'source',bytes:Uint8Array.from(atob('SOURCE'),c=>c.charCodeAt(0)),revision:0,role:'manager'}};
    const storage={clipboardScope:'pg-clipboard-test',read:async id=>window.docs[id],write:async(id,title,bytes,revision)=>{
      return window.docs[id]={id,title,bytes,revision:revision+1,updatedAt:Date.now(),role:'manager'};}};
    createApp({setup(){const editor=ref(),active=ref('source');window.switchDoc=id=>{active.value=id};window.flush=()=>editor.value.flush();
      window.run=id=>editor.value.executeMenu(id);
      return()=>h(Editor,{key:active.value,ref:editor,documentId:active.value,title:active.value,storage,
      galleryActive:true,onGallery:v=>window.gallery=v,onError:e=>window.errors.push(e)});
    }}).mount('#app');</script>'''.replace('VUE_URL', vue).replace('SOURCE', base64.b64encode(fixture()).decode())
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel='chrome', headless=True)
        try:
            context = await browser.new_context(permissions=['clipboard-read', 'clipboard-write'], viewport={'width': 1200, 'height': 850})
            page = await context.new_page()
            page_errors = []
            page.on('pageerror', lambda error: page_errors.append(str(error)))
            await page.route('**/__pg-clipboard-test', lambda route: route.fulfill(content_type='text/html', body=html))
            await page.goto('http://localhost:5173/__pg-clipboard-test')
            async def ready():
                try:
                    await page.locator('iframe:not(.pending)').wait_for(timeout=30000)
                except Exception:
                    print('BOOT', page_errors, await page.evaluate('window.errors'), flush=True)
                    print('FRAME', [(frame.url, (await frame.locator('body').inner_text())[:600]) for frame in page.frames], flush=True)
                    raise
                await page.evaluate('window.flush()')
            async def snapshot(id):
                await page.evaluate('window.flush()')
                data = await page.evaluate('(id)=>Array.from(window.docs[id].bytes)', id)
                return import_prg(bytes(data))
            async def switch(id):
                old = await page.locator('iframe').get_attribute('src')
                await page.evaluate('(id)=>window.switchDoc(id)', id)
                await page.wait_for_function('(old)=>document.querySelector("iframe").src.endsWith(old) === false', arg=old)
                await ready()
            async def select():
                await page.frame_locator('iframe').locator('canvas').first.click(position={'x': 40, 'y': 40})
                await page.keyboard.press('Control+a')
                await page.wait_for_function('window.gallery?.selection.ids.length > 0')
            async def key(value, toast):
                await page.keyboard.press(value)
                await page.frame_locator('iframe').get_by_text(toast, exact=True).last.wait_for(timeout=15000)
            await ready()
            await select()
            await key('Control+c', '已复制图结构')
            original = await snapshot('source')
            html_clipboard = await page.evaluate("async()=>{const items=await navigator.clipboard.read();return (await items[0].getType('text/html')).text()}")
            assert 'data-codeyun-pg-selection=' in html_clipboard
            await switch('target')
            await page.frame_locator('iframe').locator('canvas').first.click(position={'x': 40, 'y': 40})
            await key('Control+v', '已粘贴图结构')
            target = await snapshot('target')
            assert len(target['@order']['value']) == 4, target
            assert set(target['@order']['value']).isdisjoint(original['@order']['value'])
            node = next(value for value in target.values() if value.get('_') == 'TextNode')
            assert node['details'] == next(value for value in original.values() if value.get('_') == 'TextNode')['details']
            image = next(value for value in target.values() if value.get('_') == 'ImageNode')
            assert '@attachment:' + image['attachmentId'] + '.png' in target
            group = next(value for value in target.values() if value.get('_') == 'Section')
            edge = next(value for value in target.values() if value.get('_') == 'LineEdge')
            assert {ref['$graphRef'] for ref in group['children']} == {ref['$graphRef'] for ref in edge['associationList']}
            assert all(ref['$graphRef'] in target for ref in group['children'])
            # Native image constructors must find the transferred bytes immediately.
            await page.frame_locator('iframe').locator('canvas').first.focus()
            await page.keyboard.press('Control+z')
            await page.wait_for_timeout(250)
            undone = await snapshot('target')
            assert not undone['@order']['value'], (undone['@order']['value'], page_errors, await page.evaluate('window.errors'), await page.frame_locator('iframe').locator('body').inner_text())
            await page.keyboard.press('Control+y')
            await page.wait_for_timeout(250)
            assert len((await snapshot('target'))['@order']['value']) == 4
            await key('Control+v', '已粘贴图结构')
            assert len((await snapshot('target'))['@order']['value']) == 8
            # Clipboard transport survives a whole-page reload, not just iframe replacement.
            await page.reload(); await ready(); await switch('fresh')
            await page.frame_locator('iframe').locator('canvas').first.click(position={'x': 40, 'y': 40})
            await key('Control+v', '已粘贴图结构')
            assert len((await snapshot('fresh'))['@order']['value']) == 4
            # Cut uses the captured selection, then supports source undo and target paste.
            await switch('source'); await select()
            await key('Control+x', '已剪切图结构')
            assert not (await snapshot('source'))['@order']['value']
            await page.keyboard.press('Control+z')
            await page.wait_for_timeout(250)
            assert len((await snapshot('source'))['@order']['value']) == 4
            await switch('cut-target')
            await page.frame_locator('iframe').locator('canvas').first.click(position={'x': 40, 'y': 40})
            await key('Control+v', '已粘贴图结构')
            assert len((await snapshot('cut-target'))['@order']['value']) == 4
            # External plain text replaces structured clipboard, with no stale graph fallback.
            await page.evaluate("navigator.clipboard.writeText('外部纯文本')")
            await page.keyboard.press('Control+v')
            await page.wait_for_timeout(500)
            plain = await snapshot('cut-target')
            assert len(plain['@order']['value']) == 5
            assert any(value.get('text') == '外部纯文本' for value in plain.values())
            assert not page_errors, page_errors
            assert not await page.evaluate('window.errors'), await page.evaluate('window.errors')
            print('PASS: real Ctrl+C/X/V across rebuilt iframes and reload, graph references, rich bodies, attachments, undo/redo, repeated paste, external text')
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
