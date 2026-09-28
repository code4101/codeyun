/** Real-browser read-only contract test against the shipped iframe bridge.
 * node sharing-smoke.cjs <plugin-public-dir> <upstream> <playwright-module> <temp-output>
 * Own ephemeral HTTP server and isolated browser contexts; no production APIs.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const http = require('node:http');
const { createRequire } = require('node:module');
const [publicDir, upstream, playwrightModule, output] = process.argv.slice(2);
const { chromium } = require(playwrightModule);
const upstreamRequire = createRequire(path.resolve(upstream, 'app/package.json'));
const { ZipReader, Uint8ArrayReader, Uint8ArrayWriter } = upstreamRequire('@zip.js/zip.js');
const { decode } = upstreamRequire('@msgpack/msgpack');
let bytes = null, writes = 0;
const host = `<!doctype html><html><body style="margin:0"><iframe style="width:100vw;height:95vh;border:0" src="/embed.html?session=test"></iframe><output id="state"></output><script>
const frame = document.querySelector('iframe'), viewer = new URLSearchParams(location.search).has('viewer');
let exported;
function send(type,payload={}) {frame.contentWindow.postMessage({channel:'codeyun.project-graph',version:1,session:'test',type,payload},location.origin)}
window.exportGraph=()=>new Promise(resolve=>{exported=resolve;send('export')});
window.changeDetails=(id)=>send('details-change',{id,value:[{type:'p',children:[{text:'forbidden edit'}]}]});
addEventListener('message',async event=>{
 if(event.source!==frame.contentWindow || event.origin!==location.origin)return;
 const m=event.data;
 if(m.type==='ready') {const r=await fetch('/document');const b=await r.arrayBuffer();frame.contentWindow.postMessage({channel:m.channel,version:1,session:'test',type:'response',id:m.id,payload:{title:'ReadOnly.prg',bytes:b.byteLength?new Uint8Array(b):null,readOnly:viewer,detailsActive:true}},location.origin)}
 if(m.type==='write') {await fetch('/document',{method:'PUT',body:m.payload.bytes});frame.contentWindow.postMessage({channel:m.channel,version:1,session:'test',type:'response',id:m.id,payload:{revision:1}},location.origin)}
 if(m.type==='presented') window.presented=true;
 if(m.type==='selection-details') window.selection=m.payload;
 if(m.type==='status') document.querySelector('#state').textContent=m.payload.state;
 if(m.type==='exported') exported(Array.from(m.payload.bytes));
 if(m.type==='error') window.bridgeError=m.payload.message;
});
</script></body></html>`;
const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/') { res.setHeader('Content-Type', 'text/html'); return res.end(host); }
    if (url.pathname === '/document') {
      if (req.method === 'PUT') { const parts=[]; for await (const part of req) parts.push(part); bytes=Buffer.concat(parts); writes++; }
      return res.end(bytes ?? Buffer.alloc(0));
    }
    const filename = path.resolve(publicDir, '.' + decodeURIComponent(url.pathname));
    if (!filename.startsWith(path.resolve(publicDir) + path.sep)) {res.statusCode=403;return res.end();}
    const mime={'.html':'text/html','.js':'application/javascript','.css':'text/css','.wasm':'application/wasm','.svg':'image/svg+xml'};
    res.setHeader('Content-Type',mime[path.extname(filename)]??'application/octet-stream');
    res.end(await fs.readFile(filename));
  } catch {res.statusCode=404;res.end();}
});
async function stage(page) {
  const result = await page.evaluate(()=>window.exportGraph());
  const zip = new ZipReader(new Uint8ArrayReader(Uint8Array.from(result)));
  try { const item=(await zip.getEntries()).find(entry=>entry.filename==='stage.msgpack');return decode(await item.getData(new Uint8ArrayWriter())); }
  finally {await zip.close();}
}
async function main() {
  await fs.mkdir(output,{recursive:true});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const url=`http://127.0.0.1:${server.address().port}`;
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try {
    const errors=[];
    async function open(viewer) {
      const context=await browser.newContext({viewport:{width:1280,height:900}});
      const page=await context.newPage();page.on('pageerror',error=>errors.push(String(error)));
      await page.goto(url+(viewer?'/?viewer=1':'/'));
      await page.waitForFunction(()=>window.presented,{},{timeout:45000});
      return page;
    }
    const owner=await open(false), ownerFrame=owner.frames().find(f=>f.url().includes('embed.html'));
    await ownerFrame.locator('canvas').dblclick({position:{x:450,y:320}});
    await ownerFrame.locator('textarea').fill('分享基线节点');
    await owner.keyboard.press('Escape');
    await owner.waitForFunction(()=>document.querySelector('#state').textContent==='saved');
    const original=await stage(owner);
    assert.ok(JSON.stringify(original).includes('分享基线节点'));
    const viewer=await open(true), frame=viewer.frames().find(f=>f.url().includes('embed.html'));
    const before=await stage(viewer), initialWrites=writes;
    assert.deepEqual(before,original,'viewer loads the original PRG');
    await frame.locator('canvas').click({position:{x:450,y:320}});
    await viewer.waitForFunction(()=>window.selection?.id);
    const id=await viewer.evaluate(()=>window.selection.id);
    await viewer.keyboard.press('Delete');
    await viewer.keyboard.press('Control+z');
    await viewer.evaluate(id=>window.changeDetails(id),id);
    await frame.locator('canvas').dblclick({position:{x:650,y:400}});
    assert.equal(await frame.locator('textarea').count(),0,'viewer cannot create or edit a node');
    const box=await frame.locator('canvas').boundingBox();
    await viewer.mouse.move(box.x+450,box.y+320);await viewer.mouse.down();
    await viewer.mouse.move(box.x+550,box.y+390,{steps:8});await viewer.mouse.up();
    await viewer.waitForTimeout(1400);
    assert.deepEqual(await stage(viewer),before,'readonly actions leave every serialized field unchanged');
    assert.equal(writes,initialWrites,'viewer never autosaves');
    await viewer.screenshot({path:path.join(output,'sharing-viewer.png')});
    assert.deepEqual(errors,[]);
    console.log('PASS: owner creates/saves, viewer selects/exports, create/delete/undo/drag/body edit blocked; PRG unchanged');
  } finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
}
main().catch(error=>{console.error(error);server.close();process.exitCode=1;});
