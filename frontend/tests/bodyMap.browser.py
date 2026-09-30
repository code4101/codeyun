"""Body-map interaction regression. Uses an isolated Chrome profile, no user records.

Run against Vite from repository root:
uv run --with playwright python -c "import runpy; runpy.run_path('frontend/tests/bodyMap.browser.py', run_name='__main__')"
Set BODY_MAP_URL to check the deployed route with the same interaction contract.
"""
import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright

from backend.core.temp_paths import codeyun_temp_root


async def main():
    url = os.environ.get('BODY_MAP_URL', 'http://localhost:5173/tools/body-map?ui=0&mode=legacy')
    output = Path(codeyun_temp_root('body-map'))
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            context = await browser.new_context(viewport={'width': 1280, 'height': 1000})
            page = await context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto(url)
            await page.get_by_role('heading', name='人体模型', exact=True).wait_for(timeout=45000)
            await page.screenshot(path=str(output / 'full-body.png'), full_page=True)
            await page.get_by_role('button', name='放大头部', exact=True).click()
            canvas = page.locator('.body-canvas')

            async def click_point(x, y):
                # Verify the actual SVG viewport transform, including letterboxing.
                screen = await canvas.evaluate('(el, p) => { const q = new DOMPoint(...p).matrixTransform(el.getScreenCTM()); return {x:q.x,y:q.y} }', [x, y])
                await page.mouse.click(screen['x'], screen['y'])

            await click_point(177, 65)
            assert await page.locator('.mark-item').count() == 0, 'Viewing must not create annotations'
            await page.get_by_role('button', name='点选标注', exact=True).click()
            await click_point(177, 65)
            region = page.get_by_role('textbox', name='位置名称', exact=True)
            assert await region.input_value() == '右额部'
            assert await page.get_by_label('不适感觉', exact=True).count() == 0
            assert await page.get_by_label('不适程度', exact=True).count() == 0
            await page.get_by_label('补充说明', exact=True).fill('自动测试记录')
            await page.get_by_role('button', name='查看文字标注', exact=True).click()
            assert '右额部：自动测试记录' in await page.get_by_label('文字标注', exact=True).input_value()
            before = await page.evaluate("JSON.parse(localStorage.getItem('codeyun.body-map.v2'))")
            assert 'feeling' not in before[0] and 'severity' not in before[0]
            assert abs(before[0]['x'] - 177) < 1 and abs(before[0]['y'] - 65) < 1
            await page.reload()
            await region.wait_for(timeout=45000)
            assert await region.input_value() == '右额部'
            assert await page.get_by_label('补充说明', exact=True).input_value() == '自动测试记录'
            await page.get_by_role('button', name='背面', exact=True).click()
            await page.get_by_role('button', name='放大头部', exact=True).click()
            await page.get_by_role('button', name='点选标注', exact=True).click()
            await click_point(177, 90)
            assert await region.input_value() == '左后脑部'
            assert await canvas.locator('[role=button]').count() == 1
            await page.get_by_role('button', name='拖动查看', exact=True).click()
            bounds = await canvas.bounding_box()
            await page.mouse.move(bounds['x'] + bounds['width']/2, bounds['y'] + bounds['height']/2)
            await page.mouse.down()
            await page.mouse.move(bounds['x'] + bounds['width']/2 + 30, bounds['y'] + bounds['height']/2 + 35, steps=5)
            await page.mouse.up()
            assert await page.locator('.mark-item').count() == 2
            await page.get_by_role('button', name='点选标注', exact=True).click()
            await click_point(222, 91)
            assert await region.input_value() == '右后脑部'
            await page.get_by_role('button', name='头顶', exact=True).click()
            await click_point(200, 85)
            assert await region.input_value() == '头顶部中央'
            await page.get_by_role('button', name='本人左侧', exact=True).click()
            await click_point(190, 70)
            assert (await region.input_value()).startswith('左侧')
            await page.get_by_role('button', name='本人右侧', exact=True).click()
            await click_point(190, 70)
            assert (await region.input_value()).startswith('右侧')
            await page.screenshot(path=str(output / 'desktop.png'), full_page=True)
            async with page.expect_download() as info:
                await page.get_by_role('button', name='下载当前图', exact=True).click()
            download = await info.value
            await download.save_as(output / 'export.svg')
            assert '男性体表' in (output / 'export.svg').read_text(encoding='utf-8')
            await page.get_by_role('button', name='删除这处标注', exact=True).click()
            assert await page.locator('.mark-item').count() == 5
            await page.set_viewport_size({'width': 390, 'height': 844})
            await page.get_by_role('button', name='正面', exact=True).click()
            await page.get_by_role('button', name='放大头部', exact=True).click()
            await page.screenshot(path=str(output / 'mobile.png'), full_page=True)
            assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            await page.get_by_label('补充说明', exact=True).scroll_into_view_if_needed()
            await page.screenshot(path=str(output / 'mobile-records.png'), full_page=True)
            # Verify lossless, idempotent migration in this disposable profile.
            await page.evaluate("""() => {
              localStorage.removeItem('codeyun.body-map.v2');
              localStorage.setItem('codeyun.body-map.v1', JSON.stringify([{id:'legacy',view:'front',x:177,y:65,radius:10,region:'右额部',feeling:'胀痛',severity:4,note:'原有说明'}]));
            }""")
            await page.reload()
            await region.wait_for(timeout=45000)
            migrated = await page.get_by_label('补充说明', exact=True).input_value()
            assert migrated == '胀痛；不适程度 4/10；原有说明'
            await page.reload()
            await region.wait_for(timeout=45000)
            assert await page.get_by_label('补充说明', exact=True).input_value() == migrated
            assert not errors, errors
            print(f'PASS: {url}; transformed clicks, anatomical sides, all views, pan, persistence, editing, export, deletion, mobile overflow; screenshots: {output}')
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
