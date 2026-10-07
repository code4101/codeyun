"""窗口远控缩放回归：隔离浏览器与模拟 API，绝不向真实桌面发送输入。"""
import asyncio
import base64
import json
import os
import re
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

ORIGIN = os.getenv('CODEYUN_BROWSER_TEST_URL', 'http://127.0.0.1:5173').rstrip('/')
ENGINE = os.getenv('CODEYUN_BROWSER_TEST_ENGINE', 'chromium')
FULL_ROUTE = os.getenv('CODEYUN_BROWSER_TEST_ROUTE')


async def main():
    source = '' if FULL_ROUTE else urllib.request.urlopen(ORIGIN + '/src/components/window-projection/WindowProjectionWorkspace.vue').read().decode()
    vue = '' if FULL_ROUTE else re.search(r'"([^" ]*/deps/vue.js[^" ]*)"', source).group(1)
    html = f'''<!doctype html><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body,#app{{height:100%;margin:0;overflow:hidden}}</style><div id="app"></div><script type="module">
    import {{createApp,h}} from '{vue}'; import Projection from '/src/components/window-projection/WindowProjectionWorkspace.vue';
    import '/src/style.css';const app=createApp({{render:()=>h(Projection)}});app.directive('context-menu',{{}});app.mount('#app');</script>'''
    inputs = []
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1000"><rect width="1600" height="1000" fill="#245367"/><path d="M800 0V1000M0 500H1600" stroke="#ccc" stroke-width="4"/><text x="700" y="460" font-size="36" fill="white">Remote window</text></svg>'
    image = 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode()
    windows = [{'id': 'zoom-1', 'title': '测试窗口', 'application': 'codex.exe', 'width': 1600, 'height': 1000},
               {'id': 'zoom-2', 'title': '只读窗口', 'application': 'nemu.exe', 'width': 1600, 'height': 1000, 'read_only': True}]

    async def api(route):
        path = route.request.url.split('/api/')[-1].split('?')[0]
        if path == 'desktop-windows':
            data = {'windows': windows}
        elif path.endswith('/frame'):
            data = {'token': 'test-frame', 'input_token': 'test-input', 'window_id': 'zoom-2' if 'zoom-2' in route.request.url else 'zoom-1',
                    'capture_mode': 'window', 'width': 1600, 'height': 1000, 'image_bytes': 0,
                    'patches': [{'x': 0, 'y': 0, 'image': image}]}
        elif path.endswith('/input'):
            inputs.append(route.request.post_data_json)
            data = {}
        elif path.endswith('/activate'):
            data = {}
        elif FULL_ROUTE and path == 'auth/me':
            data = {'id':1,'username':'fixture','nickname':'fixture','is_superuser':True}
        elif FULL_ROUTE and path == 'access/context':
            data = {'registry_version':1,'subject':{'kind':'user','is_authenticated':True,'is_superuser':True,'user_id':1,'username':'fixture'},
                    'overrides':{},'items':[],'flat_items':{},'effective_keys':['cluster.window-control']}
        elif FULL_ROUTE:
            data = []
        else:
            raise AssertionError(f'Unexpected API: {path}')
        await route.fulfill(content_type='application/json', body=json.dumps(data))

    async with async_playwright() as playwright:
        browser = await getattr(playwright, ENGINE).launch(headless=True)
        try:
            for width, height in [(1440, 900), (820, 1180), (390, 844), (320, 600), (844, 390)]:
                page = await browser.new_page(viewport={'width': width, 'height': height})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                await page.route(ORIGIN + '/api/**', api)
                if FULL_ROUTE:
                    await page.add_init_script("localStorage.setItem('token','isolated-test-token')")
                await page.route('**/__window-zoom-test', lambda r: r.fulfill(content_type='text/html', body=html))
                await page.goto(ORIGIN + (FULL_ROUTE or '/__window-zoom-test'))
                await page.wait_for_function("document.querySelector('canvas')?.width===1600 && document.querySelector('.status-bar')?.textContent.includes('画面已更新')")
                canvas, stage = page.locator('canvas'), page.locator('.stage')
                await page.wait_for_timeout(100)
                before = await canvas.bounding_box()
                viewport = await stage.bounding_box()
                assert before['width'] <= viewport['width'] and before['height'] <= viewport['height'], 'fit'
                await page.get_by_role('button', name='放大画面', exact=True).click()
                await page.wait_for_timeout(80)
                assert (await canvas.bounding_box())['width'] > before['width'] * 1.15, 'zoom in'
                await page.get_by_role('button', name='100%', exact=True).click()
                await page.wait_for_timeout(80)
                assert abs((await canvas.bounding_box())['width'] - 1600) < 1, 'native pixels'
                assert await stage.evaluate('(el)=>el.scrollWidth>el.clientWidth'), 'pan area accessible'

                # A viewport point must map through the displayed, scrolled canvas rectangle.
                rect = await canvas.bounding_box()
                viewport = await stage.bounding_box()
                x, y = viewport['x'] + viewport['width'] / 2, viewport['y'] + viewport['height'] / 2
                expected = ((x - rect['x']) / rect['width'], (y - rect['y']) / rect['height'])
                count = len(inputs)
                await page.mouse.click(x, y)
                await page.wait_for_timeout(850)
                assert len(inputs) == count + 1, 'single native click'
                assert abs(inputs[-1]['x'] - expected[0]) < .002 and abs(inputs[-1]['y'] - expected[1]) < .002, 'scaled click coordinates'
                count = len(inputs)
                await page.mouse.move(x, y)
                await page.keyboard.down('Control')
                await page.mouse.wheel(0, -120)
                await page.keyboard.up('Control')
                await page.wait_for_timeout(250)
                assert (await canvas.bounding_box())['width'] > 1600, 'Ctrl+wheel zoom'
                assert len(inputs) == count, 'local zoom never sends native input'

                # Real browser layout with synthetic two-pointer input works on both engines.
                await page.evaluate('''() => {
                  const c=document.querySelector('canvas'), s=document.querySelector('.stage').getBoundingClientRect();
                  c.setPointerCapture=()=>{}; c.hasPointerCapture=()=>false;
                  const x=s.left+s.width/2,y=s.top+s.height/2;
                  const send=(type,id,dx)=>c.dispatchEvent(new PointerEvent(type,{pointerId:id,pointerType:'touch',button:0,buttons:1,clientX:x+dx,clientY:y,bubbles:true,cancelable:true}));
                  send('pointerdown',10,-20);send('pointerdown',11,20);send('pointermove',10,-35);send('pointermove',11,35);
                  send('pointerup',10,-35);send('pointerup',11,35);
                }''')
                await page.wait_for_timeout(600)
                assert (await canvas.bounding_box())['width'] > 2200, 'pinch zoom'
                assert len(inputs) == count, 'pinch cannot click or drag the native window'
                await page.get_by_role('button', name='适应窗口', exact=True).click()
                await page.set_viewport_size({'width': max(320, width - 50), 'height': height - 70})
                await page.wait_for_timeout(150)
                rect, viewport = await canvas.bounding_box(), await stage.bounding_box()
                assert rect['width'] <= viewport['width'] and rect['height'] <= viewport['height'], 'resize refit'
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'page does not overflow'
                await page.get_by_role('button', name='100%', exact=True).click()
                if width<=760:
                    await page.get_by_role('button', name='显示或隐藏应用窗口清单', exact=True).click()
                await page.locator('.window-list button').filter(has_text='只读窗口').click()
                await page.wait_for_timeout(150)
                await page.get_by_role('button', name='放大画面', exact=True).click()
                await page.locator('.window-list button').filter(has_text='测试窗口').click()
                await page.wait_for_timeout(150)
                assert abs((await canvas.bounding_box())['width'] - 1600) < 1, 'per-window zoom remembered'
                assert not errors, errors
                if width == 390:
                    await page.get_by_role('button', name='显示或隐藏应用窗口清单', exact=True).click()
                    await page.get_by_role('button', name='适应窗口', exact=True).click()
                    await page.wait_for_timeout(100)
                    output = Path(os.environ['TEMP']) / 'codeyun' / 'window-zoom-1584'
                    output.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(output / f'{ENGINE}-phone.png'))
                print(f'PASS {ENGINE} {width}x{height}: fit, zoom, pan, click coordinates, pinch isolation, resize, tabs')
                await page.close()
            if ENGINE == 'chromium':
                page = await browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
                await page.route(ORIGIN + '/api/**', api)
                if FULL_ROUTE:
                    await page.add_init_script("localStorage.setItem('token','isolated-test-token')")
                await page.route('**/__window-zoom-test', lambda r: r.fulfill(content_type='text/html', body=html))
                await page.goto(ORIGIN + (FULL_ROUTE or '/__window-zoom-test'))
                await page.wait_for_function("document.querySelector('canvas')?.width===1600")
                await page.get_by_role('button',name='100%',exact=True).click()
                await page.wait_for_timeout(100)
                stage = page.locator('.stage')
                viewport = await stage.bounding_box()
                x,y = viewport['x']+viewport['width']/2,viewport['y']+viewport['height']/2
                client = await page.context.new_cdp_session(page)
                async def touches(kind, points):
                    await client.send('Input.dispatchTouchEvent',{'type':kind,'touchPoints':[{'x':px,'y':py,'id':i} for i,px,py in points]})
                count=len(inputs)
                before=await stage.evaluate('(el)=>el.scrollLeft')
                await touches('touchStart',[(1,x-20,y)])
                await touches('touchStart',[(1,x-20,y),(2,x+20,y)])
                await touches('touchMove',[(1,x+40,y),(2,x+80,y)])
                await touches('touchEnd',[(2,x+80,y)])
                await touches('touchEnd',[])
                await page.wait_for_timeout(650)
                assert abs(await stage.evaluate('(el)=>el.scrollLeft')-before+60)<3, 'real two-finger pan'
                assert len(inputs)==count, 'real touch pan never sends native actions'
                await touches('touchStart',[(1,x-20,y)])
                await touches('touchStart',[(1,x-20,y),(2,x+20,y)])
                await touches('touchMove',[(1,x-40,y),(2,x+40,y)])
                await touches('touchEnd',[])
                await page.wait_for_timeout(650)
                assert (await page.locator('canvas').bounding_box())['width']>3000, 'real touch pinch'
                assert len(inputs)==count, 'real touch pinch never sends native actions'
                print('PASS chromium native mobile touch: two-finger pan and pinch without remote input')
                await page.close()
        finally:
            await browser.close()


asyncio.run(main())
