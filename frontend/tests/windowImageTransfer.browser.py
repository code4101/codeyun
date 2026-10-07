"""Browser image transfer checks with simulated APIs and clipboard (no desktop input)."""
import asyncio
import base64
from io import BytesIO
import json
import os
import re
import subprocess
import urllib.request

from PIL import Image
from playwright.async_api import async_playwright

ORIGIN = os.getenv('CODEYUN_BROWSER_TEST_URL', 'http://127.0.0.1:5173').rstrip('/')
FULL_ROUTE = os.getenv('CODEYUN_BROWSER_TEST_ROUTE')


async def main():
    source = '' if FULL_ROUTE else urllib.request.urlopen(ORIGIN + '/src/components/window-projection/WindowProjectionWorkspace.vue').read().decode()
    vue = '' if FULL_ROUTE else re.search(r'"([^" ]*/deps/vue.js[^" ]*)"', source).group(1)
    html = f'''<meta charset="utf-8"><style>html,body,#app{{height:100%;margin:0}}</style><div id="app"></div>
    <script type="module">import {{createApp,h}} from '{vue}';
    import Projection from '/src/components/window-projection/WindowProjectionWorkspace.vue';
    import '/src/style.css';const app=createApp({{render:()=>h(Projection)}});app.directive('context-menu',{{}});app.mount('#app');</script>'''
    out = BytesIO(); Image.new('RGB', (800, 600), (25, 70, 100)).save(out, 'PNG')
    png = out.getvalue(); image = 'data:image/png;base64,' + base64.b64encode(png).decode()
    inputs = []
    copy_kind = 'image'
    frame_requests = []
    active_frames = 0
    max_active_frames = 0
    frame_delay = 0
    frame_bytes = 0
    windows = [{'id': 'image-1', 'title': '图片测试窗口', 'application': 'codex.exe', 'width': 800, 'height': 600},
               {'id': 'image-2', 'title': '另一窗口', 'application': 'notepad.exe', 'width': 800, 'height': 600}]

    async def api(route):
        nonlocal active_frames, max_active_frames
        path = route.request.url.split('/api/')[-1].split('?')[0]
        if path == 'desktop-windows': data = {'windows': windows}
        elif path.endswith('/frame'):
            frame_requests.append(route.request.url)
            active_frames += 1
            max_active_frames = max(max_active_frames, active_frames)
            if frame_delay: await asyncio.sleep(frame_delay)
            data = {'token': 'frame', 'input_token': 'input', 'window_id': 'image-2' if 'image-2' in route.request.url else 'image-1',
                    'capture_mode': 'window', 'width': 800, 'height': 600, 'image_bytes': frame_bytes,
                    'patches': [{'x': 0, 'y': 0, 'image': image}]}
        elif path.endswith('/input'):
            inputs.append(route.request.post_data_json)
            data = {'accepted': True}
            if inputs[-1]['action'] == 'copy': data['image' if copy_kind == 'image' else 'text'] = image if copy_kind == 'image' else '远端选中文字'
        elif path.endswith('/activate'): data = {}
        elif path.endswith('/clipboard/image'): data = {'image': image}
        elif FULL_ROUTE and path == 'auth/me':
            data = {'id': 1, 'username': 'fixture', 'nickname': 'fixture', 'is_superuser': True}
        elif FULL_ROUTE and path == 'access/context':
            data = {'registry_version':1,'subject':{'kind':'user','is_authenticated':True,'is_superuser':True,'user_id':1,'username':'fixture'},
                    'overrides':{},'items':[],'flat_items':{},'effective_keys':['cluster.window-control']}
        elif FULL_ROUTE: data = []
        else: raise AssertionError(path)
        try: await route.fulfill(content_type='application/json', body=json.dumps(data))
        finally:
            if path.endswith('/frame'): active_frames -= 1

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={'width': 1200, 'height': 1000})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.add_init_script('''window.copied=[]; Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>window.copied.push({text}),write:async items=>{
              const blob=await items[0].getType('image/png'), bitmap=await createImageBitmap(blob);
              window.copied.push({type:blob.type,width:bitmap.width,height:bitmap.height});bitmap.close();
            }}});''')
            await page.route(ORIGIN + '/api/**', api)
            if FULL_ROUTE: await page.add_init_script("localStorage.setItem('token','isolated-test-token')")
            await page.route('**/__window-image-test', lambda r: r.fulfill(content_type='text/html', body=html))
            await page.goto(ORIGIN + (FULL_ROUTE or '/__window-image-test'))
            await page.wait_for_function("document.querySelector('canvas')?.width===800 && document.querySelector('.status-bar')?.textContent.includes('画面已更新')")
            await page.get_by_role('button', name='截图', exact=True).click()
            await page.wait_for_function('window.copied.length===1')
            assert await page.evaluate('window.copied[0]') == {'type': 'image/png', 'width': 800, 'height': 600}
            assert await page.locator('textarea:not(.typing-input),input[type=file]').count() == 0
            assert await page.get_by_role('button', name='传递', exact=True).count() == 0
            canvas = page.locator('canvas'); box = await canvas.bounding_box()
            await page.mouse.click(box['x'] + box['width'] * .25, box['y'] + box['height'] * .75)
            assert await page.locator('.typing-input').evaluate('el=>document.activeElement===el'), 'click focuses the remote keyboard bridge'
            # Paste immediately, before the double-click timer fires. The pending
            # click must reach the target before paste, preserving its caret.
            await canvas.evaluate(r'''(el)=>{
              const data=new DataTransfer();data.setData('text/plain','本地剪贴板\n中文');
              el.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));
            }''')
            await page.keyboard.press('Enter')
            for _ in range(100):
                if len(inputs) >= 3: break
                await page.wait_for_timeout(20)
            assert [i['action'] for i in inputs] == ['click', 'paste', 'key'], inputs
            assert inputs[1]['text'] == '本地剪贴板\n中文' and inputs[2]['key'] == 'enter'
            assert 'x' not in inputs[1] and 'y' not in inputs[1], 'paste must use existing caret without clicking again'
            # Image clipboard data goes straight to the same caret, with no UI.
            await canvas.evaluate('''async (el,image)=>{
              const blob=await (await fetch(image)).blob(), data=new DataTransfer();
              data.items.add(new File([blob],'pasted.png',{type:'image/png'}));
              el.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));
            }''', image)
            for _ in range(100):
                if len(inputs) >= 4: break
                await page.wait_for_timeout(20)
            assert inputs[-1]['action'] == 'paste' and inputs[-1]['image'].startswith('data:image/png;base64,')
            await page.keyboard.press('Control+c')
            await page.wait_for_function('window.copied.length===2')
            assert inputs[-1]['action'] == 'copy'
            copy_kind = 'text'
            await page.keyboard.press('Control+c')
            await page.wait_for_function('window.copied.length===3')
            assert await page.evaluate('window.copied[2].text') == '远端选中文字'
            # The search field keeps ordinary LOCAL copy/paste behavior.
            count = len(inputs)
            await page.get_by_role('textbox', name='搜索本机窗口').click()
            await page.keyboard.press('Control+c')
            await page.wait_for_timeout(80)
            assert len(inputs) == count
            await page.locator('.window-list button').filter(has_text='另一窗口').click()
            await page.wait_for_function("document.activeElement?.tagName==='TEXTAREA'")
            await canvas.evaluate('''el=>{const data=new DataTransfer();data.setData('text/plain','第二窗口');
              el.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));}''')
            for _ in range(100):
                if len(inputs) > count: break
                await page.wait_for_timeout(20)
            assert inputs[-1]['window_id'] == 'image-2' and inputs[-1]['text'] == '第二窗口'
            assert not errors, errors
            print('PASS: one-click screenshot, direct text/image paste, click/paste/Enter ordering, Ctrl+C image/text, local search isolation, target switch')
            # Real browser typing uses the invisible native editing bridge.
            count = len(inputs)
            await canvas.click(position={'x': 100, 'y': 100})
            await page.keyboard.type('Ab 12!?')
            await page.keyboard.press('Enter')
            for _ in range(100):
                if len(inputs) >= count + 3: break
                await page.wait_for_timeout(20)
            typed = inputs[count:]
            assert [i['action'] for i in typed] == ['click', 'type', 'key'], typed
            assert typed[1]['text'] == 'Ab 12!?' and typed[2]['key'] == 'enter'
            assert 'x' not in typed[1] and 'y' not in typed[1]
            count = len(inputs)
            bridge = page.locator('.typing-input')
            await bridge.evaluate('''el=>{
              el.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));
              el.value='zhongwen';el.dispatchEvent(new InputEvent('input',{data:'zhongwen',isComposing:true,bubbles:true}));
              el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true,cancelable:true}));
            }''')
            await page.wait_for_timeout(80)
            assert len(inputs) == count, 'IME candidates and confirmation Enter stay local'
            await bridge.evaluate('''el=>{
              el.value='中文🙂';el.dispatchEvent(new CompositionEvent('compositionend',{data:'中文🙂',bubbles:true}));
              el.dispatchEvent(new InputEvent('input',{data:'中文🙂',inputType:'insertText',bubbles:true}));
            }''')
            for _ in range(100):
                if len(inputs) > count: break
                await page.wait_for_timeout(20)
            assert len(inputs) == count + 1 and inputs[-1]['action'] == 'type' and inputs[-1]['text'] == '中文🙂'
            await bridge.evaluate("el=>el.dispatchEvent(new InputEvent('beforeinput',{inputType:'deleteContentBackward',bubbles:true,cancelable:true}))")
            for _ in range(100):
                if len(inputs) > count + 1: break
                await page.wait_for_timeout(20)
            assert inputs[-1]['key'] == 'backspace', 'mobile deletion uses remote caret'
            print('PASS: direct typing, batching, click/type/Enter ordering, Chinese IME single commit, emoji, mobile deletion')
            automatic = page.get_by_role('checkbox', name='自动更新', exact=True)
            assert await page.get_by_role('checkbox', name='操作', exact=True).count() == 0
            # Operation refresh is the default, without a separate UI control.
            count = len(frame_requests)
            await canvas.focus(); await page.keyboard.press('Enter')
            await page.wait_for_timeout(650)
            assert len(frame_requests) == count + 2, 'preflight plus operation-triggered refresh'
            await automatic.check()
            rate = page.get_by_role('spinbutton', name='自动更新频率')
            await rate.fill('5'); await rate.blur()
            count = len(frame_requests)
            await canvas.focus(); await page.keyboard.press('Enter')
            await page.wait_for_timeout(650)
            assert len(frame_requests) == count + 1, 'automatic observation replaces post-operation refresh'
            # Switching modes while the operation frame is in flight must not
            # recreate its delayed follow-up after automatic observation starts.
            await automatic.uncheck()
            frame_delay = .6
            count = len(frame_requests)
            await canvas.focus(); await page.keyboard.press('Enter')
            for _ in range(100):
                if len(frame_requests) == count + 2: break
                await page.wait_for_timeout(20)
            assert len(frame_requests) == count + 2 and active_frames == 1
            await automatic.check()
            await page.wait_for_timeout(3300)
            assert len(frame_requests) == count + 2, 'mode switch must cancel delayed operation follow-up'
            frame_delay = 0
            await rate.fill('0.5'); await rate.blur()
            count = len(frame_requests)
            await page.wait_for_timeout(1200)
            assert 2 <= len(frame_requests)-count <= 3, 'configured interval'
            await page.get_by_role('combobox', name='更新频率单位').select_option('fps')
            assert abs(float(await rate.input_value())-2) < .001, 'units represent the SAME cadence'
            await rate.fill('10'); await rate.blur()
            count = len(frame_requests)
            await page.wait_for_timeout(1000)
            assert 6 <= len(frame_requests)-count <= 12, 'continuous high-frequency update'
            await automatic.uncheck()
            await page.wait_for_timeout(150)
            # Slow/large frames must reduce FPS and never form an HTTP backlog.
            frame_delay, frame_bytes = .25, 900000
            max_active_frames = 0
            count = len(frame_requests)
            await automatic.check()
            await page.wait_for_timeout(2500)
            assert len(frame_requests)-count <= 3, 'image budget must limit large frames'
            assert max_active_frames == 1, 'one outstanding frame request per view'
            await automatic.uncheck(); await page.wait_for_timeout(400)
            frame_delay, frame_bytes = 0, 0
            await automatic.check()
            await page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))")
            await page.wait_for_timeout(150)
            count = len(frame_requests)
            await page.wait_for_timeout(400)
            assert len(frame_requests) == count, 'background tab pauses automatic capture'
            await page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'))")
            await page.wait_for_timeout(350)
            assert len(frame_requests) > count, 'foreground resumes automatically'
            await automatic.uncheck()
            count = len(frame_requests)
            await canvas.focus(); await page.keyboard.press('Enter')
            await page.wait_for_timeout(650)
            assert len(frame_requests) == count + 2, 'disabling automatic observation restores operation fallback'
            assert not errors, errors
            print('PASS: operation fallback, no competing refresh timers, cadence units, 10 FPS target, bandwidth backpressure, no frame queue, background pause/resume')
            if os.getenv('CODEYUN_NATIVE_CLIPBOARD_TEST') == '1':
                def check_lease():
                    subprocess.run(['uv','run','python','scripts/resource_lease.py','check','--token',os.environ['CODEYUN_GUI_LEASE_TOKEN']], check=True, capture_output=True)
                native_context = await browser.new_context(permissions=['clipboard-read','clipboard-write'])
                native_page = await native_context.new_page()
                await native_page.route(ORIGIN + '/api/**', api)
                if FULL_ROUTE: await native_page.add_init_script("localStorage.setItem('token','isolated-test-token')")
                await native_page.route('**/__window-image-test', lambda r:r.fulfill(content_type='text/html',body=html))
                await native_page.goto(ORIGIN + (FULL_ROUTE or '/__window-image-test'))
                await native_page.wait_for_function("document.querySelector('canvas')?.width===800")
                check_lease()
                await native_page.evaluate('''async()=>{window.saved=[];for(const item of await navigator.clipboard.read()){
                  const payload={};for(const type of item.types)payload[type]=await item.getType(type);if(item.types.length)window.saved.push(new ClipboardItem(payload));}}''')
                try:
                    await native_page.evaluate("window.trustedPastes=[];document.addEventListener('paste',e=>window.trustedPastes.push(e.isTrusted))")
                    check_lease()
                    await native_page.evaluate("navigator.clipboard.writeText('native-clipboard-bridge-test')")
                    await native_page.locator('.stage').click(position={'x':2,'y':2})
                    count = len(inputs)
                    check_lease()
                    await native_page.keyboard.press('Control+v')
                    await native_page.wait_for_timeout(500)
                    assert len(inputs) == count + 1 and inputs[-1].get('text') == 'native-clipboard-bridge-test', 'REAL Ctrl+V on the remote stage must reach backend'
                    check_lease()
                    await native_page.evaluate('''async image=>{const blob=await(await fetch(image)).blob();await navigator.clipboard.write([new ClipboardItem({'image/png':blob})])}''', image)
                    count = len(inputs)
                    check_lease()
                    await native_page.keyboard.press('Control+v')
                    await native_page.wait_for_timeout(500)
                    assert len(inputs) == count + 1 and inputs[-1].get('image','').startswith('data:image/png;base64,')
                    assert await native_page.evaluate('window.trustedPastes.every(Boolean) && window.trustedPastes.length===2')
                    print('PASS: REAL browser Ctrl+V transfers native text/image clipboard from stage background')
                finally:
                    check_lease()
                    await native_page.evaluate('async()=>{if(window.saved.length)await navigator.clipboard.write(window.saved);else await navigator.clipboard.writeText("")}')
                    await native_context.close()
        finally: await browser.close()


if __name__ == '__main__': asyncio.run(main())
