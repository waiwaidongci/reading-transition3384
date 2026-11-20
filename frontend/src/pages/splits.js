import {domain} from '../constants/domain.js';import {research} from '../stores/research.js';import {session} from '../stores/session.js';import {splitsApi} from '../api/research.js';import {download} from '../api/client.js';import {table} from '../components/common/table.js';import {metrics} from '../components/common/business.js';import {status} from '../components/common/status.js';import {esc,formObject,localTime} from '../utils/format.js';import {action,toast,bind} from '../hooks/actions.js';

const STATE_LABEL={queued:'排队中',running:'切分中',pending:'超时待定',completed:'已完成',infeasible:'已证实无解',failed:'失败'};
const CHECK_LABEL={coverage:'会话全覆盖',group_integrity:'组内同侧',balance:'两侧人数差≤1',both_sides:'两侧都有人',no_route_leak:'无重复路线跨侧',no_trigram_leak:'无连续三章跨侧',no_session_leak:'无会话编号跨侧',verified:'全部核验通过'};

function picker(sources){return sources.map(s=>'<label class="check"><input type="checkbox" name="collection_ids" value="'+s.id+'" checked>'+esc(s.name)+' <small>'+s.record_count+' 条</small></label>').join('')}

function resultSection(job,detail){
 if(job.state==='infeasible')return '<div class="failure"><h2>已证实无解</h2><p>'+esc(detail.reason||job.error||'没有满足条件的切分')+'</p><p class="fine">只有穷尽搜索或可直接证明时才报告无解；可回到文集追加会话后再试。</p></div>';
 if(job.state==='pending')return '<div class="pending"><h2>超时，结果待定</h2><p>'+esc(job.error)+'</p><p class="fine">已保存搜索进度 '+esc(JSON.stringify(detail.progress))+'，续跑会从该进度继续，不会从零重算。</p>'+(session.writable?'<button id="resume-split">从已保存进度续跑 →</button>':'')+'</div>';
 if(job.state==='failed')return '<div class="failure"><h2>切分未完成</h2><p>'+esc(job.error)+'</p></div>';
 if(job.state!=='completed')return '<div class="pending"><span class="spinner"></span><h2>独立 worker 正在切分</h2><p>页面自动刷新；受理时的会话快照已经冻结。</p></div>';
 const result=detail.result||{};
 return metrics(result.summary||[])+'<h2>逐文集核对 <small>练习集 / 验收集 两侧人数最多差一</small></h2>'+table((result.collections||[]).map(c=>({name:c.name,key:c.key,total:c.total,train:c.train,validation:c.validation,difference:c.difference})),[{key:'name',label:'文集'},{key:'key',label:'自然键'},{key:'total',label:'会话数'},{key:'train',label:'练习集'},{key:'validation',label:'验收集'},{key:'difference',label:'人数差'}])+'<h2>核验项</h2><ul class="checks">'+Object.entries(result.checks||{}).filter(([k])=>k!=='verified').map(([k,v])=>'<li class="'+(v?'ok':'bad')+'">'+esc(CHECK_LABEL[k]||k)+'：'+(v?'通过':'未通过')+'</li>').join('')+'</ul><p class="fine">输入校验和 <code>'+esc(result.checksum||'')+'</code> · 搜索节点 '+esc(result.nodes??'')+' · 分组 '+esc(result.group_count??'')+'</p><h2>逐会话切分 <small>共 '+(result.rows||[]).length+' 行，可下载 CSV 逐行核验</small></h2>'+table(result.rows||[],[{key:'group',label:'分组'},{key:'side_label',label:'所属侧'},{key:'collection',label:'文集'},{key:'reference',label:'样本编号'},{key:'session',label:'匿名会话'},{key:'path',label:'章节路径'}],{limit:30})+'<p><button class="secondary" id="export-split">下载逐会话切分 CSV ↗</button></p>';
}

export function splitsPage(){
 const job=research.splitId?research.splits.find(s=>s.id===research.splitId):null;const detail=job?research.splitDetail:null;
 const ledger=research.splits.length?'<div class="ledger">'+research.splits.map(s=>'<a href="#splits" data-split="'+s.id+'" class="'+(s.id===research.splitId?'active':'')+'"><small>#'+s.id+'</small><strong>'+esc(s.name)+'</strong><span class="status '+esc(s.state)+'">'+esc(STATE_LABEL[s.state]||s.state)+'</span><time>'+esc(localTime(s.created_at))+'</time></a>').join('')+'</div>':'<div class="empty">还没有切分任务。选择多个文集一起切分，受理时冻结会话快照。</div>';
 return '<section class="page-heading"><p class="eyebrow">05 / SPLIT & VERIFY</p><h1>同一路线不跨两侧，验收分数才可信。</h1><p>同一路线、共享连续三章（含间接相连）的会话放在同一侧；每个文集练习集与验收集都有人，人数最多差一。受理即冻结会话，之后补录不混入。</p></section><div class="split"><section><h2>切分任务</h2>'+ledger+(job?'<div class="run-title"><h2>'+esc(job.name)+'</h2><span class="status '+esc(job.state)+'">'+esc(STATE_LABEL[job.state]||job.state)+'</span><small>#'+job.id+' · 超时预算 '+job.budget_seconds+' 秒'+(job.attempts?' · 尝试 '+job.attempts+' 次':'')+'</small><button id="refresh-split" class="secondary">刷新状态</button></div><p class="fine">冻结于 '+esc(localTime(job.created_at))+' · 来源改名或追加记录不影响本次快照</p>'+(detail?resultSection(job,detail):'<div class="pending">读取切分结果中…</div>'):'')+'</section><aside class="entry-sheet">'+(session.writable?'<h2>新建切分</h2><form id="split-form"><label>任务名称<input name="name" required value="十一月验收切分"></label><fieldset class="checks"><legend>一起切分的文集</legend>'+picker(research.sources)+'</fieldset><label>搜索时间预算（秒）<input name="budget_seconds" type="number" min="1" max="300" step="1" required value="20"></label><p class="fine">多个文集一起切分；同一会话只进入一侧。超时则保留进度待定，可续跑。</p><button type="submit">冻结会话并切分 →</button></form>':'<h2>阅读权限</h2><p>阅读者可以查看切分结果并下载逐会话核验表；新建与续跑由负责人或分析师执行。</p>')+'</aside></div>';
}

export function bindSplits(refresh){
 action('#split-form',async form=>{const data=formObject(form);const ids=[...form.querySelectorAll('input[name="collection_ids"]:checked')].map(x=>Number(x.value));if(!ids.length)throw Error('请至少选择一个文集');const j=await splitsApi.create({name:data.name,collection_ids:ids,budget_seconds:Number(data.budget_seconds)});research.splitId=j.id;await refresh();toast('切分任务已受理，会话快照已冻结')});
 for(const a of document.querySelectorAll('[data-split]'))a.onclick=async()=>{research.splitId=Number(a.dataset.split);await refresh()};
 bind('#refresh-split','click',()=>refresh());
 bind('#resume-split','click',async()=>{try{await splitsApi.resume(research.splitId);await refresh();toast('已从保存的进度重新排队')}catch(e){toast(e.message,true)}});
 bind('#export-split','click',async()=>{try{await download('/splits/'+research.splitId+'/export','split-'+research.splitId+'.csv')}catch(e){toast(e.message,true)}});
}
