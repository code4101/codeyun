"""uv run --with playwright python frontend/tests/projectGraphJournal.browser.py

Exercise the real workspace/iframe with an isolated browser and in-memory HTTP
library. Never reads or writes the user's graphs. Screenshots go to TEMP/codeyun.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import urllib.request

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
                    day = suffix.rsplit('/', 1)[1]
                    result = next((r for r in entries.values() if r.get('journalDate') == day), None)
                    if not result:
                        rid = str(len(entries) + 1)
                        result = {'id': rid, 'title': day, 'kind': 'document', 'role': 'manager', 'ownerId': 999999, 'parentId': 0, 'revision': 0, 'updatedAt': 0, 'journalDate': day, 'content': ''}
                        entries[rid] = result
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
                await route.fulfill(json=result)
            await page.route('**/api/project-graph/files**', api)
            await page.route('**/__pg-journal-test', lambda r: r.fulfill(content_type='text/html', body=html))
            await page.goto(origin + '/__pg-journal-test')
            await page.get_by_role('button', name='每日记录', exact=True).click()
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            await expect(page.locator('.journal-week button[aria-pressed=true]')).to_have_count(1)
            await page.get_by_role('button', name='上一周', exact=True).click()
            assert len(entries) == 1, 'browsing the calendar must not create empty documents'
            await page.get_by_label('选择记录日期', exact=True).fill('2026-01-01')
            await page.get_by_label('选择记录日期', exact=True).press('Tab')
            await expect(page.locator('.journal-toolbar strong')).to_contain_text('1月1日')
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            await page.get_by_role('button', name='前一天', exact=True).click()
            await expect(page.locator('.journal-toolbar strong')).to_contain_text('12月31日')
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            await page.screenshot(path=str(output / 'daily-canvas.png'))
            await page.get_by_role('button', name='修改日期', exact=True).click()
            await page.get_by_role('button', name='清除日期', exact=True).click()
            await page.get_by_role('button', name='确定', exact=True).click()
            await expect(page.get_by_role('button', name='设为每日记录', exact=True)).to_be_visible()
            assert len(entries) == 3
            assert entries['3']['journalDate'] is None and entries['3']['content']
            assert writes, 'real iframe flush saves PRG content through the adapter'
            await page.get_by_role('button', name='全部文件', exact=True).click()
            await expect(page.get_by_role('treeitem')).to_have_count(3)
            await page.reload()
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            assert len(entries) == 3
            assert not errors, errors
            print(json.dumps({'documents': len(entries), 'writes': len(writes), 'errors': errors, 'screenshot': str(output / 'daily-canvas.png')}))
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
