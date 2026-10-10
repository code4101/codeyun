/** 专家机制分数与背景数据分别维护；未知值始终保留-1，绘图估计不回写。 */
export const MAX_SCORE=5;
export const KEYS = ['expert','environment','assets','evaluation','efficiency','delivery'];
export const assessmentScore=assessment=>assessment.score??assessment.grade;
/** 保留公司原币事实，只在展示层用同日央行汇率交叉折算美元。 */
export function usdValuation(company,exchange) {
  const amount=company.valuation_amount,currency=company.valuation_currency,quote=exchange?.quotes?.[currency],usdQuote=exchange?.quotes?.USD;
  if(!Number.isFinite(amount)||!quote||!usdQuote)return null;
  const rate=quote/usdQuote,usd=amount*rate;
  return {amount:usd,text:`约${(usd/1e8).toFixed(2)}亿美元`,detail:`原币金额：${amount.toLocaleString('en-US')} ${currency}\n参考汇率：1 ${currency} = ${rate.toFixed(6)} USD；${exchange.date}，${exchange.provider}\n折算方法：原币金额 × 原币兑加元 ÷ 美元兑加元\n汇率来源：${exchange.source}`};
}
export function rank(value, values) { return value===-1?-1:1+values.filter(v=>v!==-1&&v>value).length; }
export function averageScore(system) {
  const known=KEYS.map(k=>assessmentScore(system.assessments[k])).filter(value=>value!==-1);
  return known.length?known.reduce((sum,value)=>sum+Math.round(value*10),0)/(10*known.length):-1;
}
export function sortSystems(systems,key='average',direction='desc') {
  const value=s=>key==='average'?averageScore(s):KEYS.includes(key)?assessmentScore(s.assessments[key]):s[key]||'';
  const sign=direction==='asc'?1:-1;
  // 未知项始终置后；排序仅改变展示顺序，名次仍由完整数据计算。
  return [...systems].sort((a,b)=>{const av=value(a),bv=value(b),au=av===-1||av==='',bu=bv===-1||bv==='';if(au!==bu)return au?1:-1;if(au)return 0;return sign*(typeof av==='string'?av.localeCompare(bv,'zh-CN'):av-bv);});
}
export function radarValues(system) {
  const values=KEYS.map(k=>assessmentScore(system.assessments[k])),known=values.filter(v=>v!==-1);
  const mean=known.length?averageScore(system):null;
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
const axisPoint=(index,value)=>{const angle=-Math.PI/2+index*Math.PI/3,r=150*value/MAX_SCORE;return [260+r*Math.cos(angle),215+r*Math.sin(angle)];};
const polygonPoints=value=>KEYS.map((_,i)=>axisPoint(i,value).join(',')).join(' ');

export function drawRadar(systems,dimensions,host,legend) {
  host.replaceChildren();legend.replaceChildren();
  const chart=svg('svg',{viewBox:'0 0 520 430',role:'img','aria-label':'六维专家机制比较；虚线待核实，仅按已知维度均值定位'});
  for(let v=1;v<=MAX_SCORE;v++){chart.append(svg('polygon',{points:polygonPoints(v),fill:'none',stroke:'#d8e1e9'}));chart.append(svg('text',{x:267,y:215-150*v/MAX_SCORE+12,'font-size':11,fill:'#8293a7'},String(v)));}
  dimensions.forEach((d,i)=>{const [x,y]=axisPoint(i,MAX_SCORE);chart.append(svg('line',{x1:260,y1:215,x2:x,y2:y,stroke:'#e0e7ee'}));const [lx,ly]=axisPoint(i,MAX_SCORE*1.1625);chart.append(svg('text',{x:lx,y:ly,'text-anchor':'middle','dominant-baseline':'middle','font-size':13,fill:'#34465b'},d.name));});
  systems.forEach((s,index)=>{
    const color=COLORS[index],result=radarValues(s),item=el('li');const swatch=el('span','','swatch');swatch.style.background=color;item.append(swatch,el('strong',s.name));
    item.title=result.mean===null?'全部维度待核实':`已核实 ${result.points.filter(p=>!p.unknown).length}/6；已知维度均值 ${result.mean.toFixed(2)}`;legend.append(item);
    if(result.mean===null)return;
    const coords=result.points.map((p,i)=>axisPoint(i,p.plot));
    if(result.points.every(p=>!p.unknown))chart.append(svg('polygon',{points:coords.map(p=>p.join(',')).join(' '),fill:color,'fill-opacity':0.07,stroke:'none'}));
    result.points.forEach((p,i)=>{
      const next=(i+1)%6,a=coords[i],b=coords[next];chart.append(svg('line',{x1:a[0],y1:a[1],x2:b[0],y2:b[1],stroke:color,'stroke-width':2.3,...(p.unknown||result.points[next].unknown?{'stroke-dasharray':'6 5'}:{})}));
      const node=svg('circle',{cx:a[0],cy:a[1],r:p.unknown?6:4,fill:p.unknown?'#fff':color,stroke:color,'stroke-width':2,tabindex:0});
      node.append(svg('title',{},`${s.name} · ${dimensions[i].name}：${p.unknown?`待核实（-1）；绘图参考均值${result.mean.toFixed(2)}，不参与评分`:`评分${p.value.toFixed(1)}`}`));chart.append(node);
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
    const systems=sortSystems(data.systems,'average').map((system,index)=>({...system,number:index+1})),selected=new Set(systems.slice(0,2).map(s=>s.name));let expanded=null;
    const companiesFor=system=>(data.background||[]).filter(company=>(aliases[system.name]||[system.name]).includes(company.name));
    $('as-of').textContent=data.as_of;
    if(data.xlsx_url)$('download').replaceChildren(safeLink(data.xlsx_url,'下载 Excel'));
    let sorting={key:'average',direction:'desc'};
    const headings=[['average','平均分'],['name','平台'],...data.dimensions.map(d=>[d.key,d.name])];
    const hr=el('tr');headings.forEach(([key,label])=>{const th=el('th'),button=el('button',label,'sort-heading'),arrow=el('span','','sort-arrow');button.dataset.sortKey=key;arrow.setAttribute('aria-hidden','true');button.append(arrow);button.addEventListener('click',()=>{sorting={key,direction:sorting.key===key?(sorting.direction==='desc'?'asc':'desc'):['number','name','expert_summary','other'].includes(key)?'asc':'desc'};render();});if(KEYS.includes(key))th.title=data.dimensions.find(d=>d.key===key).description+'\n'+data.scoring.method+'\n主观评议，非实测；空白表示待核实。';th.append(button);hr.append(th);});$('thead').replaceChildren(hr);
    function renderDetail(s) {
      const row=el('tr','','detail-row'),cell=el('td');cell.colSpan=headings.length;const wrapper=el('div','','detail-wrapper');
      const companies=companiesFor(s);
      if(companies.length){wrapper.append(el('h3','公司背景（与机制排名分别比较）'));const bt=el('table','','detail-table');for(const c of companies){const items=[['主体 / 归属',c.name+'\n'+c.parent_background],['成立',c.founded_date+'\n'+c.founding],['估值 / 市值 / 对价',(usdValuation(c,data.exchange_rates)?.text||c.valuation)+'\n口径：'+c.valuation_kind+'\n日期：'+c.valuation_date],['员工',c.employees+'\n日期：'+c.employees_date],['专家网络',c.expert_network],['融资 / 规模',c.funding+'\n'+c.scale],['证据边界',c.limitations]];for(const [label,text]of items){const tr=el('tr');const value=el('td',text,'points');if(label==='估值 / 市值 / 对价'){const converted=usdValuation(c,data.exchange_rates);if(converted)value.title=c.valuation+'\n'+converted.detail;}tr.append(el('th',label),value);bt.append(tr);}const sr=el('tr'),sc=el('td');for(const source of c.sources)sc.append(safeLink(source.url,source.title),el('br'));if(usdValuation(c,data.exchange_rates))sc.append(safeLink(data.exchange_rates.source,'美元折算汇率 · '+data.exchange_rates.date),el('br'));sr.append(el('th','来源'),sc);bt.append(sr);}wrapper.append(bt);}
      cell.append(wrapper);row.append(cell);return row;
    }
    function render() {
      const visible=sortSystems(systems,sorting.key,sorting.direction);
      hr.querySelectorAll('button').forEach(button=>{const active=button.dataset.sortKey===sorting.key;button.parentElement.setAttribute('aria-sort',active?(sorting.direction==='asc'?'ascending':'descending'):'none');button.querySelector('.sort-arrow').textContent=active?(sorting.direction==='asc'?'▲':'▼'):'↕';button.title=active?`点击切换为${sorting.direction==='asc'?'降':'升'}序`:'点击排序';});
      $('tbody').replaceChildren();$('table-summary').textContent=`${systems.length} 个平台`;$('table-summary').title=(data.coverage?`原文11项 + 新增14项均已覆盖；另有机制研究补充对象。\n`:'')+'点击表头切换升降序；名次按完整名单计算，允许并列；点击平台名称展开公司背景。';
      if(data.dimension_summary){
        const summary=el('tr','','summary-row');summary.append(el('td','0','platform-overview'),el('td','总结','summary-label'));
        data.dimensions.forEach(d=>{const cell=el('td','','summary-cell'),list=el('ol','','dimension-strengths');for(const point of data.dimension_summary[d.key]||[])list.append(el('li',point));cell.title='本指标的优秀设计归纳；不参与评分、排名或雷达对比。';cell.append(list);summary.append(cell);});$('tbody').append(summary);
      }
      visible.forEach(s=>{
        const tr=el('tr'),overview=el('td','','platform-overview'),selection=el('div','','platform-selection'),number=el('span',String(s.number),'platform-number');number.title='按六维已核实分数平均值降序编号；待核实项不计入平均分，相同均值保留原顺序。';const check=el('input');check.type='checkbox';check.checked=selected.has(s.name);check.setAttribute('aria-label','对比 '+s.name);
        check.addEventListener('change',()=>{if(check.checked&&selected.size>=3){check.checked=false;$('live').textContent='最多同时对比3个平台，请先取消一个。';return;}if(check.checked)selected.add(s.name);else selected.delete(s.name);renderRadar();});selection.append(number,check);overview.append(selection);
        const title=el('td'),button=el('button',s.name,'platform-name');button.setAttribute('aria-expanded',String(expanded===s.name));button.addEventListener('click',()=>{expanded=expanded===s.name?null:s.name;render();});
        button.title=[s.category,s.listing_source,s.evidence_scope].filter(Boolean).join('\n');title.append(button);
        const average=averageScore(s),known=KEYS.map(key=>assessmentScore(s.assessments[key])).filter(score=>score!==-1),averageLine=el('p','','platform-average');
        averageLine.append(el('strong',average===-1?'':average.toFixed(2)));
        averageLine.title=`满分5分；按新六维统一量表整理的主观评议，非平台实测。平均分仅覆盖有证据的指标，不能视为完整能力排名。\n已核实${known.length}/6项；${known.length?`(${known.map(score=>score.toFixed(1)).join(' + ')}) ÷ ${known.length} = ${average.toFixed(2)}`:'无已核实分数'}；待核实项不计入平均分。`;
        overview.append(averageLine);tr.append(overview);
        const companies=companiesFor(s);
        for(const company of companies){
          const profile=el('div','','company-brief');if(companies.length>1)profile.append(el('strong',company.name,'company-subject'));
          for(const item of company.card||[]){
            if(item.field==='parent_background'&&['独立公司','独立上市公司'].includes(item.text))continue;
            const converted=item.field==='valuation'?usdValuation(company,data.exchange_rates):null,text=converted?.text||item.text;
            if(text==null||['','—','–','-','-1'].includes(String(text).trim()))continue;
            const line=el('p'),source=company.sources[item.source_index],value=source&&text!=='—'?safeLink(source.url,text):el('span',text),date=item.field==='valuation'?company.valuation_date:item.field==='employees'?company.employees_date:'';
            value.title=[company[item.field],date&&`统计日期 / 口径：${date}`,converted?.detail,source&&`来源：${source.title}`].filter(Boolean).join('\n');value.className='company-value';line.append(el('span',item.label+'：','company-label'),value);profile.append(line);
          }if(profile.querySelector('p'))title.append(profile);
        }tr.append(title);
        data.dimensions.forEach(d=>{
          const assessment=s.assessments[d.key],score=assessmentScore(assessment),cell=el('td','','score-cell'),rating=el('div','','rating-line');rating.title=score===-1?'待核实（原始值-1），不计入排名':`评分 / 维度名次；满分5分，结构化主观评议，非实测性能。\n${assessment.score_basis||''}\n证据范围：${assessment.evidence_scope||''}`;
          rating.append(el('strong',score===-1?'':score.toFixed(1),score===-1?'unknown':`grade grade-${assessment.grade}`));if(score!==-1)rating.append(el('span',`#${rank(score,systems.map(x=>assessmentScore(x.assessments[d.key])))}`));
          const strengths=assessment.strengths||[];
          if(strengths.length){const source=safeLink(assessment.source,'↗');source.className='source-link';source.title='查看来源；证据范围：'+assessment.evidence_scope;source.setAttribute('aria-label',s.name+' · '+d.name+' 来源');rating.append(source);}
          cell.append(rating);if(strengths.length){const list=el('ol','','dimension-strengths');strengths.forEach(point=>list.append(el('li',point)));cell.append(list);}tr.append(cell);
        });
        $('tbody').append(tr);if(expanded===s.name)$('tbody').append(renderDetail(s));
      });if(!visible.length){const tr=el('tr'),td=el('td','没有匹配的平台。','empty');td.colSpan=headings.length;tr.append(td);$('tbody').append(tr);}
    }
    function renderRadar(){const chosen=systems.filter(s=>selected.has(s.name));$('selected-count').textContent=String(chosen.length);drawRadar(chosen,data.dimensions,$('radar'),$('radar-legend'));}
    status.hidden=true;$('content').hidden=false;render();renderRadar();
  }catch(error){status.replaceChildren(el('span','加载失败：'+error.message+' '));const retry=el('button','重试');retry.addEventListener('click',()=>location.reload());status.append(retry);}
}
if(typeof document!=='undefined'&&document.getElementById('content'))init();
