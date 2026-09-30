"""Integration test for the real model-viewer component, isolated browser profile.

uv run --with playwright python -c "import runpy; runpy.run_path('frontend/tests/bodyModel3D.browser.py', run_name='__main__')"
BODY_MODEL_URL optionally targets the public production route.
"""
import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright
from backend.core.temp_paths import codeyun_temp_root


async def main():
    url = os.environ.get('BODY_MODEL_URL', 'http://localhost:5173/tools/body-map?ui=0')
    output = Path(codeyun_temp_root('body-model-3d'))
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            context = await browser.new_context(viewport={'width': 1280, 'height': 1000}, accept_downloads=True)
            page = await context.new_page()
            errors = []
            requested_models = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: requested_models.append(request.url) if '.glb' in request.url else None)
            await page.goto(url)
            model = page.locator('model-viewer')
            await page.wait_for_function("document.querySelector('model-viewer')?.loaded === true", timeout=60000)
            await page.wait_for_function("document.querySelector('model-viewer')?.modelIsVisible === true", timeout=15000)
            assert await page.locator('.records').count() == 0
            dimensions = await model.evaluate('(el) => el.getDimensions()')
            assert abs(dimensions['y'] - 1.75) < 0.02, dimensions  # Opaque clothing adds a few mm.
            assert 'male-ordinary.glb' in await model.evaluate('(el) => el.src')
            await page.screenshot(path=str(output / 'full.png'), full_page=True)
            await page.get_by_role('button', name='放大头部', exact=True).click()
            await page.wait_for_function("document.querySelector('model-viewer').getCameraTarget().y > 1.60")
            await page.wait_for_function("document.querySelector('model-viewer').getCameraOrbit().radius < 0.7")
            await model.evaluate('(el) => el.jumpCameraToGoal()')
            await page.screenshot(path=str(output / 'head-front.png'), full_page=True)
            await page.get_by_role('button', name='左侧', exact=True).click()
            await page.wait_for_function("Math.abs(document.querySelector('model-viewer').getCameraOrbit().theta - Math.PI / 2) < 0.02")
            await page.screenshot(path=str(output / 'head-side.png'), full_page=True)
            bounds = await model.bounding_box()
            before = await model.evaluate('(el) => el.getCameraOrbit().theta')
            await page.mouse.move(bounds['x'] + bounds['width'] * .6, bounds['y'] + bounds['height'] * .5)
            await page.mouse.down()
            await page.mouse.move(bounds['x'] + bounds['width'] * .4, bounds['y'] + bounds['height'] * .55, steps=15)
            await page.mouse.up()
            await page.wait_for_function('(before) => Math.abs(document.querySelector("model-viewer").getCameraOrbit().theta - before) > 0.2', arg=before)
            # Wait for damped camera motion by observing the public camera state.
            await model.evaluate('(el) => el.jumpCameraToGoal()')
            before_zoom = await model.evaluate('(el) => el.getCameraOrbit().radius')
            await page.get_by_role('button', name='放大', exact=True).click()
            await page.wait_for_function('(radius) => document.querySelector("model-viewer").getCameraOrbit().radius < radius', arg=before_zoom)
            async with page.expect_download() as info:
                await page.get_by_role('button', name='下载当前图', exact=True).click()
            download = await info.value
            await download.save_as(output / 'snapshot.png')
            assert (output / 'snapshot.png').stat().st_size > 10000
            await page.get_by_role('button', name='全身', exact=True).click()
            await page.wait_for_function("document.querySelector('model-viewer').getCameraOrbit().radius > 3")
            await page.set_viewport_size({'width': 390, 'height': 844})
            await page.get_by_role('button', name='放大头部', exact=True).click()
            await page.wait_for_function("document.querySelector('model-viewer').getCameraOrbit().radius < 0.7")
            await model.evaluate('(el) => el.jumpCameraToGoal()')
            await page.screenshot(path=str(output / 'mobile.png'), full_page=True)
            assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            await page.get_by_role('button', name='女性', exact=True).click()
            await page.wait_for_function("document.querySelector('model-viewer')?.loaded && document.querySelector('model-viewer').src.includes('female-ordinary.glb')")
            await page.set_viewport_size({'width': 1280, 'height': 1000})
            await page.get_by_role('button', name='正面', exact=True).click()
            await model.evaluate('(el) => el.jumpCameraToGoal()')
            await page.screenshot(path=str(output / 'female-ordinary-front.png'), full_page=True)
            await page.get_by_role('button', name='背面', exact=True).click()
            await model.evaluate('(el) => el.jumpCameraToGoal()')
            await page.screenshot(path=str(output / 'female-ordinary-back.png'), full_page=True)
            await model.evaluate("(el) => { el.cameraOrbit='0deg 160deg 2m'; el.cameraTarget='0m 0.9m 0m'; el.jumpCameraToGoal() }")
            await page.screenshot(path=str(output / 'female-ordinary-below.png'), full_page=True)
            await page.get_by_role('button', name='完整体表', exact=True).click()
            assert await page.get_by_role('dialog').is_visible()
            await page.get_by_role('button', name='取消', exact=True).click()
            assert 'ordinary.glb' in await model.evaluate('(el) => el.src')
            assert requested_models and all('-ordinary.glb' in src for src in requested_models), requested_models
            await page.get_by_role('button', name='完整体表', exact=True).click()
            await page.get_by_role('button', name='显示完整体表', exact=True).click()
            await page.wait_for_function("document.querySelector('model-viewer')?.loaded && document.querySelector('model-viewer').src.includes('female-surface.glb')")
            await page.get_by_role('button', name='男性', exact=True).click()
            await page.wait_for_function("document.querySelector('model-viewer')?.loaded && document.querySelector('model-viewer').src.includes('male-surface.glb')")
            await page.get_by_role('button', name='普通模式', exact=True).click()
            await page.wait_for_function("document.querySelector('model-viewer')?.loaded && document.querySelector('model-viewer').src.includes('male-ordinary.glb')")
            await page.get_by_role('button', name='完整体表', exact=True).click()
            await page.get_by_role('button', name='显示完整体表', exact=True).click()
            await page.reload()
            await page.wait_for_function("document.querySelector('model-viewer')?.loaded && document.querySelector('model-viewer').src.includes('male-ordinary.glb')")
            assert not errors, errors
            print(f'PASS: {url}; actual GLB, camera, PNG, mobile, both sexes and modes, disclosure/cancel, covered reset on reload; screenshots {output}')
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
