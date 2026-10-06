"""真实 Chromium 验证共享 Shell/Pane。先启动 Vite，再运行 uv run --with playwright python frontend/tests/workbenchLayout.browser.py。"""
import asyncio
import json
import re
import urllib.request
from playwright.async_api import async_playwright

ORIGIN = 'http://127.0.0.1:5173'


async def main():
    source = urllib.request.urlopen(ORIGIN + '/src/components/docking/DockWorkspace.vue').read().decode()
    vue = re.search(r'"([^" ]*/deps/vue.js[^" ]*)"', source).group(1)
    html = '''<!doctype html><meta charset="UTF-8"><div id="app"></div><script type="module">
import {createApp,h,ref} from 'VUE_URL';
import Shell from '/src/components/layout/WorkbenchShell.vue';
import Pane from '/src/components/layout/WorkbenchPane.vue';
import Surface from '/src/standard/pdf/library/BookReaderSurface.vue';
import Tabs from '/src/components/editor-workspace/EditorTabs.vue';
import Dock from '/src/components/docking/DockWorkspace.vue';
import {useDockLayout} from '/src/components/docking/useDockLayout.ts';
import '/src/style.css';
const long = name => h('div',{'data-long':name,style:'height:6000px;flex:none;overflow-wrap:anywhere'},name+' 超长内容');
createApp({setup(){
 const search=ref('initial query');
 const dock=useDockLayout(null,[{id:'toc',title:'目录',icon:'document',position:'left',open:true},
 {id:'search',title:'搜索',icon:'search',position:'left'},
 {id:'info',title:'信息',icon:'info',position:'right',open:true},
 {id:'logs',title:'日志',icon:'info',position:'bottom',open:true}]);
 window.dock=dock;
 return()=>h(Shell,{}, {
 top:()=>h('div',{'data-chrome':'top',style:'height:32px'},'菜单'),
 left:()=>h('nav',{'data-chrome':'navigation',style:'width:48px;flex:none;background:#f5f7fa'},'导航'),
 bottom:()=>h('div',{'data-chrome':'status',style:'height:24px'},'状态'),
 default:()=>h(Pane,{scroll:'hidden'},()=>h(Surface,{headerless:true},()=>h(Dock,{dock},{
 // 不自行包 Pane：直接验证框架默认正文滚动契约。
 'content-header':()=>h('div',{'data-chrome':'tabs'},[h(Tabs,{tabs:Array.from({length:50},(_,i)=>({id:String(i),title:'Document '+i})),active:'0'})]),
 default:()=>long('body'),
 toc:()=>long('toc'),search:()=>[h('input',{onInput:e=>search.value=e.target.value,value:search.value}),long('search')],info:()=>long('info'),logs:()=>long('logs')
 })))
 });
}}).directive('context-menu',{}).mount('#app');window.ready=true;
</script>'''.replace('VUE_URL', vue)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.route('**/__workbench-layout-test', lambda r: r.fulfill(content_type='text/html', body=html))
            await page.goto(ORIGIN + '/__workbench-layout-test')
            await page.wait_for_function('window.ready && document.querySelector("[data-dock-tool=toc]")')
            results = []
            for width, height in [(1440,900),(900,600),(640,480),(320,480),(640,240)]:
                await page.set_viewport_size({'width':width,'height':height})
                await page.wait_for_timeout(150)
                result = await page.evaluate('''() => {
 const rect = el => {const r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height]};
 const chrome=[...document.querySelectorAll('[data-chrome],.dock-rail')];
 const before=chrome.map(rect);
 const panes=[...document.querySelectorAll('[data-long]')].map(el=>el.parentElement);
 for(const pane of panes) {
   if(pane.clientHeight<=0 || pane.scrollHeight<=pane.clientHeight) throw Error('Missing internal scroll');
   const peerScroll=panes.filter(p=>p!==pane).map(p=>p.scrollTop);
   pane.scrollTop=200;
   if(pane.scrollTop!==200) throw Error('Pane did not scroll');
   if(panes.filter(p=>p!==pane).some((p,i)=>p.scrollTop!==peerScroll[i])) throw Error('Peers scrolled');
 }
 const after=chrome.map(rect);
 if(JSON.stringify(before)!==JSON.stringify(after)) throw Error('Chrome moved');
 for(const [x,y,w,h] of after) if(w<=0||h<=0||x<-.5||y<-.5||x+w>innerWidth+.5||y+h>innerHeight+.5) throw Error('Chrome outside viewport '+JSON.stringify([x,y,w,h]));
 if(document.scrollingElement.scrollHeight>innerHeight || document.scrollingElement.scrollWidth>innerWidth) throw Error('Document grew');
 window.scrollTo(100,100);if(scrollX||scrollY) throw Error('Window scroll');
 const center=document.querySelector('.reader-content').getBoundingClientRect();
 if(center.width<60 || center.height<30) throw Error('Center squeezed');
 return {width:innerWidth,height:innerHeight,independentPanes:panes.length,chromeStable:true};
}''')
                results.append(result)
            body = page.locator('.reader-content > .workbench-pane > .pane-body')
            await body.evaluate('e => e.scrollTop = 0')
            box = await body.bounding_box()
            await page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
            await page.mouse.wheel(0, 120)
            await page.wait_for_timeout(200)
            # overflow:hidden 仍允许程序改 scrollTop，所以必须用真实滚轮验证。
            assert await body.evaluate('e => e.scrollTop') > 0, 'Default central document cannot wheel-scroll'
            await page.get_by_role('button', name='搜索', exact=True).click()
            await page.locator('[data-dock-tool=search] input').fill('retained query')
            await page.get_by_role('button', name='目录', exact=True).click()
            await page.get_by_role('button', name='搜索', exact=True).click()
            assert await page.locator('[data-dock-tool=search] input').input_value() == 'retained query'
            await page.evaluate("dock.move('search','right');dock.resize('right',900);dock.open('toc')")
            await page.wait_for_timeout(150)
            assert await page.locator('[data-dock-tool=search] input').input_value() == 'retained query'
            await page.evaluate("dock.open('info')")
            await page.wait_for_timeout(150)
            await page.evaluate('''() => {
 const search=document.querySelector('[data-dock-tool=search] .pane-body');
 const info=document.querySelector('[data-dock-tool=info] .pane-body');
 if(search.clientHeight<=0 || info.clientHeight<=0) throw Error('Split pane collapsed');
 info.scrollTop=83;search.scrollTop=110;
 if(info.scrollTop!==83 || search.scrollTop!==110) throw Error('Split scroll interference');
 const tabs=document.querySelector('.editor-tabs');
 tabs.scrollLeft=200;if(tabs.scrollLeft!==200) throw Error('Long tabs do not scroll');
 if(document.scrollingElement.scrollWidth>innerWidth) throw Error('Tabs stretched shell');
}''')
            assert await page.locator('button').filter(has_text='常驻').count() == 0
            assert not errors, errors
            print(json.dumps({'viewports':results,'panelSwitchAndMove':True,'pageErrors':errors},ensure_ascii=False))
        finally:
            await browser.close()


asyncio.run(main())
