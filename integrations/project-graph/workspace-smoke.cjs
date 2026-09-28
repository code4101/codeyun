/** Actual Vue workspace + real isolated API. No production app or credentials.
 * node workspace-smoke.cjs <server-manifest> <playwright-module> <temp-output>
 */
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const {createRequire}=require('node:module'),{pathToFileURL}=require('node:url');
const [manifest,playwrightModule,output]=process.argv.slice(2);
const root=path.resolve(__dirname,'../../frontend'),frontendRequire=createRequire(path.join(root,'package.json'));
async function main(){
 console.log('Starting isolated Vue workspace acceptance');
 const {url}=JSON.parse(await fs.readFile(manifest,'utf8')),tokens=await fetch(url+'/fixture/sessions').then(r=>r.json());
 await fs.mkdir(output,{recursive:true});
 const {createServer,build}=await import(pathToFileURL(frontendRequire.resolve('vite')).href);
 const vue=(await import(pathToFileURL(frontendRequire.resolve('@vitejs/plugin-vue')).href)).default;

 const source=`import {createApp,h} from 'vue';import {createPinia} from 'pinia';import {createRouter,createWebHistory,RouterView} from 'vue-router';
 import ElementPlus from 'element-plus';import 'element-plus/dist/index.css';import '/src/style.css';import '/src/standard/pdf/library/readerTheme.css';
 import GraphWorkspace from '/src/plugins/modules/project-graph/GraphWorkspace.vue';import {useUserStore} from '/src/store/userStore.ts';
 import {contextMenuDirective} from '/src/directives/contextMenu.ts';import '/src/directives/contextMenu.css';
 const user=Number(new URLSearchParams(location.search).get('user')||1),tokens=await fetch('/fixture/sessions').then(r=>r.json());
 const app=createApp({render:()=>h(RouterView)}),pinia=createPinia();app.use(pinia);const store=useUserStore();store.setTokens(tokens[user],null);store.user={id:user,username:'test'+user,nickname:'测试用户'+user,is_superuser:false};
 app.use(ElementPlus);app.directive('context-menu',contextMenuDirective);const router=createRouter({history:createWebHistory(),routes:[{path:'/fixture-workspace',component:GraphWorkspace}]});app.use(router);await router.isReady();app.mount('#app');`;
 await build({configFile:false,define:{'process.env.NODE_ENV':JSON.stringify('production')},root,publicDir:path.join(root,'public'),logLevel:'error',plugins:[vue(),{name:'pg-bundle',resolveId(id){if(id.endsWith('virtual:pg-acceptance'))return '\0pg-acceptance'},load(id){if(id==='\0pg-acceptance')return source}}],resolve:{alias:{'@':path.join(root,'src')}},build:{copyPublicDir:false,target:'esnext',outDir:path.join(output,'bundle'),emptyOutDir:false,lib:{entry:'virtual:pg-acceptance',formats:['es'],fileName:'workspace'}}});console.log('Workspace bundle built');
 const vite=await createServer({configFile:false,root,publicDir:path.join(root,'public'),cacheDir:path.join(output,'vite-cache'),logLevel:'error',
  plugins:[vue(),{name:'pg-acceptance-host',resolveId(id){if(id.endsWith('virtual:pg-acceptance'))return '\0pg-acceptance'},load(id){if(id==='\0pg-acceptance')return source},configureServer(server){server.middlewares.use(async(req,res,next)=>{if(req.url==='/workspace.js'||req.url==='/workspace.css'){res.setHeader('Content-Type',req.url.endsWith('.css')?'text/css':'text/javascript');res.end(await fs.readFile(path.join(output,'bundle',req.url.slice(1))));return;}if(req.url?.startsWith('/fixture-workspace')){res.setHeader('Content-Type','text/html');res.end('<!doctype html><html><head><meta charset="utf-8"><style>html,body,#app{height:100%;margin:0}</style></head><body><div id="app"></div><link rel="stylesheet" href="/workspace.css"><script type="module" src="/workspace.js"></script></body></html>')}else next()})}}],
  optimizeDeps:{entries:[]},resolve:{alias:{'@':path.join(root,'src')}},server:{host:'127.0.0.1',port:19847,strictPort:true,proxy:{'/api':{target:url,ws:true},'/fixture/sessions':url}}});
 await vite.listen();const origin='http://127.0.0.1:'+vite.httpServer.address().port;console.log('Workspace served at '+origin);
 const {chromium}=require(playwrightModule),browser=await chromium.launch({channel:'chrome',headless:true}),pages=[],errors=[];
 const title='宿主分享验收-'+Date.now();
 const response=await fetch(url+'/api/project-graph/files',{method:'POST',headers:{Authorization:'Bearer '+tokens['1'],'Content-Type':'application/json'},body:JSON.stringify({title})});
 assert.ok(response.ok);const rid=(await response.json()).id;
 const frame=p=>p.frames().find(f=>f.url().includes('embed.html'));
 async function open(user){const context=await browser.newContext({viewport:{width:1500,height:1000}}),page=await context.newPage();pages.push(page);page.on('pageerror',e=>{errors.push(String(e));console.error('Browser error:',String(e))});page.on('console',m=>{if(m.type()==='error')console.error('Console:',m.text())});await page.goto(`${origin}/fixture-workspace?user=${user}&doc=${rid}`);await page.getByTitle('ProjectGraph 编辑器').waitFor();await page.frameLocator('iframe[title="ProjectGraph 编辑器"]').locator('canvas').waitFor({timeout:60000});return page;}
 async function menu(page,label){await page.getByRole('treeitem').filter({hasText:title}).click({button:'right'});await page.getByRole('menuitem',{name:label,exact:true}).click();}
 try{
  const owner=await open(1);
  await frame(owner).locator('canvas').dblclick({position:{x:420,y:320}});await frame(owner).locator('textarea').fill('宿主节点');await owner.keyboard.press('Escape');await owner.waitForTimeout(1200);
  await menu(owner,'分享权限…');const dialog=owner.getByRole('dialog');
  await dialog.getByPlaceholder('搜索账号或昵称').fill('test2');
  await owner.getByRole('option').filter({hasText:'test2'}).first().click();
  await dialog.getByRole('button',{name:'添加',exact:true}).click();
  await dialog.locator('.grant').filter({hasText:'测试用户2'}).waitFor();
  const viewer=await open(2);assert.equal(await frame(viewer).locator('html.codeyun-readonly').count(),1,'share defaults to viewer');
  await dialog.locator('.grant .el-select').click();await owner.getByRole('option',{name:'可编辑',exact:true}).click();
  await dialog.getByRole('button',{name:'关闭',exact:true}).click();
  await viewer.reload();await viewer.frameLocator('iframe[title="ProjectGraph 编辑器"]').locator('canvas').waitFor({timeout:60000});
  assert.equal(await frame(viewer).locator('html.codeyun-readonly').count(),0,'editor grant reaches iframe');
  await menu(owner,'启用多人协作');await owner.frameLocator('iframe[title="ProjectGraph 编辑器"]').getByRole('status').filter({hasText:'协作已连接'}).waitFor();
  await viewer.reload();await viewer.frameLocator('iframe[title="ProjectGraph 编辑器"]').getByRole('status').filter({hasText:'协作已连接'}).waitFor();
  await owner.screenshot({path:path.join(output,'workspace-sharing.png')});
  await viewer.close();await owner.waitForTimeout(300);
  const disabledResponse=owner.waitForResponse(r=>r.url().endsWith(`/${rid}/collaboration`)&&r.request().method()==='DELETE');
  await menu(owner,'结束协作并保存为普通文件');
  const disabled=await disabledResponse;assert.equal(disabled.status(),200,await disabled.text());
  await owner.locator('main.graph-workspace[aria-busy="false"]').waitFor();
  await owner.frameLocator('iframe[title="ProjectGraph 编辑器"]').locator('canvas').waitFor({timeout:60000});
  assert.equal(await frame(owner).locator('.codeyun-collaboration').count(),0,'exit collaboration returns to ordinary editor');
  assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(output,'result.json'),JSON.stringify({passed:true,resourceId:rid,checks:['actual GraphWorkspace','share dialog','viewer/editor role','enable collaboration','disable collaboration preserves ordinary file']},null,2));
  console.log('PASS: real Vue workspace share dialog, viewer/editor roles, collaboration enable/disable and iframe bridge');
 }catch(error){for(let i=0;i<pages.length;i++)if(!pages[i].isClosed()){await pages[i].screenshot({path:path.join(output,`failure-${i}.png`)});console.error('PAGE',i,await pages[i].locator('body').innerText());}throw error;}
 finally{await browser.close();await vite.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1});
