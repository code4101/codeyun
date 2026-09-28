"""uv run --with playwright python frontend/tests/projectGraphDetails.browser.py

Verify sticky body target through the public editor bridge, using an isolated
in-memory PRG. No user documents are read or changed.
"""
import asyncio
import re
import urllib.request
from playwright.async_api import async_playwright


async def main():
    source = urllib.request.urlopen('http://localhost:5173/src/components/PlateEditor.vue').read().decode()
    vue = re.search(r'"([^" ]*/deps/vue.js[^" ]*)"', source).group(1)
    html = '''<style>html,body,#app{height:100%;margin:0}</style><div id="app"></div><script type="module">
    import {createApp,h,ref} from 'VUE_URL';
    import Editor from '/src/plugins/modules/project-graph/ProjectGraphEditor.vue';
    window.details=null;window.errors=[];window.snapshots=[];
    const docs=new Map();const storage={read:async id=>docs.get(id),write:async(id,title,bytes,revision)=>{
      const doc={id,title,bytes,revision:revision+1,updatedAt:Date.now()};docs.set(id,doc);return doc;}};
    createApp({setup(){const editor=ref(),id=ref('A'),visible=ref(true);
      window.toggle=()=>visible.value=!visible.value;
      window.switchFile=()=>id.value='B';window.flush=()=>editor.value.flush();
      window.run=command=>editor.value.executeMenu(command);
      window.edit=value=>editor.value.updateDetails(window.details.id,[{type:'p',children:[{text:value}]}]);
      return()=>h(Editor,{ref:editor,key:id.value,documentId:id.value,title:id.value,storage,detailsActive:visible.value,
        onDetails:value=>{window.details=value;window.snapshots.push(value)},onError:error=>window.errors.push(error)});
    }}).mount('#app');</script>'''.replace('VUE_URL', vue)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        try:
            page = await browser.new_page(viewport={'width':1200,'height':800})
            errors=[]
            page.on('pageerror', lambda error:errors.append(str(error)))
            await page.route('**/__pg-details-test', lambda r:r.fulfill(content_type='text/html',body=html))
            await page.goto('http://localhost:5173/__pg-details-test')
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            frame=page.frame_locator('iframe')
            canvas=frame.locator('canvas').first
            async def create_node(x,y,name):
                await canvas.dblclick(position={'x':x,'y':y})
                await frame.locator('textarea').fill(name)
                await page.keyboard.press('Escape')
                await canvas.click(position={'x':x+10,'y':y+10})
                await page.wait_for_function('name=>window.details?.title===name',arg=name)
                return await page.evaluate('window.details.id')
            first=await create_node(300,250,'First')
            await canvas.click(position={'x':900,'y':650})
            # Wait across multiple 50 ms selection ticks, not just the click frame.
            await page.wait_for_timeout(200)
            assert await page.evaluate('window.details.id')==first
            await page.evaluate("window.edit('Body after background click')")
            await page.evaluate('window.flush()')
            second=await create_node(650,250,'Second')
            assert first!=second
            await canvas.click(position={'x':310,'y':260})
            await page.wait_for_function('id=>window.details?.id===id',arg=first)
            assert 'Body after background click' in await page.evaluate('JSON.stringify(window.details.value)')
            await canvas.click(position={'x':900,'y':650})
            await page.evaluate('window.toggle()')
            await page.evaluate('window.toggle()')
            await page.wait_for_timeout(200)
            assert await page.evaluate('window.details.id')==first
            await canvas.click(position={'x':660,'y':260})
            await page.wait_for_function('id=>window.details?.id===id',arg=second)
            await page.keyboard.press('Delete')
            await page.wait_for_function('window.details===null')
            await canvas.click(position={'x':310,'y':260})
            await page.wait_for_function('id=>window.details?.id===id',arg=first)
            await page.evaluate('window.flush()')
            await page.evaluate('window.switchFile()')
            await page.locator('iframe:not(.pending)').wait_for(timeout=60000)
            await page.wait_for_function('window.details===null')
            assert not errors,errors
            assert await page.evaluate('window.errors')==[],await page.evaluate('window.errors')
            print('PASS: background retains body; edits keep target; next object replaces it; deletion and file switch clear it')
        finally:
            await browser.close()


asyncio.run(main())
