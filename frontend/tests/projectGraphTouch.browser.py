"""Trusted multi-touch regression on the shipped editor, isolated in-memory PRG.
Run: uv run --with playwright python frontend/tests/projectGraphTouch.browser.py
"""
import asyncio
import re
import sys
import urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.core.project_graph.codec import import_prg
from playwright.async_api import async_playwright


async def main():
    source = urllib.request.urlopen('http://localhost:5173/src/components/PlateEditor.vue').read().decode()
    vue = re.search(r'"([^" ]*/deps/vue.js[^" ]*)"', source).group(1)
    html = '''<style>html,body,#app{height:100%;margin:0}</style><div id="app"></div><script type="module">
    import {createApp,h,ref} from 'VUE_URL';
    import Editor from '/src/plugins/modules/project-graph/ProjectGraphEditor.vue';
    window.errors=[];window.details=null;window.gallery=null;
    const storage={read:async()=>window.doc,write:async(id,title,bytes,revision)=>{
      return window.doc={id,title,bytes,revision:revision+1,updatedAt:Date.now()};}};
    createApp({setup(){const editor=ref();window.flush=()=>editor.value.flush();
      window.run=id=>editor.value.executeMenu(id);
      return()=>h(Editor,{ref:editor,documentId:'touch-fixture',title:'touch-fixture',storage,
      viewStateKey:'touch-fixture:view',detailsActive:true,galleryActive:true,
      onDetails:v=>window.details=v,onGallery:v=>window.gallery=v,onError:e=>window.errors.push(e)});
    }}).mount('#app');</script>'''.replace('VUE_URL', vue)
    async with async_playwright() as p:
        webkit = '--webkit' in sys.argv
        browser = await p.webkit.launch(headless=True) if webkit else await p.chromium.launch(channel='chrome', headless=True)
        try:
            page = await browser.new_page(viewport={'width':1024,'height':768}, has_touch=True)
            if webkit:
                # The Windows WebKit test port ships without Web Audio. Audio
                # is outside this regression; Safari on iPad provides AudioContext.
                await page.add_init_script("window.AudioContext ??= class {}; localStorage.setItem('codeyun.pg.settings.settings.json.soundEnabled', 'false');")
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.route('**/__pg-touch-test', lambda r:r.fulfill(content_type='text/html',body=html))
            await page.goto('http://localhost:5173/__pg-touch-test')
            try:
                await page.locator('iframe:not(.pending)').wait_for(timeout=15000 if webkit else 60000)
            except Exception:
                print('BOOT ERRORS', errors, await page.evaluate('window.errors'), flush=True)
                print('FRAME', [(f.url, (await f.locator('body').inner_text())[:500]) for f in page.frames], flush=True)
                raise
            frame = page.frame_locator('iframe')
            canvas = frame.locator('canvas').first
            cdp = None if webkit else await page.context.new_cdp_session(page)
            box = await canvas.bounding_box()
            await frame.locator('canvas').evaluate("el => { window.touchTrace=[]; for(const type of ['pointerdown','pointerup','pointercancel']) document.addEventListener(type, e=>window.touchTrace.push([type,e.pointerType,e.detail,e.clientX,e.clientY]), true); }")
            active = {}
            async def touch(kind, points):
                if cdp:
                    await cdp.send('Input.dispatchTouchEvent', {'type':kind,'touchPoints':[
                        {'id':i,'x':box['x']+x,'y':box['y']+y,'radiusX':5,'radiusY':5}
                        for i,x,y in points]})
                    return
                # WebKit has no CDP multi-touch injection. Exercise its PointerEvent
                # runtime for drags/pinch; stationary taps below use real touch input.
                next_points = {i:(x,y) for i,x,y in points}
                events = []
                for i,(x,y) in next_points.items():
                    if kind != 'touchEnd':
                        events.append({'type':'pointermove' if i in active else 'pointerdown','pointerId':i,'clientX':box['x']+x,'clientY':box['y']+y,'buttons':1})
                for i,(x,y) in active.items():
                    if i not in next_points:
                        events.append({'type':'pointerup','pointerId':i,'clientX':box['x']+x,'clientY':box['y']+y,'buttons':0})
                active.clear(); active.update(next_points)
                await canvas.evaluate("(el,events)=>{for(const e of events) el.dispatchEvent(new PointerEvent(e.type,{...e,pointerType:'touch',bubbles:true,cancelable:true,isPrimary:e.pointerId===1}));}", events)
            async def tap(x,y):
                if webkit:
                    await page.touchscreen.tap(box['x']+x,box['y']+y)
                    return
                await touch('touchStart', [(1,x,y)]); await touch('touchEnd', [])
            async def swipe(a,b):
                await touch('touchStart', [(1,*a)])
                for step in range(1,6):
                    x=a[0]+(b[0]-a[0])*step/5; y=a[1]+(b[1]-a[1])*step/5
                    await touch('touchMove', [(1,x,y)])
                await touch('touchEnd', [])
            async def objects():
                await page.evaluate('window.flush()')
                return import_prg(bytes(await page.evaluate('Array.from(window.doc.bytes)')))
            async def stage():
                return {k:v for k,v in (await objects()).items() if not k.startswith('@')}
            assert len(await stage()) == 0
            for _ in range(6): await swipe((750,500),(800,550))
            assert len(await stage()) == 0, 'swipes must never create shapes'
            # Simultaneous and staggered finger release must never generate a double click.
            await touch('touchStart', [(1,400,450),(2,600,450)])
            await touch('touchMove', [(1,350,420),(2,650,480)])
            await touch('touchEnd', [(1,350,420)])
            await touch('touchMove', [(1,400,470)])
            await touch('touchEnd', [])
            assert len(await stage()) == 0, 'pinch/release must never create shapes'
            for x,name in [(260,'First'),(600,'Second')]:
                await tap(x,250); await page.wait_for_timeout(80); await tap(x,250)
                try:
                    await frame.locator('textarea').fill(name, timeout=3000)
                except Exception:
                    print('TRACE', await frame.locator('canvas').evaluate('()=>window.touchTrace'))
                    print('STAGE', await stage(), 'ERRORS', errors)
                    raise
                await page.keyboard.press('Escape'); await page.wait_for_timeout(100)
                assert await frame.locator('textarea').count() == 0
            before = await stage()
            assert len(before) == 2, before
            # Single touch selects; movement uses native geometry and undo.
            await tap(270,260)
            await page.wait_for_function("window.details?.title==='First'")
            assert await frame.locator('textarea').count() == 0, 'single tap must not edit'
            await swipe((270,260),(350,340))
            await page.wait_for_timeout(300)
            moved = await stage()
            assert before != moved, ('drag must move selected node', await frame.locator('canvas').evaluate('()=>window.touchTrace'), moved)
            await page.evaluate("window.run('undo')")
            restored = await stage()
            assert restored == before, 'node drag must undo in one step'
            await touch('touchStart', [(1,270,260)])
            await touch('touchMove', [(1,300,280)])
            await touch('touchStart', [(1,300,280),(2,650,500)])
            stopped = await stage()
            await touch('touchEnd', [(1,300,280)])
            await touch('touchMove', [(1,370,350)])
            await touch('touchEnd', [])
            assert await stage() == stopped, 'remaining finger must not resume node movement'
            assert len(stopped) == 2, 'switching to two fingers must not create shapes'
            if stopped != restored:
                await page.evaluate("window.run('undo')")
                assert await stage() == restored
            await frame.locator('[data-touch-tool="multi"]').tap()
            await tap(270,260); await tap(610,260)
            await page.wait_for_timeout(300)
            assert len(await page.evaluate('window.gallery.selection.ids')) == 1
            await tap(270,260)
            await page.wait_for_timeout(300)
            assert len(await page.evaluate('window.gallery.selection.ids')) == 2
            await tap(270,260)
            await page.wait_for_timeout(300)
            assert len(await page.evaluate('window.gallery.selection.ids')) == 1
            async def node_point(name):
                # Personal viewport and serialized node geometry are public boundaries.
                await page.wait_for_timeout(300)
                view = await canvas.evaluate("()=>JSON.parse(localStorage.getItem('touch-fixture:view'))")
                node = next(v for v in (await stage()).values() if v.get('text') == name)
                rect = node['collisionBox']['shapes'][0]
                return ((rect['location']['x'] + rect['size']['x']/2 - view['x'])*view['scale'] + box['width']/2,
                        (rect['location']['y'] + rect['size']['y']/2 - view['y'])*view['scale'] + box['height']/2)
            # Start linking from the current selection; touching blank space only navigates.
            await frame.locator('[data-touch-tool="connect"]').tap()
            await tap(*await node_point('First'))
            linked = await stage()
            assert len(linked) == 3, 'point-to-point link must create one edge'
            await page.evaluate("window.run('undo')")
            assert len(await stage()) == 2
            await page.evaluate("window.run('redo')")
            assert len(await stage()) == 3
            await swipe((750,500),(780,520))
            assert len(await stage()) == 3
            await frame.locator('[data-touch-tool="select"]').tap()
            # Explicit frame selection works without sacrificing default blank-space pan.
            a,b = await node_point('First'),await node_point('Second')
            await frame.locator('[data-touch-tool="box"]').tap()
            await swipe((min(a[0],b[0])-100,min(a[1],b[1])-100),
                        (max(a[0],b[0])+140,max(a[1],b[1])+100))
            await page.wait_for_timeout(300)
            assert len(await page.evaluate('window.gallery.selection.ids')) >= 2
            assert await frame.locator('[data-touch-tool="select"]').get_attribute('aria-pressed') == 'true'
            # Long press displays an actual context menu without adding objects.
            await touch('touchStart', [(1,850,600)])
            await page.wait_for_timeout(650)
            await touch('touchEnd', [])
            assert await frame.locator('[role="menu"]').count() > 0
            await page.keyboard.press('Escape')
            await frame.locator('[data-touch-tool="draw"]').tap()
            await swipe((750,500),(800,550))
            assert len(await stage()) == 4, 'finger drawing requires explicit drawing tool'
            await page.evaluate("window.run('undo')")
            assert len(await stage()) == 3
            assert not errors, errors
            assert not await page.evaluate('window.errors'), await page.evaluate('window.errors')
            print('PASS', 'WebKit' if webkit else 'Chrome', ': swipes, pinch/release, genuine double tap, selection, drag/undo, multi-select, linking/undo/redo, frame selection, long press, explicit drawing')
        finally:
            await browser.close()


asyncio.run(main())
