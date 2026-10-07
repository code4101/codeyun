/** Real native editor/PRG acceptance; all files and users belong to a disposable fixture.
 * node node-times-smoke.cjs <server-manifest> <checkout> <playwright-module> <output>
 */
const assert = require('node:assert/strict'), fs = require('node:fs/promises'), path = require('node:path');
const {createRequire} = require('node:module');
const [manifest, checkout, playwrightModule, output] = process.argv.slice(2);
const upstream = createRequire(path.resolve(checkout, 'app/package.json'));
const {ZipReader, ZipWriter, Uint8ArrayReader, Uint8ArrayWriter} = upstream('@zip.js/zip.js');
const {decode, encode} = upstream('@msgpack/msgpack');
const {chromium} = require(playwrightModule);
async function main() {
  await fs.mkdir(output, {recursive:true});
  const {url} = JSON.parse(await fs.readFile(manifest, 'utf8'));
  const tokens = await fetch(url+'/fixture/sessions').then(r=>r.json());
  const api = async (suffix='', method='GET', body, user=1) => {
    const r = await fetch(url+'/api/project-graph/files'+suffix, {method,headers:{Authorization:'Bearer '+tokens[user], 'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
    const result=await r.json(); assert.ok(r.ok, JSON.stringify(result)); return result;
  };
  const browser = await chromium.launch({channel:'chrome',headless:true}), pages=[], errors=[];
  const frame = page => page.frames().find(f=>f.url().includes('embed.html'));
  async function open(id, user=1) {
    const context=await browser.newContext({permissions:['clipboard-read','clipboard-write'],viewport:{width:1280,height:900}});
    const page=await context.newPage(); pages.push(page); page.on('pageerror',e=>errors.push(String(e)));
    await page.goto(`${url}/?doc=${id}&user=${user}`); await page.waitForFunction(()=>window.presented,{},{timeout:60000});
    return page;
  }
  async function bytes(page) { await page.evaluate(()=>window.flushGraph()); return Uint8Array.from(await page.evaluate(()=>window.exportGraph())); }
  async function stage(page) {
    const reader=new ZipReader(new Uint8ArrayReader(await bytes(page)));
    try {return decode(await (await reader.getEntries()).find(e=>e.filename==='stage.msgpack').getData(new Uint8ArrayWriter()));}
    finally {await reader.close();}
  }
  async function waitFor(check, label) {
    const until=Date.now()+15000; while(Date.now()<until){if(await check())return;await new Promise(r=>setTimeout(r,80));}throw Error(label);
  }
  try {
    const run=Date.now();
    const rid=(await api('','POST',{title:'Node timestamp acceptance '+run})).id, page=await open(rid);
    const start=Date.now();
    await frame(page).locator('canvas').dblclick({position:{x:400,y:320}});
    await frame(page).locator('textarea').fill('Timestamp node'); await page.keyboard.press('Escape');
    const first=(await stage(page)).find(n=>n.text==='Timestamp node');
    assert.ok(first.createdAt>=start && first.createdAt<=Date.now(), JSON.stringify(first));
    assert.ok(first.updatedAt>=first.createdAt);
    const untouched=await stage(page); await page.waitForTimeout(1100);
    assert.deepEqual(await stage(page),untouched,'idle and repeated saves do not change times');
    await page.evaluate(id=>window.changeDetails(id,'Body edit'),first.uuid);
    await waitFor(async()=>JSON.stringify(await stage(page)).includes('Body edit'),'body edit saved');
    const edited=(await stage(page)).find(n=>n.uuid===first.uuid);
    assert.equal(edited.createdAt,first.createdAt);assert.ok(edited.updatedAt>first.updatedAt);
    await waitFor(async()=>await page.evaluate(t=>window.selection?.updatedAt===t,edited.updatedAt),'selection bridge carries latest time');
    await page.evaluate(()=>window.command('undo'));
    const undone=(await stage(page)).find(n=>n.uuid===first.uuid);
    assert.ok(!JSON.stringify(undone.details).includes('Body edit'));assert.equal(undone.createdAt,first.createdAt);assert.ok(undone.updatedAt>edited.updatedAt);
    await page.evaluate(()=>window.command('redo'));
    const redone=(await stage(page)).find(n=>n.uuid===first.uuid);
    assert.ok(JSON.stringify(redone.details).includes('Body edit'));assert.ok(redone.updatedAt>undone.updatedAt);
    await page.reload();await page.waitForFunction(()=>window.presented,{},{timeout:60000});
    assert.deepEqual((await stage(page)).find(n=>n.uuid===first.uuid),redone,'reload preserves native serialized dates');
    await frame(page).locator('canvas').click({position:{x:400,y:320}});
    await page.evaluate(()=>window.command('copy'));
    await page.waitForFunction(()=>window.clipboardPacket?.objects?.['@order'].value.length===1);
    await page.evaluate(()=>window.command('pasteWithOriginLocation'));
    await waitFor(async()=>(await stage(page)).length===2,'structured paste creates another node');
    const copied=(await stage(page)).find(n=>n.uuid!==first.uuid);
    assert.ok(copied.createdAt>first.createdAt,'copy has its own creation time');
    assert.ok(copied.updatedAt>=copied.createdAt);
    assert.deepEqual((await stage(page)).find(n=>n.uuid===first.uuid),redone,'copy does not edit source');
    // Legacy archive with exactly the same native geometry and attachments.
    const native=await bytes(page), reader=new ZipReader(new Uint8ArrayReader(native)), writer=new ZipWriter(new Uint8ArrayWriter(),{level:0});
    for(const entry of await reader.getEntries())if(!entry.directory){let data=await entry.getData(new Uint8ArrayWriter());if(entry.filename==='stage.msgpack'){const nodes=decode(data);for(const n of nodes){delete n.createdAt;delete n.updatedAt;}data=encode(nodes);}await writer.add(entry.filename,new Uint8ArrayReader(data),{level:0});}
    const legacyBytes=await writer.close();await reader.close();
    const legacyId=(await api('','POST',{title:'Legacy timestamp acceptance '+run,content:Buffer.from(legacyBytes).toString('base64')})).id;
    const legacy=await open(legacyId), old=(await stage(legacy)).find(n=>n.uuid===first.uuid);
    assert.equal(old.createdAt,undefined);assert.equal(old.updatedAt,undefined);
    await legacy.evaluate(id=>window.changeDetails(id,'Legacy edited'),first.uuid);
    await waitFor(async()=>JSON.stringify(await stage(legacy)).includes('Legacy edited'),'legacy edit saved');
    const oldEdited=(await stage(legacy)).find(n=>n.uuid===first.uuid);
    assert.equal(oldEdited.createdAt,null);assert.ok(oldEdited.updatedAt>redone.updatedAt);
    await legacy.close();await page.close();
    const current=await api('/'+rid);await api(`/${rid}/access`,'PUT',{userId:2,role:'editor'});await api(`/${rid}/access`,'PUT',{userId:3,role:'viewer'});
    await api(`/${rid}/collaboration`,'POST',{expectedRevision:current.revision});
    const one=await open(rid),two=await open(rid,2),viewer=await open(rid,3);
    await waitFor(async()=>await frame(one).locator('.collaborator').count()===3,'three peers connected');
    const before=(await stage(one)).find(n=>n.uuid===first.uuid);
    await one.evaluate(id=>window.changeDetails(id,'Collaborative dated edit'),first.uuid);
    await waitFor(async()=>JSON.stringify((await api(`/${rid}/collaboration`)).objects[first.uuid]).includes('Collaborative dated edit'),'collaboration commit');
    const committed=(await api(`/${rid}/collaboration`)).objects[first.uuid];
    assert.equal(committed.createdAt,first.createdAt);assert.ok(committed.updatedAt>before.updatedAt);
    await waitFor(async()=>JSON.stringify(await stage(two))===JSON.stringify(await stage(one)),'editors converge including dates');
    await waitFor(async()=>JSON.stringify(await stage(viewer))===JSON.stringify(await stage(one)),'viewer converges including dates');
    await one.evaluate(id=>window.changeDetails(id,'Second dated edit'),first.uuid);
    await waitFor(async()=>JSON.stringify((await api(`/${rid}/collaboration`)).objects[first.uuid]).includes('Second dated edit'),'second collaboration commit');
    await one.evaluate(()=>window.command('undo'));
    await waitFor(async()=>JSON.stringify((await api(`/${rid}/collaboration`)).objects[first.uuid]).includes('Collaborative dated edit'),'first of two undos');
    await one.evaluate(()=>window.command('undo'));
    await waitFor(async()=>!JSON.stringify((await api(`/${rid}/collaboration`)).objects[first.uuid]).includes('Collaborative dated edit'),'collaborative undo');
    const undoTime=(await api(`/${rid}/collaboration`)).objects[first.uuid].updatedAt;assert.ok(undoTime>committed.updatedAt);
    await one.evaluate(()=>window.command('redo'));
    await waitFor(async()=>JSON.stringify((await api(`/${rid}/collaboration`)).objects[first.uuid]).includes('Collaborative dated edit'),'collaborative redo');
    assert.ok((await api(`/${rid}/collaboration`)).objects[first.uuid].updatedAt>undoTime);
    await one.evaluate(()=>window.command('redo'));
    await waitFor(async()=>JSON.stringify((await api(`/${rid}/collaboration`)).objects[first.uuid]).includes('Second dated edit'),'second consecutive redo');
    assert.deepEqual(errors,[]);
    for(const p of pages.filter(p=>!p.isClosed()))assert.deepEqual(await p.evaluate(()=>window.bridgeErrors??[]),[]);
    await one.screenshot({path:path.join(output,'dated-node.png')});
    await fs.writeFile(path.join(output,'result.json'),JSON.stringify({passed:true,resourceId:rid,checks:['new node timestamps','body edits','idle saves','native undo/redo','reload','new copy identity/times','legacy unknown dates','selection time snapshot','three-account synchronization','collaborative undo/redo']},null,2));
    console.log('PASS: native node timestamps, legacy files, undo/redo and three-account convergence');
  } catch(error) {
    for(let i=0;i<pages.length;i++)if(!pages[i].isClosed()){await pages[i].screenshot({path:path.join(output,`failure-${i}.png`)});console.error('BRIDGE',i,await pages[i].evaluate(()=>window.bridgeErrors??[]));}
    throw error;
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
