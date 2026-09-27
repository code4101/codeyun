"""Real-browser docking regression (isolated Chrome, no login or saved user data).
Run against the local Vite server: uv run --with playwright python frontend/tests/dockResize.browser.py
"""
import asyncio,re,urllib.request
from playwright.async_api import async_playwright
async def main():
    source=urllib.request.urlopen('http://localhost:5173/src/components/docking/DockWorkspace.vue').read().decode()
    vue=re.search(r'"([^" ]*/deps/vue.js[^" ]*)"',source).group(1)
    html=f'''<style>html,body,#app{{height:100%;margin:0}}iframe{{border:0;width:100%;height:100%;flex:1}}</style><div id="app"></div><script type="module">
    import {{createApp,h}} from '{vue}';import Dock from '/src/components/docking/DockWorkspace.vue';import {{useDockLayout}} from '/src/components/docking/useDockLayout.ts';
    const app=createApp({{setup(){{const dock=useDockLayout(null,[{{id:'left1',title:'L1',icon:'document',position:'left',open:true}},{{id:'left2',title:'L2',icon:'document',position:'left',open:true}},{{id:'right',title:'R',icon:'document',position:'right',open:true}},{{id:'bottom',title:'B',icon:'document',position:'bottom',open:true}}]);const frame=()=>h('iframe',{{srcdoc:'<body style="background:#ddd">iframe</body>'}});return()=>h(Dock,{{dock}},{{default:frame,left1:frame,left2:frame,right:frame,bottom:frame}})}}}});app.directive('context-menu',{{}});app.mount('#app');</script>'''
    async with async_playwright() as p:
      browser=await p.chromium.launch(channel='chrome',headless=True)
      try:
        page=await browser.new_page(viewport={'width':1400,'height':900})
        await page.route('**/__dock-resize-test',lambda r:r.fulfill(content_type='text/html',body=html))
        await page.goto('http://localhost:5173/__dock-resize-test')
        await page.locator('.dock-edge.left').wait_for()
        await page.wait_for_timeout(500)
        await page.evaluate("document.addEventListener('pointerdown',e=>window.testPointerId=e.pointerId)")
        failures=[]
        for side,dx,dy in [('left',90,0),('right',-80,0),('bottom',0,-60)]:
          region=page.locator('.dock-region.'+side)
          before=await region.bounding_box()
          edge=await page.locator('.dock-edge.'+side).bounding_box()
          x=edge['x']+edge['width']/2;y=edge['y']+edge['height']/2
          await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x+dx,y+dy);await page.mouse.up();await page.wait_for_timeout(100)
          after=await region.bounding_box()
          actual=after['height' if side=='bottom' else 'width']-before['height' if side=='bottom' else 'width']
          expected=abs(dx or dy)
          print(side,actual,'expected',expected)
          if abs(actual-expected)>2:failures.append(side)
          # No mouse button held: moving back must not continue a lost drag.
          await page.mouse.move(x,y);await page.wait_for_timeout(50)
          assert await page.locator('.is-resizing').count()==0,'stuck drag'
        assert not failures,failures
        left=page.locator('.dock-region.left')
        for dx in (25,-25,35,-35):
          before=await left.bounding_box()
          edge=await page.locator('.dock-edge.left').bounding_box()
          x=edge['x']+4;y=edge['y']+edge['height']/2
          await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x+dx,y);await page.mouse.up()
          await page.wait_for_timeout(60)
          after=await left.bounding_box()
          assert abs(after['width']-before['width']-dx)<2, 'each press starts a fresh drag'
          await page.mouse.move(x-100,y)
          await page.wait_for_timeout(30)
          assert (await left.bounding_box())['width']==after['width'], 'movement after release must do nothing'
        for end in ('blur','pointercancel','lostpointercapture','missing-up'):
          edge=await page.locator('.dock-edge.left').bounding_box()
          x=edge['x']+4;y=edge['y']+edge['height']/2
          await page.mouse.move(x,y);await page.mouse.down()
          await page.wait_for_timeout(20)
          if end=='blur': await page.evaluate("window.dispatchEvent(new Event('blur'))")
          elif end=='lostpointercapture':
            await page.mouse.move(x+1,y)
            await page.evaluate("document.querySelector('.dock-edge.left').releasePointerCapture(window.testPointerId)")
          else: await page.evaluate("kind=>window.dispatchEvent(new PointerEvent(kind==='missing-up'?'pointermove':'pointercancel',{pointerId:window.testPointerId,buttons:0}))",end)
          await page.wait_for_timeout(60)
          # For lost capture, the browser delivers its notification on the next pointer event.
          await page.mouse.move(x+15,y)
          await page.wait_for_timeout(60)
          assert await page.locator('.is-resizing').count()==0,end
          width=(await left.bounding_box())['width']
          await page.mouse.move(x+50,y);await page.mouse.up();await page.wait_for_timeout(30)
          assert (await left.bounding_box())['width']==width,end
        splitter=page.locator('.dock-splitter.left')
        edge=await splitter.bounding_box()
        before=await page.locator('[data-dock-tool=left1]').bounding_box()
        x=edge['x']+edge['width']/2;y=edge['y']+edge['height']/2
        await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x,y+45);await page.mouse.up();await page.wait_for_timeout(60)
        after=await page.locator('[data-dock-tool=left1]').bounding_box()
        assert abs(after['height']-before['height']-45)<2, 'internal splitter over iframe'
        print('PASS: repeated drag, release, cancel, lost capture, blur and internal splitter')
      finally:await browser.close()
asyncio.run(main())
