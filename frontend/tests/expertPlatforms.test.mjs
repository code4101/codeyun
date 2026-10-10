import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import {JSDOM} from 'jsdom';
import {KEYS,expertScore,rank,sortSystems,radarValues,drawRadar,init} from '../public/expert-platforms/app.js';
const system=grades=>({name:'sample',assessments:Object.fromEntries(KEYS.map((k,i)=>[k,{grade:grades[i]}]))});
test('unknown radar positions use only known values and never change scores',()=>{
 const s=system([4,-1,2,3,-1,1]),r=radarValues(s);assert.equal(r.mean,2.5);assert.equal(r.points[1].plot,2.5);assert.equal(r.points[1].value,-1);assert.equal(expertScore(s),-1);assert.equal(s.assessments.strategy.grade,-1);
});
test('all unknown does not invent an average',()=>{const r=radarValues(system(Array(6).fill(-1)));assert.equal(r.mean,null);assert.ok(r.points.every(p=>p.plot===null));});
test('rank preserves ties and unknown; score sort puts unknown last',()=>{
 assert.equal(rank(3,[4,4,3,-1,2]),3);assert.equal(rank(-1,[4,3,-1]),-1);
 const a=system([4,4,4,1,1,1]),b=system([4,-1,4,4,4,4]),c=system([2,2,2,4,4,4]);assert.deepEqual(sortSystems([b,c,a]),[a,c,b]);assert.equal(expertScore(a),12);
 assert.deepEqual(sortSystems([b,c,a],'expert','asc'),[c,a,b]);assert.deepEqual(sortSystems([b,c,a],'strategy','asc'),[c,a,b]);
 a.name='Alpha';b.name='Beta';c.name='Gamma';assert.deepEqual(sortSystems([b,c,a],'name','asc'),[a,b,c]);assert.deepEqual(sortSystems([b,c,a],'name','desc'),[c,b,a]);
});
test('radar dashes every edge touching unknown and preserves actual tooltip',()=>{
 const dom=new JSDOM('<div id="chart"></div><ul id="legend"></ul>');globalThis.document=dom.window.document;
 const host=document.querySelector('#chart');drawRadar([system([4,-1,2,3,4,1])],KEYS.map(key=>({key,name:key})),host,document.querySelector('#legend'));
 assert.equal(host.querySelectorAll('line[stroke-dasharray]').length,2);assert.match(host.textContent,/待核实（-1）/);assert.match(host.textContent,/不参与评分/);assert.equal(host.querySelectorAll('circle').length,6);delete globalThis.document;
});
test('complete dataset has six multi-point assessments, numerical unknowns and unique platform names',async()=>{
 const data=JSON.parse(await readFile(new URL('../public/expert-platforms/data.json',import.meta.url),'utf8'));assert.equal(new Set(data.systems.map(s=>s.name)).size,data.systems.length);
 assert.equal(data.systems.length,21);for(const s of data.systems)for(const k of KEYS){const a=s.assessments[k];assert.ok([-1,1,2,3,4].includes(a.grade));assert.ok(a.text.split('\n').length>=3);assert.match(a.source,/^https:\/\//);}
});
test('page shows all platforms, sorts through headers, expands and limits comparison',async()=>{
 const dom=new JSDOM(await readFile(new URL('../public/expert-platforms/index.html',import.meta.url),'utf8'),{url:'http://localhost/expert-platforms/'});
 globalThis.document=dom.window.document;globalThis.location=dom.window.location;
 const data=JSON.parse(await readFile(new URL('../public/expert-platforms/data.json',import.meta.url),'utf8'));const previousFetch=globalThis.fetch;globalThis.fetch=async()=>({ok:true,json:async()=>data});
 try{await init();assert.equal(document.querySelector('#content').hidden,false);assert.equal(document.querySelectorAll('#tbody input').length,21);assert.equal(document.querySelectorAll('.detail-table').length,0);assert.equal(document.querySelectorAll('#tbody > tr').length,21);
 assert.equal(document.querySelector('#search'),null);assert.equal(document.querySelector('#sort'),null);
 const rows=()=>[...document.querySelectorAll('#tbody > tr:not(.detail-row)')];
 const initialRanks=new Map(rows().map(row=>[row.querySelector('.platform-name').textContent,[...row.querySelectorAll('.score-cell')].map(cell=>cell.textContent).join('|')]));
 for(const direction of ['desc','asc']){document.querySelector('[data-sort-key="verification"]').click();const expected=sortSystems(data.systems,'verification',direction).map(s=>s.name);assert.deepEqual(rows().map(row=>row.querySelector('.platform-name').textContent),expected);assert.equal(document.querySelector('[data-sort-key="verification"]').parentElement.getAttribute('aria-sort'),direction==='asc'?'ascending':'descending');}
 for(const direction of ['asc','desc']){document.querySelector('[data-sort-key="name"]').click();assert.deepEqual(rows().map(row=>row.querySelector('.platform-name').textContent),sortSystems(data.systems,'name',direction).map(s=>s.name));}
 for(const row of rows())assert.equal([...row.querySelectorAll('.score-cell')].map(cell=>cell.textContent).join('|'),initialRanks.get(row.querySelector('.platform-name').textContent));
 const bespoke=rows().find(row=>row.querySelector('.platform-name').textContent.includes('Bespoke'));assert.equal(document.querySelector('.rank-caption'),null);assert.equal(document.querySelector('[data-sort-key=expert]'),null);assert.ok([...bespoke.querySelectorAll('.score-cell')].some(cell=>cell.textContent==='—'&&cell.title.includes('待核实')));bespoke.querySelector('.platform-name').click();assert.ok([...document.querySelectorAll('.detail-table td')].some(cell=>cell.textContent==='—'&&cell.title.includes('待核实')));
 const unchecked=[...document.querySelectorAll('#tbody input:not(:checked)')];unchecked[0].click();unchecked[1].click();assert.equal(document.querySelector('#selected-count').textContent,'3');assert.equal(unchecked[1].checked,false);assert.match(document.querySelector('#live').textContent,/最多/);
 document.querySelector('[data-sort-key="compare"]').click();assert.ok(rows().slice(0,3).every(row=>row.querySelector('input').checked));assert.equal(document.querySelectorAll('#tbody input').length,21);assert.equal(document.querySelectorAll('.detail-row').length,1);
 }finally{globalThis.fetch=previousFetch;delete globalThis.document;delete globalThis.location;}
});
test('inline deployment mode uses embedded data and does not fetch a relative data file',async()=>{
 const html=await readFile(new URL('../public/expert-platforms/index.html',import.meta.url),'utf8');
 const dom=new JSDOM(html,{url:'http://localhost/static/expert-platforms.html'});globalThis.document=dom.window.document;globalThis.location=dom.window.location;
 const inline=document.createElement('script');inline.id='platform-data';inline.type='application/json';inline.textContent=await readFile(new URL('../public/expert-platforms/data.json',import.meta.url),'utf8');document.head.append(inline);
 const previousFetch=globalThis.fetch;globalThis.fetch=async()=>{throw Error('unexpected network request');};
 try{await init();assert.equal(document.querySelector('#content').hidden,false);assert.equal(document.querySelectorAll('#tbody input').length,21);assert.match(document.querySelector('#download a').href,/\.xlsx$/);}
 finally{globalThis.fetch=previousFetch;delete globalThis.document;delete globalThis.location;}
});
