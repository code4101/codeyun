"""Run against Vite: uv run --with playwright python frontend/tests/workspaceMenu.browser.py"""
import asyncio,re,urllib.request
from playwright.async_api import async_playwright
async def main():
 source=urllib.request.urlopen('http://localhost:5173/src/components/editor-workspace/WorkspaceMenu.vue').read().decode()
 vue=re.search(r'"([^" ]*/deps/vue.js[^" ]*)"',source).group(1)
 html=f"""<meta charset="UTF-8"><div id="app"></div><script type="module">
 import {{createApp,h}} from '{vue}';import Menu from '/src/components/editor-workspace/WorkspaceMenu.vue';
 import '/node_modules/element-plus/dist/index.css';
 const items=[{{id:'window',label:'窗口',children:[{{id:'grid',label:'背景网格',children:[{{id:'dots',label:'网点'}}]}}]}},{{id:'about',label:'关于',children:[{{id:'license',label:'许可'}}]}}];
 window.commands=[];createApp({{render:()=>h('div',{{style:'height:800px;--reader-panel:#252a32;--reader-text:#fff;--reader-border:#444;--reader-hover:#333'}},[h(Menu,{{items,onSelect:id=>window.commands.push(id)}}),h('button',{{style:'position:absolute;top:500px;left:700px'}},'外部')])}}).mount('#app');</script>"""
 async with async_playwright() as p:
  browser=await p.chromium.launch(channel='chrome',headless=True)
  try:
   page=await browser.new_page(viewport={'width':1000,'height':800})
   page.set_default_timeout(10000)
   page.on('pageerror',lambda error:print(str(error),flush=True))
   await page.route('**/__workspace-menu-test',lambda route:route.fulfill(content_type='text/html',body=html))
   await page.goto('http://localhost:5173/__workspace-menu-test')
   top=page.locator('.el-sub-menu__title').filter(has_text='窗口')
   grid=page.locator('.el-sub-menu__title').filter(has_text='背景网格')
   dots=page.get_by_role('menuitem',name='网点',exact=True)
   await top.hover();await grid.wait_for(state='visible')
   await grid.hover();await dots.wait_for(state='visible')
   await page.get_by_role('button',name='外部').hover();await grid.wait_for(state='hidden')
   await top.hover();await top.click()
   await page.get_by_role('button',name='外部').hover();await page.wait_for_timeout(400)
   assert await grid.is_visible()
   await grid.hover();await grid.click()
   await page.get_by_role('button',name='外部').hover();await page.wait_for_timeout(400)
   assert await dots.is_visible()
   await page.keyboard.press('Escape');await grid.wait_for(state='hidden')
   await top.hover();await top.click();await top.click();await grid.wait_for(state='hidden')
   await page.get_by_role('button',name='外部').hover()
   await top.hover();await top.click();await page.get_by_role('button',name='外部').click();await grid.wait_for(state='hidden')
   await top.hover();await grid.hover();await dots.click();assert await page.evaluate('window.commands')==['dots']
   await grid.wait_for(state='hidden')
   print('PASS: hover cascade, pointer travel, click pin, second click, Escape, outside click and command dismissal')
  finally:await browser.close()
asyncio.run(main())
