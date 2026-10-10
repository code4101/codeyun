/** 专家机制分数与背景数据分别维护；未知值始终保留-1，绘图估计不回写。 */
export const KEYS = ['problem','strategy','division','collection','verification','quality'];
export function expertScore(system) {
  const values=KEYS.slice(0,3).map(k=>system.assessments[k].grade);
  return values.includes(-1)?-1:values.reduce((a,b)=>a+b,0);
}
export function rank(value, values) { return value===-1?-1:1+values.filter(v=>v!==-1&&v>value).length; }
export function sortSystems(systems,key='expert',direction='desc',selected=new Set()) {
  const value=s=>key==='expert'?expertScore(s):key==='compare'?Number(selected.has(s.name)):KEYS.includes(key)?s.assessments[key].grade:s[key]||'';
  const sign=direction==='asc'?1:-1;
  // 未知项始终置后；排序仅改变展示顺序，名次仍由完整数据计算。
  return [...systems].sort((a,b)=>{const av=value(a),bv=value(b),au=av===-1||av==='',bu=bv===-1||bv==='';if(au!==bu)return au?1:-1;if(au)return 0;return sign*(typeof av==='string'?av.localeCompare(bv,'zh-CN'):av-bv);});
}
export function radarValues(system) {
  const values=KEYS.map(k=>system.assessments[k].grade),known=values.filter(v=>v!==-1);
  const mean=known.length?known.reduce((a,b)=>a+b,0)/known.length:null;
  return {mean,points:values.map(value=>({value,unknown:value===-1,plot:value===-1?mean:value}))};
}
const COLORS=['#008b7a','#3b67ca','#c16b31'];
const aliases={
 'Crosby + micro1':['Crosby','micro1'], 'TELUS Digital':['TELUS Digital / AI Community'],
 'Xpert / CoreXpert（ByteDance）':['ByteDance Xpert / CoreXpert'], 'Sapien':['Sapien / sapien.io'],
 'Handshake AI':['Handshake / Handshake AI'], 'Labelbox + Alignerr':['Labelbox / Alignerr'],
};
function el(tag,text='',className='') {const n=document.createElement(tag);n.textContent=text;if(className)n.className=className;return n;}
function safeLink(url,label) {const a=el('a',label);try{const u=new URL(url,location.href);if(['https:','http:'].includes(u.protocol)){a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';}}catch{}return a;}
function svg(tag,attrs={},text='') {const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,v);n.textContent=text;return n;}
const axisPoint=(index,value)=>{const angle=-Math.PI/2+index*Math.PI/3,r=150*value/4;return [260+r*Math.cos(angle),215+r*Math.sin(angle)];};
const polygonPoints=value=>KEYS.map((_,i)=>axisPoint(i,value).join(',')).join(' ');

export function drawRadar(systems,dimensions,host,legend) {
  host.replaceChildren();legend.replaceChildren();
  const chart=svg('svg',{viewBox:'0 0 520 430',role:'img','aria-label':'六维专家机制比较；虚线待核实，仅按已知维度均值定位'});
  for(let v=1;v<=4;v++){chart.append(svg('polygon',{points:polygonPoints(v),fill:'none',stroke:'#d8e1e9'}));chart.append(svg('text',{x:267,y:215-150*v/4+12,'font-size':11,fill:'#8293a7'},String(v)));}
  dimensions.forEach((d,i)=>{const [x,y]=axisPoint(i,4);chart.append(svg('line',{x1:260,y1:215,x2:x,y2:y,stroke:'#e0e7ee'}));const [lx,ly]=axisPoint(i,4.65);chart.append(svg('text',{x:lx,y:ly,'text-anchor':'middle','dominant-baseline':'middle','font-size':13,fill:'#34465b'},d.name));});
  systems.forEach((s,index)=>{
    const color=COLORS[index],result=radarValues(s),item=el('li');const swatch=el('span','','swatch');swatch.style.background=color;item.append(swatch,el('strong',s.name));
    item.title=result.mean===null?'全部维度待核实':`已核实 ${result.points.filter(p=>!p.unknown).length}/6；已知维度均值 ${result.mean.toFixed(2)}`;legend.append(item);
    if(result.mean===null)return;
    const coords=result.points.map((p,i)=>axisPoint(i,p.plot));
    if(result.points.every(p=>!p.unknown))chart.append(svg('polygon',{points:coords.map(p=>p.join(',')).join(' '),fill:color,'fill-opacity':0.07,stroke:'none'}));
    result.points.forEach((p,i)=>{
      const next=(i+1)%6,a=coords[i],b=coords[next];chart.append(svg('line',{x1:a[0],y1:a[1],x2:b[0],y2:b[1],stroke:color,'stroke-width':2.3,...(p.unknown||result.points[next].unknown?{'stroke-dasharray':'6 5'}:{})}));
      const node=svg('circle',{cx:a[0],cy:a[1],r:p.unknown?6:4,fill:p.unknown?'#fff':color,stroke:color,'stroke-width':2,tabindex:0});
      node.append(svg('title',{},`${s.name} · ${dimensions[i].name}：${p.unknown?`待核实（-1）；绘图参考均值${result.mean.toFixed(2)}，不参与评分`:`等级${p.value}`}`));chart.append(node);
      if(p.unknown)chart.append(svg('text',{x:a[0]+9,y:a[1]-8,fill:color,'font-size':14,'font-weight':700},'?'));
    });
  });
  host.append(chart);if(!systems.length)host.append(el('p','从下表选择平台开始对比。','empty'));
}

export async function init() {
  const $=id=>document.getElementById(id),status=$('status');
  try {
    const inline=document.getElementById('platform-data');let data;
    if(inline)data=JSON.parse(inline.textContent);
    else{const response=await fetch('./data.json',{cache:'no-cache'});if(!response.ok)throw Error(`HTTP ${response.status}`);data=await response.json();}
    if(data.dimensions.map(d=>d.key).join()!==KEYS.join())throw Error('维度数据不匹配');
    const systems=sortSystems(data.systems),selected=new Set(systems.slice(0,2).map(s=>s.name));let expanded=null;
    $('as-of').textContent=data.as_of;
    if(data.xlsx_url)$('download').replaceChildren(safeLink(data.xlsx_url,'下载 Excel'));
    let sorting={key:'expert',direction:'desc'};
    const headings=[['compare','对比'],['name','平台'],...data.dimensions.map(d=>[d.key,d.name]),['expert_summary','专家核心特色'],['other','其他特色']];
    const hr=el('tr');headings.forEach(([key,label])=>{const th=el('th'),button=el('button',label,'sort-heading'),arrow=el('span','','sort-arrow');button.dataset.sortKey=key;arrow.setAttribute('aria-hidden','true');button.append(arrow);button.addEventListener('click',()=>{sorting={key,direction:sorting.key===key?(sorting.direction==='desc'?'asc':'desc'):['name','expert_summary','other'].includes(key)?'asc':'desc'};render();});if(KEYS.includes(key))th.title='等级1–4，4最佳；—表示待核实（原始值-1），不计入排名。';th.append(button);hr.append(th);});$('thead').replaceChildren(hr);
    function renderDetail(s) {
      const row=el('tr','','detail-row'),cell=el('td');cell.colSpan=headings.length;const wrapper=el('div','','detail-wrapper');
      wrapper.append(el('h3',s.name+' · 机制与证据'),el('p',s.scope,'scope'));
      const table=el('table','','detail-table');const header=el('tr');['维度','等级 / 名次','机制、价值与证据边界','来源'].forEach(h=>header.append(el('th',h)));table.append(header);
      data.dimensions.forEach(d=>{const a=s.assessments[d.key],r=rank(a.grade,systems.map(x=>x.assessments[d.key].grade)),tr=el('tr'),value=el('td',a.grade===-1?'—':`${a.grade} / ${r}`);value.title=a.grade===-1?'待核实（原始值-1），不计入排名':'机制等级 / 维度名次，相同等级允许并列';tr.append(el('td',d.name),value,el('td',a.text,'points'));const source=el('td');source.append(safeLink(a.source,'查看官方来源'));tr.append(source);table.append(tr);});wrapper.append(table);
      const names=aliases[s.name]||[s.name],companies=(data.background||[]).filter(c=>names.includes(c.name));
      if(companies.length){wrapper.append(el('h3','公司背景（与机制排名分别比较）'));const bt=el('table','','detail-table');for(const c of companies){const items=[['主体 / 归属',c.name+'\n'+c.parent_background],['估值 / 市值 / 对价',c.valuation+'\n口径：'+c.valuation_kind+'\n日期：'+c.valuation_date],['员工',c.employees+'\n日期：'+c.employees_date],['专家网络',c.expert_network],['融资 / 规模',c.funding+'\n'+c.scale],['证据边界',c.limitations]];for(const [label,text]of items){const tr=el('tr');tr.append(el('th',label),el('td',text,'points'));bt.append(tr);}const sr=el('tr'),sc=el('td');for(const source of c.sources)sc.append(safeLink(source.url,source.title),el('br'));sr.append(el('th','来源'),sc);bt.append(sr);}wrapper.append(bt);}
      cell.append(wrapper);row.append(cell);return row;
    }
    function render() {
      const visible=sortSystems(systems,sorting.key,sorting.direction,selected);
      hr.querySelectorAll('button').forEach(button=>{const active=button.dataset.sortKey===sorting.key;button.parentElement.setAttribute('aria-sort',active?(sorting.direction==='asc'?'ascending':'descending'):'none');button.querySelector('.sort-arrow').textContent=active?(sorting.direction==='asc'?'▲':'▼'):'↕';button.title=active?`点击切换为${sorting.direction==='asc'?'降':'升'}序`:'点击排序';});
      $('tbody').replaceChildren();$('table-summary').textContent=`${systems.length} 个平台`;$('table-summary').title='点击表头切换升降序；名次按完整名单计算，允许并列；点击平台名称展开评价。';
      visible.forEach(s=>{
        const tr=el('tr'),compare=el('td');const check=el('input');check.type='checkbox';check.checked=selected.has(s.name);check.setAttribute('aria-label','对比 '+s.name);
        check.addEventListener('change',()=>{if(check.checked&&selected.size>=3){check.checked=false;$('live').textContent='最多同时对比3个平台，请先取消一个。';return;}if(check.checked)selected.add(s.name);else selected.delete(s.name);if(sorting.key==='compare')render();renderRadar();});compare.append(check);tr.append(compare);
        const title=el('td'),button=el('button',s.name,'platform-name');button.setAttribute('aria-expanded',String(expanded===s.name));button.addEventListener('click',()=>{expanded=expanded===s.name?null:s.name;render();});
        title.append(button);tr.append(title);
        data.dimensions.forEach(d=>{const grade=s.assessments[d.key].grade,cell=el('td','','score-cell');cell.title=grade===-1?'待核实（原始值-1），不计入排名':'机制等级 / 维度名次';cell.append(el('strong',grade===-1?'—':String(grade),grade===-1?'unknown':`grade grade-${grade}`));if(grade!==-1)cell.append(el('span',`#${rank(grade,systems.map(x=>x.assessments[d.key].grade))}`));tr.append(cell);});
        tr.append(el('td',s.expert_summary,'points core-points'),el('td',s.other||'','points other-points'));$('tbody').append(tr);if(expanded===s.name)$('tbody').append(renderDetail(s));
      });if(!visible.length){const tr=el('tr'),td=el('td','没有匹配的平台。','empty');td.colSpan=headings.length;tr.append(td);$('tbody').append(tr);}
    }
    function renderRadar(){const chosen=systems.filter(s=>selected.has(s.name));$('selected-count').textContent=String(chosen.length);drawRadar(chosen,data.dimensions,$('radar'),$('radar-legend'));}
    status.hidden=true;$('content').hidden=false;render();renderRadar();
  }catch(error){status.replaceChildren(el('span','加载失败：'+error.message+' '));const retry=el('button','重试');retry.addEventListener('click',()=>location.reload());status.append(retry);}
}
if(typeof document!=='undefined'&&document.getElementById('content'))init();
