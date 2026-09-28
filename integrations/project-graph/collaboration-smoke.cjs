/** End-to-end: real JWTs, real sockets and three isolated Chrome contexts.
 * node collaboration-smoke.cjs <server-manifest> <upstream> <playwright-module> <output>
 */
const assert=require('node:assert/strict'), fs=require('node:fs/promises'), path=require('node:path');
const {createRequire}=require('node:module');
const [manifest,upstream,playwrightModule,output]=process.argv.slice(2);
const {chromium}=require(playwrightModule), upstreamRequire=createRequire(path.resolve(upstream,'app/package.json'));
const {ZipReader,Uint8ArrayReader,Uint8ArrayWriter}=upstreamRequire('@zip.js/zip.js'), {decode}=upstreamRequire('@msgpack/msgpack');
async function main(){
 const {url}=JSON.parse(await fs.readFile(manifest,'utf8')), tokens=await fetch(url+'/fixture/sessions').then(r=>r.json());
 await fs.mkdir(output,{recursive:true});
 async function api(suffix='',method='GET',body,user=1){const response=await fetch(url+'/api/project-graph/files'+suffix,{method,headers:{Authorization:'Bearer '+tokens[user],'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const value=await response.json();assert.ok(response.ok,JSON.stringify(value));return value;}
 const browser=await chromium.launch({channel:'chrome',headless:true}), pages=[], errors=[];
 let rid;
 const frame=page=>page.frames().find(f=>f.url().includes('embed.html'));
 async function open(user,collaborative,suffix=''){const context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage();pages.push(page);page.on('pageerror',error=>errors.push(String(error)));await page.goto(`${url}/?user=${user}&doc=${rid}${suffix}`);await page.waitForFunction(()=>window.presented,{},{timeout:45000});if(collaborative)await frame(page).getByRole('status').filter({hasText:'协作'}).waitFor();return page;}
 async function exported(page){const bytes=await page.evaluate(()=>window.exportGraph()),zip=new ZipReader(new Uint8ArrayReader(Uint8Array.from(bytes)));try {const entry=(await zip.getEntries()).find(e=>e.filename==='stage.msgpack');return decode(await entry.getData(new Uint8ArrayWriter()));}finally{await zip.close();}}
 async function select(page,id){for(const [x,y] of [[400,320],[640,430],[800,520],[450,320],[350,350],[500,430],[850,430],[350,500],[900,600]]){await frame(page).locator('canvas').click({position:{x,y}});await page.waitForTimeout(120);if(await page.evaluate(id=>window.selection?.id===id,id))return;}await page.screenshot({path:path.join(output,'selection-failure.png')});throw Error('Cannot select node '+id);}
 async function waitFor(check,message){const deadline=Date.now()+15000;while(Date.now()<deadline){if(await check())return;await new Promise(r=>setTimeout(r,100));}throw Error(message);}
 try {
  rid=(await api('','POST',{title:'协作浏览器验收-'+Date.now()})).id;
  const setup=await open(1,false);
  for(const [x,y,text] of [[400,320,'节点甲'],[800,520,'节点乙']]){await frame(setup).locator('canvas').dblclick({position:{x,y}});await frame(setup).locator('textarea').fill(text);await setup.keyboard.press('Escape');await setup.waitForTimeout(150);}
  const canvas=await frame(setup).locator('canvas').boundingBox();
  await setup.mouse.move(canvas.x+400,canvas.y+320);await setup.mouse.down({button:'right'});
  await setup.mouse.move(canvas.x+800,canvas.y+520,{steps:12});await setup.mouse.up({button:'right'});await setup.keyboard.press('Escape');
  await setup.evaluate(()=>window.flushGraph());
  const initial=await exported(setup), a=initial.find(n=>n.text==='节点甲').uuid,b=initial.find(n=>n.text==='节点乙').uuid;
  assert.ok(initial.some(item=>item._==='LineEdge'),'real connection exists before enabling collaboration');
  await setup.close();
  await api(`/${rid}/access`,'PUT',{userId:2,role:'editor'});await api(`/${rid}/access`,'PUT',{userId:3,role:'viewer'});
  const current=await api(`/${rid}`);await api(`/${rid}/collaboration`,'POST',{expectedRevision:current.revision});
  const one=await open(1,true),two=await open(2,true),viewer=await open(3,true);
  await waitFor(async()=>await frame(one).locator('.collaborator').count()===3,'all three accounts appear online');
  await select(one,a);
  await one.evaluate(({id,text})=>window.changeDetails(id,text),{id:a,text:'甲由用户一编辑'});
  await waitFor(async()=>JSON.stringify((await api(`/${rid}/collaboration`)).objects[a].details).includes('甲由用户一编辑'),'owner edit persisted');
  await frame(two).getByText('测试用户1 正在编辑',{exact:true}).waitFor();
  await two.evaluate(({id,text})=>window.changeDetails(id,text),{id:a,text:'不得覆盖'});
  await two.waitForTimeout(400);
  assert.ok(!JSON.stringify((await api(`/${rid}/collaboration`)).objects[a]).includes('不得覆盖'));
  await Promise.all([
    one.evaluate(({id,text})=>window.changeDetails(id,text),{id:a,text:'甲并发更新'}),
    two.evaluate(({id,text})=>window.changeDetails(id,text),{id:b,text:'乙并发更新'})]);
  await waitFor(async()=>{const state=(await api(`/${rid}/collaboration`)).objects;return JSON.stringify(state[a]).includes('甲并发更新')&&JSON.stringify(state[b]).includes('乙并发更新')},'disjoint edits both persist');
  await waitFor(async()=>JSON.stringify(await exported(viewer)).includes('乙并发更新'),'viewer receives remote edits');
  assert.deepEqual(await exported(one),await exported(two),'two editors converge');
  assert.deepEqual(await exported(two),await exported(viewer),'viewer converges');
  await one.evaluate(()=>window.command('undo'));
  await waitFor(async()=>JSON.stringify((await api(`/${rid}/collaboration`)).objects[a]).includes('甲由用户一编辑'),'own undo restores own previous value');
  assert.ok(JSON.stringify((await api(`/${rid}/collaboration`)).objects[b]).includes('乙并发更新'),'undo preserves another user edit');
  await one.evaluate(()=>window.command('redo'));
  await waitFor(async()=>JSON.stringify((await api(`/${rid}/collaboration`)).objects[a]).includes('甲并发更新'),'own redo');
  await Promise.all([[one,650,690,'同时新增一'],[two,650,180,'同时新增二']].map(async([page,x,y,text])=>{
    await frame(page).locator('canvas').dblclick({position:{x,y}});
    await frame(page).locator('textarea').fill(text);await page.keyboard.press('Escape');
  }));
  await waitFor(async()=>{const objects=(await api(`/${rid}/collaboration`)).objects;return Object.values(objects).some(o=>o.text==='同时新增一')&&Object.values(objects).some(o=>o.text==='同时新增二')},'simultaneous node insertion');
  await waitFor(async()=>JSON.stringify(await exported(one))===JSON.stringify(await exported(two)),'insertion order converges');
  await one.mouse.move(700,600);
  await frame(two).locator('.collaboration-cursor').filter({hasText:'测试用户1'}).waitFor();
  // Disconnect the lock owner and verify another user can edit that object.
  await one.close();
  await waitFor(async()=>await frame(two).locator('.collaborator').count()===2,'disconnected peer removed');
  await two.evaluate(({id,text})=>window.changeDetails(id,text),{id:a,text:'断线后接续编辑'});
  await waitFor(async()=>JSON.stringify((await api(`/${rid}/collaboration`)).objects[a]).includes('断线后接续编辑'),'disconnect releases locks');
  await two.reload();await two.waitForFunction(()=>window.presented,{},{timeout:45000});
  await waitFor(async()=>JSON.stringify(await exported(two)).includes('断线后接续编辑'),'reload restores committed data');
  const beforeFault=await api(`/${rid}/collaboration`);
  const fault=await open(1,true,'&dropCommitAck=1');
  await fault.evaluate(({id,text})=>window.changeDetails(id,text),{id:a,text:'确认丢失后恢复'});
  await waitFor(async()=>JSON.stringify((await api(`/${rid}/collaboration`)).objects[a]).includes('确认丢失后恢复'),'fault injection still commits durably');
  await waitFor(async()=>await frame(fault).locator('.collaborator').count()===0||await fault.evaluate(()=>Object.keys(sessionStorage).some(key=>key.endsWith(':draft'))),'lost acknowledgment retains a draft');
  await waitFor(async()=>await fault.evaluate(()=>!Object.keys(sessionStorage).some(key=>key.endsWith(':draft'))),'receipt resolves draft after reconnect');
  const afterFault=await api(`/${rid}/collaboration`);
  assert.equal(afterFault.revision,beforeFault.revision+1,'ACK loss must not duplicate commit');
  assert.ok(JSON.stringify(await exported(fault)).includes('确认丢失后恢复'));
  await fault.close();
  await two.context().setOffline(true);
  await frame(two).getByRole('status').filter({hasText:'连接已断开'}).waitFor();
  await two.context().setOffline(false);
  await waitFor(async()=>await frame(two).getByRole('status').filter({hasText:'协作已连接'}).count()>0,'offline reconnect');
  await api(`/${rid}/access`,'PUT',{userId:2,role:'deny'});
  await waitFor(async()=>await frame(viewer).locator('.collaborator').count()===1,'revocation disconnects existing editor');
  await viewer.screenshot({path:path.join(output,'collaboration-viewer.png')});
  assert.deepEqual(errors,[],'no browser runtime errors');
  await fs.writeFile(path.join(output,'result.json'),JSON.stringify({passed:true,resourceId:rid,checks:['real JWT roles','presence names','exclusive object lease','disjoint concurrent edits','simultaneous node insertion','three-way convergence','cursor','disconnect release','reload persistence','local undo/redo preserves remote edit','lost ACK recovery without duplication','offline reconnect','live permission revocation']},null,2));
  console.log('PASS: three-account collaboration, object locks, concurrent disjoint edits, presence/cursors, disconnect and reload');
 }catch(error){for(let i=0;i<pages.length;i++)if(!pages[i].isClosed()){await pages[i].screenshot({path:path.join(output,`failure-${i}.png`)});console.error('PAGE',i,await pages[i].evaluate(()=>({errors:window.bridgeErrors,selection:window.selection})));}throw error;}
 finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
