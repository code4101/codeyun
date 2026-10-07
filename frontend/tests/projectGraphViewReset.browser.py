"""uv run --with playwright python frontend/tests/projectGraphViewReset.browser.py

Actual workspace and embedded camera, with isolated browser storage and mock files.
"""
import asyncio
import json
import re
import urllib.request

from playwright.async_api import async_playwright, expect

ORIGIN = 'http://localhost:5173'


def dependency(source, name):
    text = urllib.request.urlopen(ORIGIN + source).read().decode()
    return re.search(r'"([^" ]*/deps/' + name + r'\.js[^" ]*)"', text).group(1)


async def main():
    html = '''<!doctype html><style>html,body,#app{height:100%;margin:0}</style>
    <div id="app"></div><script type="module">
    import {createApp,h} from 'VUE';
    import {createPinia} from 'PINIA';
    import {createRouter,createWebHistory,RouterView} from 'ROUTER';
    import Workspace from '/src/plugins/modules/project-graph/GraphWorkspace.vue';
    import {useUserStore} from '/src/store/userStore.ts';
    import {contextMenuDirective} from '/src/directives/contextMenu.ts';
    import '/src/standard/pdf/library/readerTheme.css';
    import '/node_modules/element-plus/dist/index.css';
    const app=createApp({render:()=>h(RouterView)});app.use(createPinia());
    const user=useUserStore();user.setTokens('fixture.'+btoa(JSON.stringify({sub:'1',scope:'user-session'}))+'.fixture',null);
    user.user={id:1,username:'fixture',is_superuser:false};
    app.directive('context-menu',contextMenuDirective);
    const router=createRouter({history:createWebHistory(),routes:[{path:'/__view-reset-test',component:Workspace}]});
    app.use(router);await router.isReady();app.mount('#app');
    </script>'''
    for token, source, name in [
        ('VUE', '/src/components/PlateEditor.vue', 'vue'),
        ('PINIA', '/src/store/userStore.ts', 'pinia'),
        ('ROUTER', '/src/plugins/modules/project-graph/GraphWorkspace.vue', 'vue-router'),
    ]:
        html = html.replace("'" + token + "'", json.dumps(dependency(source, name)))
    rows = [dict(id=i, kind='document', title=f'视图{i}', role='viewer',
                 revision=1, content='', updatedAt=1, parentId=0) for i in (1, 2)]
    writes = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            page = await browser.new_page(viewport={'width': 1400, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/__view-reset-test*', lambda route: route.fulfill(content_type='text/html', body=html))

            async def api(route):
                request = route.request
                if request.method != 'GET':
                    writes.append(request.url)
                    await route.fulfill(status=500, json={'detail': 'Unexpected document mutation'})
                    return
                suffix = request.url.split('/api/project-graph/files')[1]
                await route.fulfill(json={'entries': rows, 'legacyBrowserImport': False} if not suffix
                                    else next(row for row in rows if suffix == f'/{row["id"]}'))

            await page.route('**/api/project-graph/files**', api)
            await page.add_init_script('''
              if (window === window.top) {
                localStorage.setItem('codeyun.project-graph.tabs:1', '["1","2"]');
                for(const id of ['1','2']) localStorage.setItem('codeyun.project-graph.view:1:'+id,
                  JSON.stringify({scale:0.37,x:900,y:800}));
              }
            ''')
            await page.goto(ORIGIN + '/__view-reset-test?doc=1')
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            await expect(page.get_by_role('tablist')).to_have_count(1)
            await expect(page.frame_locator('iframe').locator('[data-pg-tab-bar]')).to_have_count(0)
            key = 'codeyun.project-graph.view:1:'
            state = lambda id: page.evaluate('(key)=>JSON.parse(localStorage.getItem(key))', key + id)
            assert await state('1') == dict(scale=0.37, x=900, y=800)
            await page.get_by_role('tab', name='视图2.prg', exact=True).click(button='right')
            await page.get_by_role('menuitem', name='重置比例与位置', exact=True).click()
            assert await state('2') is None
            await expect(page.get_by_role('tab', name='视图1.prg', exact=True)).to_have_attribute('aria-selected', 'true')
            await page.get_by_role('tab', name='视图1.prg', exact=True).click(button='right')
            await page.get_by_role('menuitem', name='重置比例与位置', exact=True).click()
            await page.wait_for_function('JSON.parse(localStorage.getItem("codeyun.project-graph.view:1:1")).x === 0')
            reset = await state('1')
            assert reset['scale'] > 0.37 and reset['y'] == 0
            await page.get_by_role('tab', name='视图2.prg', exact=True).click()
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            await page.wait_for_function('localStorage.getItem("codeyun.project-graph.view:1:2") !== null')
            assert await state('2') == reset, (await state('2'), reset)
            await page.get_by_role('tab', name='视图1.prg', exact=True).click()
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            assert await state('1') == reset
            assert not writes, writes
            assert not errors, errors
            print('PASS: active/inactive tab reset, persisted fit/center, read-only viewing, zero document mutations')
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
