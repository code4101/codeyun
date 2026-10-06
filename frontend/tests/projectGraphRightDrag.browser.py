"""Native right-drag cutting must not be intercepted by the browser menu.

uv run --with playwright python frontend/tests/projectGraphRightDrag.browser.py [origin]
"""
import asyncio
import json
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.core.project_graph.codec import import_prg
from playwright.async_api import async_playwright

HTML = runpy.run_path(str(Path(__file__).with_name('projectGraphZipCompatibility.browser.py')))['HTML']


async def main():
    origin = sys.argv[1].rstrip('/') if len(sys.argv) > 1 else 'http://localhost:5173'
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            page = await browser.new_page(viewport={'width':1024,'height':768})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.route('**/__pg-right-drag-test', lambda r: r.fulfill(content_type='text/html',body=HTML))
            await page.goto(origin+'/__pg-right-drag-test')
            await page.wait_for_function('window.presented',timeout=60000)
            frame = page.frame_locator('iframe')
            canvas = frame.locator('canvas').first
            await canvas.click(position={'x':50,'y':50})
            await page.wait_for_timeout(500)
            await canvas.dblclick(position={'x':300,'y':200})
            await frame.locator('textarea').fill('右键划线删除验收')
            await page.keyboard.press('Escape')
            await page.wait_for_function('window.saved !== null',timeout=30000)
            assert '右键划线删除验收' in json.dumps(import_prg(bytes(await page.evaluate('window.saved'))),ensure_ascii=False)
            box = await canvas.bounding_box()
            await page.evaluate('window.saved=null')
            # Start well outside the node; font loading can change its width.
            await page.mouse.move(box['x']+50,box['y']+200)
            await page.mouse.down(button='right')
            await page.wait_for_timeout(100)
            assert not await frame.get_by_role('menu').count(), 'right down must not steal the native gesture'
            await page.mouse.move(box['x']+600,box['y']+200,steps=20)
            await page.mouse.up(button='right')
            assert not await frame.get_by_role('menu').count(), 'drag must not open a menu'
            await page.evaluate("document.querySelector('iframe').contentWindow.postMessage({channel:'codeyun.project-graph',version:1,session:'zip-test',type:'export'},location.origin)")
            await page.wait_for_function('window.exported !== null',timeout=30000)
            exported = import_prg(bytes(await page.evaluate('window.exported')))
            assert not exported.get('@order',{}).get('value'), {'remaining':exported,'errors':await page.evaluate('window.errors')}
            await page.evaluate("document.querySelector('iframe').contentWindow.postMessage({channel:'codeyun.project-graph',version:1,session:'zip-test',type:'save'},location.origin)")
            await page.wait_for_function('window.saved !== null',timeout=30000)
            objects = import_prg(bytes(await page.evaluate('window.saved')))
            assert not objects.get('@order',{}).get('value'), objects
            await canvas.dblclick(position={'x':300,'y':200})
            await frame.locator('textarea').fill('Selected context menu')
            await page.keyboard.press('Escape')
            await canvas.click(position={'x':50,'y':50})
            await page.keyboard.press('Control+a')
            await canvas.click(position={'x':300,'y':200},button='right')
            await frame.get_by_role('menu').first.wait_for(timeout=5000)
            await frame.get_by_role('menuitem', name='更改颜色', exact=True).wait_for(timeout=5000)
            # These are upstream selection-sensitive entries, not the host's
            # workspace menu. Exercise a native command and a native submenu.
            for label in ['更改颜色', '放大字体', '缩小字体', '切换换行模式',
                          '文本节点边框样式', '文本节点 巧妙操作']:
                assert await frame.get_by_role('menuitem', name=label, exact=False).count(), label
            assert await frame.get_by_role('menu').first.get_by_role('button').count() >= 5
            async def snapshot():
                await page.evaluate('window.exported=null')
                await page.evaluate("document.querySelector('iframe').contentWindow.postMessage({channel:'codeyun.project-graph',version:1,session:'zip-test',type:'export'},location.origin)")
                await page.wait_for_function('window.exported !== null',timeout=30000)
                return import_prg(bytes(await page.evaluate('window.exported')))
            before = await snapshot()
            await frame.get_by_role('menuitem', name='放大字体', exact=False).click()
            larger = await snapshot()
            node_id = before['@order']['value'][0]
            assert larger[node_id]['fontScaleLevel'] > before[node_id]['fontScaleLevel'], 'native font command must change the selected node'
            await canvas.click(position={'x':300,'y':200},button='right')
            await frame.get_by_role('menuitem', name='文本节点边框样式', exact=True).hover()
            await frame.get_by_role('menuitem', name='虚线', exact=False).click()
            dashed = await snapshot()
            assert dashed[node_id]['borderStyle'] == 'dashed', 'native border submenu must change the selected node'
            await canvas.click(position={'x':50,'y':50})
            await canvas.click(position={'x':50,'y':50},button='right')
            await frame.get_by_role('menuitem', name='更改颜色', exact=True).wait_for(state='hidden',timeout=5000)
            assert not await frame.get_by_role('menuitem', name='文本节点边框样式', exact=True).count()
            assert not errors and not await page.evaluate('window.errors'), errors
            print(json.dumps({'right_drag_deleted_node':True,'drag_did_not_open_menu':True,
                              'native_selected_menu':True,'native_font_command':True,
                              'native_border_submenu':True,'selection_refresh':True}),flush=True)
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
