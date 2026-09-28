"""uv run --with playwright python frontend/tests/projectGraphJournal.browser.py

Exercise the real workspace/iframe with an isolated browser and in-memory HTTP
library. Never reads or writes the user's graphs. Screenshots go to TEMP/codeyun.
"""
import asyncio
import base64
import json
import os
from pathlib import Path
import re
import urllib.request
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.core.project_graph.codec import has_prg_content, import_prg

from playwright.async_api import async_playwright, expect


async def main():
    origin = 'http://127.0.0.1:5173'
    source = urllib.request.urlopen(origin + '/src/plugins/modules/project-graph/GraphWorkspace.vue').read().decode()
    user_source = urllib.request.urlopen(origin + '/src/store/userStore.ts').read().decode()
    vue = re.search(r'"([^" ]*/deps/vue.js[^" ]*)"', source).group(1)
    router = re.search(r'"([^" ]*/deps/vue-router.js[^" ]*)"', source).group(1)
    pinia = re.search(r'"([^" ]*/deps/pinia.js[^" ]*)"', user_source).group(1)
    html = '''<style>html,body,#app{height:100%;margin:0}</style><div id="app"></div>
    <script type="module">
    import {createApp} from 'VUE'; import {createPinia} from 'PINIA';
    import {createRouter,createMemoryHistory} from 'ROUTER';
    import Page from '/src/plugins/modules/project-graph/GraphWorkspace.vue';
    import {useUserStore} from '/src/store/userStore.ts';
    import '/node_modules/element-plus/dist/index.css';
    const app=createApp(Page), pinia=createPinia();app.use(pinia);
    const user=useUserStore();user.user={id:999999,username:'journal-test'};
    user.token='test.'+btoa(JSON.stringify({sub:'999999',scope:'user-session'}))+'.test';
    const router=createRouter({history:createMemoryHistory(),routes:[{path:'/',component:Page,meta:{supportsContentOnly:true}}]});
    app.use(router);app.directive('context-menu',{});await router.push('/');await router.isReady();app.mount('#app');
    </script>'''.replace("'VUE'", json.dumps(vue)).replace("'PINIA'", json.dumps(pinia)).replace("'ROUTER'", json.dumps(router))
    entries = {}
    writes = []
    output = Path(os.environ['TEMP']) / 'codeyun' / 'pg-journal'
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            page = await browser.new_page(viewport={'width': 1440, 'height': 900}, timezone_id='Asia/Shanghai')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            async def api(route):
                request = route.request
                suffix = request.url.split('/api/project-graph/files')[1]
                if not suffix:
                    result = {'ownerId': 999999, 'legacyBrowserImport': False, 'entries': list(entries.values())}
                elif suffix.startswith('/journals/'):
                    day = suffix.split('/')[2]
                    result = next((r for r in entries.values() if r.get('journalDate') == day), None)
                    if suffix.endswith('/content') and has_prg_content(base64.b64decode(request.post_data_json['content'])) and not result:
                        rid = str(len(entries) + 1)
                        result = {'id': rid, 'title': day, 'kind': 'document', 'role': 'manager', 'ownerId': 999999, 'parentId': 0, 'revision': 1, 'updatedAt': 0, 'journalDate': day, 'content': request.post_data_json['content']}
                        entries[rid] = result
                        writes.append(rid)
                else:
                    rid = suffix.split('/')[1]
                    result = entries[rid]
                    if request.method == 'PUT':
                        body = request.post_data_json
                        assert body['expectedRevision'] == result['revision']
                        result.update(content=body['content'], revision=result['revision'] + 1)
                        writes.append(rid)
                    elif request.method == 'PATCH':
                        result.update(request.post_data_json)
                await route.fulfill(content_type='application/json', body=json.dumps(result))
            await page.route('**/api/project-graph/files**', api)
            await page.route('**/__pg-journal-test', lambda r: r.fulfill(content_type='text/html', body=html))
            await page.goto(origin + '/__pg-journal-test')
            await page.get_by_role('button', name='每日记录', exact=True).click()
            await page.locator('.journal-heading').get_by_role('button', name='今天', exact=True).click()
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            await expect(page.locator('.journal-week button[aria-pressed=true]')).to_have_count(1)
            await page.get_by_role('button', name='上一周', exact=True).click()
            assert len(entries) == 0, 'browsing the calendar must not create empty documents'
            await page.get_by_label('选择记录日期', exact=True).fill('2026-01-01')
            await page.get_by_label('选择记录日期', exact=True).press('Tab')
            await expect(page.locator('.journal-toolbar strong')).to_contain_text('1月1日')
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            await page.get_by_role('button', name='前一天', exact=True).click()
            await expect(page.locator('.journal-toolbar strong')).to_contain_text('12月31日')
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            assert len(entries) == 0, [import_prg(base64.b64decode(r['content'])) for r in entries.values()]
            await expect(page.get_by_role('tab')).to_have_count(1)
            await expect(page.get_by_role('tab')).to_contain_text('每日记录')
            original_frame = await page.locator('iframe').element_handle()
            await page.frame_locator('iframe').locator('canvas').first.dblclick(position={'x': 400, 'y': 300})
            await page.keyboard.insert_text('首次记录')
            await page.keyboard.press('Escape')
            await expect(page.get_by_role('button', name='修改日期', exact=True)).to_be_visible(timeout=15000)
            assert await original_frame.evaluate('(frame) => frame === document.querySelector("iframe")'), 'first save must preserve the editor and undo history'
            await page.frame_locator('iframe').locator('canvas').first.dblclick(position={'x': 650, 'y': 450})
            await page.keyboard.insert_text('继续记录')
            await page.keyboard.press('Escape')
            await page.get_by_role('button', name='前一天', exact=True).click()
            await expect(page.locator('.journal-toolbar strong')).to_contain_text('12月30日')
            assert len(entries) == 1, 'first authored content creates exactly one file'
            assert entries['1']['revision'] >= 2, 'subsequent edits write to the adopted permanent ID'
            await expect(page.get_by_role('tab')).to_have_count(1)
            await page.get_by_role('button', name='后一天', exact=True).click()
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            await page.screenshot(path=str(output / 'daily-canvas.png'))
            await expect(page.get_by_role('tab')).to_have_count(1)
            await page.get_by_role('button', name='资源管理器', exact=True).click()
            await expect(page.get_by_role('treeitem')).to_have_count(0)
            await page.get_by_role('button', name='每日记录', exact=True).click()
            # Old sessions stored individual daily file IDs. Restore them into
            # the same aggregate tab without creating another daily document.
            await page.evaluate("localStorage.setItem('codeyun.project-graph.tabs:999999', JSON.stringify(['1']))")
            await page.reload()
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            await expect(page.get_by_role('tab')).to_have_count(1)
            await expect(page.get_by_role('tab')).to_contain_text('每日记录')
            await expect(page.locator('.journal-toolbar strong')).to_contain_text('12月31日')
            assert len(entries) == 1
            await page.get_by_role('button', name='修改日期', exact=True).click()
            await page.get_by_role('button', name='清除日期', exact=True).click()
            await page.get_by_role('button', name='确定', exact=True).click()
            await expect(page.locator('.journal-toolbar')).to_have_count(0)
            assert len(entries) == 1
            assert entries['1']['journalDate'] is None and entries['1']['content']
            assert writes, 'real iframe flush saves PRG content through the adapter'
            await page.get_by_role('button', name='资源管理器', exact=True).click()
            await expect(page.get_by_role('treeitem')).to_have_count(1)
            await page.reload()
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            assert len(entries) == 1
            assert not errors, errors
            print(json.dumps({'documents': len(entries), 'writes': len(writes), 'errors': errors, 'screenshot': str(output / 'daily-canvas.png')}))
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
