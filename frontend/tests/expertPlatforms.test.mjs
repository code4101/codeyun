import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import {JSDOM} from 'jsdom';
import {KEYS,usdValuation,assessmentScore,expertScore,averageScore,rank,sortSystems,radarValues,drawRadar,init} from '../public/expert-platforms/app.js';
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
 assert.equal(data.systems.length,21);for(const s of data.systems)for(const k of KEYS){const a=s.assessments[k];assert.ok([-1,1,2,3,4].includes(a.grade));assert.ok(a.score===-1||(a.score>=1&&a.score<=5&&Math.abs(a.score*10-Math.round(a.score*10))<1e-9));assert.equal(a.score===-1,a.grade===-1);if(a.score!==-1)assert.ok(a.score_basis.length>8);assert.ok(a.text.split('\n').length>=3);assert.match(a.source,/^https:\/\//);assert.ok(Array.isArray(a.strengths));if(a.grade===-1)assert.deepEqual(a.strengths,[]);for(const point of a.strengths){assert.ok(point.length>8);assert.doesNotMatch(point,/待核实|未公开|需补|边界：|缺少|不足以/);}}
});
test('page shows all platforms, sorts through headers, expands and limits comparison',async()=>{
 const dom=new JSDOM(await readFile(new URL('../public/expert-platforms/index.html',import.meta.url),'utf8'),{url:'http://localhost/expert-platforms/'});
 globalThis.document=dom.window.document;globalThis.location=dom.window.location;
 const data=JSON.parse(await readFile(new URL('../public/expert-platforms/data.json',import.meta.url),'utf8'));const previousFetch=globalThis.fetch;globalThis.fetch=async()=>({ok:true,json:async()=>data});
 try{await init();assert.equal(document.querySelector('#content').hidden,false);assert.equal(document.querySelectorAll('#tbody input').length,21);assert.equal(document.querySelectorAll('.detail-table').length,0);assert.equal(document.querySelectorAll('#tbody > tr').length,21);
 assert.equal(document.querySelector('#search'),null);assert.equal(document.querySelector('#sort'),null);assert.equal(document.querySelectorAll('#thead th').length,8);assert.ok([...document.querySelectorAll('#thead th')].every(th=>!th.textContent.includes('特色')));for(const row of [...document.querySelectorAll('#tbody > tr')]){const platform=data.systems.find(s=>s.name===row.querySelector('.platform-name').textContent);[...row.querySelectorAll('.score-cell')].forEach((cell,i)=>{assert.deepEqual([...cell.querySelectorAll('.dimension-strengths li')].map(li=>li.textContent),platform.assessments[KEYS[i]].strengths);assert.equal(cell.querySelectorAll('.source-link').length,platform.assessments[KEYS[i]].strengths.length?1:0);const score=assessmentScore(platform.assessments[KEYS[i]]);assert.equal(cell.querySelector('strong').textContent,score===-1?'—':score.toFixed(1));if(score!==-1)assert.equal(cell.querySelector('.rating-line span').textContent,'#'+rank(score,data.systems.map(s=>assessmentScore(s.assessments[KEYS[i]]))));});}
 const rows=()=>[...document.querySelectorAll('#tbody > tr:not(.detail-row)')];
 assert.equal(document.querySelectorAll('.company-brief').length,22);for(const row of rows()){assert.ok(row.querySelector('.company-brief'));for(const value of row.querySelectorAll('.company-value')){assert.ok(value.title.length>0);if(value.tagName==='A')assert.match(value.href,/^https:\/\//);}}const scaleRow=rows().find(row=>row.querySelector('.platform-name').textContent==='Scale AI');assert.match(scaleRow.querySelector('.company-brief').textContent,/Meta持股49%/);assert.match(scaleRow.querySelector('.company-brief').textContent,/超过290亿美元/);assert.match(scaleRow.querySelector('.company-brief').textContent,/1,300\+人/);assert.ok([...scaleRow.querySelectorAll('.company-value')].some(value=>value.title.includes('2025-06-12')));
 assert.deepEqual(rows().map(row=>row.querySelector('.platform-name').textContent),sortSystems(data.systems,'average').map(s=>s.name));assert.deepEqual(rows().map(row=>Number(row.querySelector('.platform-number').textContent)),Array.from({length:21},(_,i)=>i+1));
 for(const row of rows()){const platform=data.systems.find(s=>s.name===row.querySelector('.platform-name').textContent),average=row.querySelector('.platform-average');assert.equal(average.querySelector('strong').textContent,averageScore(platform).toFixed(2));assert.match(average.title,/已核实\d\/6/);assert.match(average.title,/主观暂评/);}
 const initialRanks=new Map(rows().map(row=>[row.querySelector('.platform-name').textContent,[...row.querySelectorAll('.score-cell')].map(cell=>cell.textContent).join('|')]));
 for(const direction of ['desc','asc']){document.querySelector('[data-sort-key="verification"]').click();const expected=sortSystems(sortSystems(data.systems,'average'),'verification',direction).map(s=>s.name);assert.deepEqual(rows().map(row=>row.querySelector('.platform-name').textContent),expected);assert.equal(document.querySelector('[data-sort-key="verification"]').parentElement.getAttribute('aria-sort'),direction==='asc'?'ascending':'descending');}
 for(const direction of ['asc','desc']){document.querySelector('[data-sort-key="name"]').click();assert.deepEqual(rows().map(row=>row.querySelector('.platform-name').textContent),sortSystems(data.systems,'name',direction).map(s=>s.name));}
 for(const row of rows())assert.equal([...row.querySelectorAll('.score-cell')].map(cell=>cell.textContent).join('|'),initialRanks.get(row.querySelector('.platform-name').textContent));
 const bespoke=rows().find(row=>row.querySelector('.platform-name').textContent.includes('Bespoke'));assert.equal(document.querySelector('.rank-caption'),null);assert.equal(document.querySelector('[data-sort-key=expert]'),null);assert.ok([...bespoke.querySelectorAll('.score-cell')].some(cell=>cell.textContent==='—'&&cell.querySelector('.rating-line').title.includes('待核实')));bespoke.querySelector('.platform-name').click();assert.ok(document.querySelector('.detail-row').textContent.includes('公司背景'));
 const unchecked=[...document.querySelectorAll('#tbody input:not(:checked)')];unchecked[0].click();unchecked[1].click();assert.equal(document.querySelector('#selected-count').textContent,'3');assert.equal(unchecked[1].checked,false);assert.match(document.querySelector('#live').textContent,/最多/);
 assert.equal(document.querySelector('[data-sort-key="compare"]'),null);assert.equal(document.querySelector('[data-sort-key="number"]'),null);
 for(const direction of ['desc','asc']){document.querySelector('[data-sort-key="average"]').click();assert.deepEqual(rows().map(row=>row.querySelector('.platform-name').textContent),sortSystems(sortSystems(data.systems,'average'),'average',direction).map(s=>s.name));}
 const beforeSelection=rows().map(row=>row.querySelector('.platform-name').textContent);rows().find(row=>row.querySelector('input').checked).querySelector('input').click();assert.deepEqual(rows().map(row=>row.querySelector('.platform-name').textContent),beforeSelection);
 for(const row of rows()){const cell=row.querySelector('td');assert.ok(cell.classList.contains('platform-overview'));assert.ok(cell.querySelector('.platform-number'));assert.ok(cell.querySelector('input'));assert.ok(cell.querySelector('.platform-average'));assert.equal(row.querySelectorAll('.platform-average').length,1);assert.equal(row.querySelectorAll('td').length,8);}
 assert.equal(document.querySelectorAll('#tbody input').length,21);assert.equal(document.querySelectorAll('.detail-row').length,1);
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

test('default average uses all six known dimensions and puts wholly unknown last',()=>{
 const a=system([4,4,4,1,1,1]),b=system([3,3,3,4,4,4]),c=system([4,-1,-1,-1,-1,-1]),d=system(Array(6).fill(-1));
 assert.equal(averageScore(a),2.5);assert.equal(averageScore(b),3.5);assert.equal(averageScore(c),4);assert.equal(averageScore(d),-1);assert.deepEqual(sortSystems([a,d,b,c],'average'),[c,b,a,d]);assert.deepEqual(sortSystems([a,d,b,c],'average','asc'),[a,b,c,d]);
});

test('fine scores drive every calculation while original grades remain intact',()=>{
 const a=system([4,4,4,4,4,4]),b=system([4,4,4,4,4,4]);
 KEYS.forEach(key=>{a.assessments[key].score=3.7;b.assessments[key].score=3.9;});
 assert.equal(averageScore(a),3.7);assert.equal(expertScore(a),11.1);assert.deepEqual(sortSystems([a,b],'problem'),[b,a]);assert.equal(rank(3.7,[3.7,3.9]),2);assert.equal(radarValues(a).points[0].plot,3.7);assert.equal(a.assessments.problem.grade,4);
 a.assessments.strategy.score=-1;assert.equal(expertScore(a),-1);assert.equal(averageScore(a),3.7);assert.equal(radarValues(a).points[1].plot,3.7);
});

test('currency conversion uses original full amounts and same-day cross rates',async()=>{
 const data=JSON.parse(await readFile(new URL('../public/expert-platforms/data.json',import.meta.url),'utf8'));
 const appen=data.background.find(c=>c.name==='Appen / CrowdGen'),rws=data.background.find(c=>c.name==='RWS / TrainAI');
 const a=usdValuation(appen,data.exchange_rates),r=usdValuation(rws,data.exchange_rates);
 assert.ok(Math.abs(a.amount-303430000*0.9958/1.4271)<0.000001);assert.match(a.text,/2.12亿美元/);assert.match(r.text,/7.12亿美元/);assert.match(a.detail,/303,430,000 AUD/);assert.match(a.detail,/2026-10-09/);assert.equal(usdValuation({valuation:'—'},data.exchange_rates),null);
});
