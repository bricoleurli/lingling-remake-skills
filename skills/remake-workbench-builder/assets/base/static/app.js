const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const token=$('meta[name="workbench-token"]').content;
let catalog=[], jobs=[], current=null, historyCurrent=null, examples=[], settings={}, signature='', historySignature='', activeView='create';
const names={planning:'正在生成内容',awaiting_confirmation:'等待确认内容',generating:'正在逐页生成',completed:'已完成',failed:'执行遇到问题',stopped:'已停止',interrupted:'等待恢复'};
async function api(path,body){const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':token},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.error||'请求失败');return d;}
function notify(text,error=false){const n=$('#notice');n.textContent=text;n.classList.toggle('error',error);n.hidden=false;}
function view(which){activeView=which;$('#notice').hidden=true;for(const k of ['create','library','history','settings']){$('#'+k+'-view').hidden=k!==which;$('#nav-'+k).classList.toggle('active',k===which);}$('#breadcrumb').textContent='工作空间 / '+({create:'生成工作台',library:'玩法库',history:'生成历史',settings:'设置'}[which]);}
function media(j,file){return `/media/${j.id}/${file}`;}
function chooseProduct(){const p=catalog.find(p=>p.key===$('#product').value);if(!p)return;$('#product-desc').textContent=p.description;$('#character').hidden=!p.cover;if(p.cover)$('#character').src='/product/'+p.key+'/'+p.cover;$('.character strong').textContent=p.presentation.asset_title;$('.character p').textContent=p.presentation.asset_note;$('#input-fields').innerHTML=p.inputs.texts.map(f=>`<label>${esc(f.name)}<textarea name="${esc(f.id)}" rows="4" maxlength="${f.max_chars}" placeholder="${esc(f.placeholder||'填写本次内容')}" required></textarea></label>`).join('');}
function connections(){const ok=settings.text?.has_key&&settings.image?.has_key;$('#connection-hint').innerHTML=ok?'<p class="connected">● 模型连接已保存</p>':'<button type="button" class="text-button" data-settings>首次使用：先设置模型连接 ↗</button>';}
async function refreshHistory(){
 const all=await api('/api/jobs');jobs=all.filter(j=>!j.example);
 const samples=all.filter(j=>j.example);examples=await Promise.all(samples.map(j=>examples.find(e=>e.id===j.id)||api('/api/jobs/'+j.id)));
 $('#history-count').textContent=jobs.length;
 $('#history-list').innerHTML=jobs.length?jobs.map(j=>`<button class="history-card" data-job="${j.id}"><span class="badge">${esc(names[j.status])}</span><h2>${esc(j.topic)}</h2><p>${esc(j.name)}</p><div>${j.count} 页 <span>${esc(j.created.slice(0,10))}　查看 →</span></div></button>`).join(''):'<div class="empty-history"><span class="empty-symbol">◷</span><h2>还没有生成记录</h2><p>提交生成任务后，会自动保存在这里。<br>玩法的效果示例请到“玩法库”查看。</p><button class="primary" data-new>去生成第一套内容 →</button></div>';
 renderLibrary();
}
function renderLibrary(){
 $('#library-count').textContent=catalog.length;$('#library-total').textContent=catalog.length+' 个玩法';
 $('#library-list').innerHTML=catalog.length?catalog.map(p=>{const sample=examples.find(e=>e.product===p.key);return `<article class="library-card"><div class="library-preview">${sample?sample.pages.slice(0,3).map(page=>`<img src="${media(sample,page.file)}" alt="玩法效果示例：${esc(page.content.title)}" loading="lazy">`).join(''):'<div class="no-sample">暂无效果示例</div>'}</div><div class="library-info"><span class="badge">已添加 · 可在工作台选择</span><h2>${esc(p.name)}</h2><p>${esc(p.description)}</p><div class="library-input">需要填写：${p.inputs.texts.map(f=>esc(f.name)).join('、')}</div><div class="library-actions"><button data-details="${esc(p.key)}">查看玩法与示例</button><button class="primary" data-use="${esc(p.key)}">使用此玩法 →</button></div></div></article>`;}).join(''):'<div class="empty-history"><h2>还没有添加玩法</h2><p>把已确认的拆解结果交给搭建 Agent，添加第一个玩法。</p></div>';
}
const productDetails=new Map();
let detailFlow=null,detailRequest=0;
const promptText=step=>Array.isArray(step.prompt)?step.prompt.join('\n'):String(step.prompt||'');
function detailTab(which){
 document.querySelectorAll('[data-detail-tab]').forEach(b=>{const on=b.dataset.detailTab===which;b.classList.toggle('active',on);b.setAttribute('aria-pressed',String(on));});
 document.querySelectorAll('[data-detail-panel]').forEach(p=>p.hidden=p.dataset.detailPanel!==which);
}
async function showLibraryDetail(key){
 const p=catalog.find(p=>p.key===key);if(!p)return;
 const request=++detailRequest;
 if(!productDetails.has(key))productDetails.set(key,await api('/api/products/'+key));
 if(request!==detailRequest)return;
 detailFlow=productDetails.get(key);const f=detailFlow,samples=examples.filter(e=>e.product===key);
 const ui=f.presentation;const stages=ui.stages;
 $('#library-list').hidden=true;$('#library-detail').hidden=false;
 $('#library-detail').innerHTML=`<button class="text-button" data-back-library>← 返回玩法库</button>
 <div class="detail-heading"><div><span class="section-label">玩法详情</span><h2>${esc(p.name)}</h2><p>${esc(p.description)}</p></div><button class="primary" data-use="${esc(p.key)}">使用此玩法 →</button></div>
 <div class="template-facts"><div><span>每次只需填写</span><strong>${p.inputs.texts.map(f=>esc(f.name)).join('、')}</strong></div><div><span>生成结果</span><strong>${esc(ui.result)}</strong></div><div><span>自带素材</span><strong>${esc(ui.asset_summary)}</strong></div></div>
 <div class="detail-tabs" role="group" aria-label="玩法详情内容"><button class="active" data-detail-tab="flow" aria-pressed="true">完整流程</button><button data-detail-tab="prompts" aria-pressed="false">提示词 <span>${f.steps.length}</span></button><button data-detail-tab="examples" aria-pressed="false">效果示例</button></div>
 <section data-detail-panel="flow"><div class="detail-intro"><h3>从一个主题，到一套图文</h3><p>${esc(ui.intro)}以下是这个玩法在当前工作台中的执行顺序。</p></div>
 <ol class="workflow-list">${stages.map(({title,actor,description:desc,prompt_step},i)=>`<li><span class="workflow-number">${String(i+1).padStart(2,'0')}</span><div><div class="workflow-heading"><h3>${esc(title)}</h3><span class="badge">${esc(actor)}</span></div><p>${esc(desc)}</p>${prompt_step?`<button class="text-button" data-jump-prompt="${f.steps.findIndex(s=>s.id===prompt_step)}">查看这一步的完整提示词 ↗</button>`:''}</div></li>`).join('')}</ol>
 <div class="detail-footnote">正常执行：${esc(ui.calls)}。失败重试或重新生成会增加调用次数。玩法规则固定，日常使用只需填写内容。</div></section>
 <section data-detail-panel="prompts" hidden><div class="detail-intro"><h3>这个玩法使用的完整提示词</h3><p>直接读取已保存的玩法原文，可展开查看或复制。这里的 {{topic}}、{{pages}}、{{item}} 等是变量，运行时会替换成这次任务的实际内容。提示词在玩法层统一维护。</p></div>
 <div class="prompt-list">${f.steps.map((step,i)=>`<details class="prompt-card" id="template-prompt-${i}"><summary><span class="workflow-number">${String(i+1).padStart(2,'0')}</span><div><strong>${esc(step.name)}</strong><small>${esc(step.type==='image'?'图片模型':'文字模型')} · ${esc(step.consume==='once'?'执行一次':step.consume==='per_item'?'逐项执行':step.consume)} · 完整原文</small></div></summary><div class="prompt-content"><div class="prompt-toolbar"><span>只读 · ${promptText(step).length.toLocaleString()} 字符</span><button data-copy-prompt="${i}">复制提示词</button></div><pre>${esc(promptText(step))}</pre></div></details>`).join('')}</div>
 <div class="detail-footnote">${esc(ui.reference_note)}生图前确认区可以查看替换变量后的逐页提示词。</div></section>
 <section data-detail-panel="examples" hidden><div class="detail-intro"><h3>这套玩法做出来是什么样</h3><p>以下是此前已确认的效果示例，用于了解成品，不计入你的生成历史。点击图片可放大查看。</p></div>${samples.length?samples.map(j=>`<section class="template-example"><div class="result-heading"><h3>${esc(j.topic)} <span>${j.pages.length} 张</span></h3><a class="secondary button-link" href="/api/jobs/${j.id}/export">下载示例</a></div><div class="image-grid">${j.pages.map((page,i)=>`<article class="image-card"><button class="image-button library-image" data-example-image="${esc(page.file)}" data-example-id="${j.id}"><img src="${media(j,page.file)}" alt="示例第${i+1}页：${esc(page.content.title)}" loading="lazy"></button><div class="image-caption"><strong>${String(i+1).padStart(2,'0')} · ${esc(page.content.title)}</strong></div></article>`).join('')}</div><p class="sample-caption">${esc(j.provenance||'已确认的效果示例，来自此前的制作与验收。')}</p></section>`).join(''):'<p class="no-sample">这个玩法还没有附带效果示例。</p>'}</section>`;
 view('library');
}
function showHistoryList(){historyCurrent=null;$('#history-detail').hidden=true;$('#history-list').hidden=false;view('history');}
function copyBlock(c){return `<section class="publish"><div class="section-label">配套发布文案</div><div class="copy-top"><h3>${esc(c.title)}</h3><button data-copy="title">复制标题</button></div><p class="body-copy">${esc(c.body)}</p><div class="copy-bottom"><span>${(c.tags||[]).map(t=>'#'+esc(t)).join(' ')}</span><button data-copy="all">复制整段文案</button></div></section>`;}
function renderJob(j,surface='create'){
 if(j.example)return;
 const busy=['planning','generating'].includes(j.status);
 if(surface==='history'){
  historyCurrent=j;$('#history-list').hidden=true;$('#history-detail').hidden=false;$('#history-title').textContent=j.topic;$('#history-meta').textContent=j.name+' · '+j.created.replace('T',' ');$('#history-status').textContent=names[j.status];
 }else{
  current=j;$('#welcome').hidden=true;$('#job-content').hidden=false;$('#area-title').textContent=j.topic;$('#area-badge').textContent=names[j.status];$('#start').disabled=busy;
  const stage=j.status==='completed'?3:j.stage==='images'?2:j.status==='awaiting_confirmation'?1:0;
  ['plan','review','image','done'].forEach((step,i)=>$('#step-'+step).classList.toggle('reached',i<=stage));
 }
let h=j.example?`<div class="provenance">${esc(j.provenance)}</div>`:'';
if(busy)h+=`<div class="progress-box"><span class="spinner"></span><div><strong>${j.status==='planning'?(j.stage==='txt_copy'?'正在撰写配套发布文案':'正在组织内容与自动分页'):`正在生成第 ${j.current_page||1} / ${j.pages.length} 页`}</strong><p>可以切换页面，已完成的内容会保存在本机。</p></div><button data-action="stop" ${j.cancel?'disabled':''}>${j.cancel?'正在停止':'停止后续步骤'}</button></div>`;
if(j.error)h+=`<div class="job-error">${esc(j.error)}</div>`;
if(['failed','interrupted','stopped'].includes(j.status)&&!j.example)h+='<button class="secondary" data-action="retry">从未完成的步骤继续</button>';
if(j.pages.length){
h+=`<div class="result-heading"><h3>${j.stage==='images'?'本次作品':'本次内容方案'} <span>${j.pages.length} 页</span></h3>${j.pages.every(p=>p.file)?`<a class="primary small" href="/api/jobs/${j.id}/export">下载整套作品 ↓</a>`:''}</div>`;
if(j.stage==='images'){
h+='<div class="image-grid">'+j.pages.map((p,i)=>`<article class="image-card"><div class="image-wrap">${p.file?`<button class="image-button" data-image="${esc(p.file)}"><img src="${media(j,p.file)}" alt="第${i+1}页：${esc(p.content.title)}" loading="lazy"></button>`:`<div class="image-placeholder"><span>${String(i+1).padStart(2,'0')}</span><p>${busy&&j.current_page===i+1?'生成中…':'等待生成'}</p></div>`}</div><div class="image-caption"><strong>${String(i+1).padStart(2,'0')} · ${esc(p.content.title.replace(/\n/g,' '))}</strong><div class="image-actions">${p.file?`<a href="${media(j,p.file)}" download="${String(i+1).padStart(2,'0')}.png">下载</a>${!j.example?`<button data-redraw="${i}" ${busy?'disabled':''}>重新生成</button>`:''}`:''}</div>${p.versions.length>1?`<details><summary>保留了 ${p.versions.length} 个版本</summary><div class="versions">${p.versions.map((v,n)=>`<button data-image="${v}">版本 ${n+1}${v===p.file?' · 当前':''}</button>`).join('')}</div></details>`:''}</div></article>`).join('')+'</div>';
}else{
h+='<div class="plan-list">'+j.pages.map((p,i)=>`<details class="plan-card" ${i===0?'open':''}><summary><span class="page-number">${String(i+1).padStart(2,'0')}</span><strong>${esc(p.content.title.replace(/\n/g,' '))}</strong><span class="page-role">${i===0?'封面':i===j.pages.length-1?'总结':'正文'}</span></summary><div class="plan-text">${p.content.display_text.map(t=>`<p>${esc(t)}</p>`).join('')}</div></details>`).join('')+'</div>';
}
}
if(j.copy?.title)h+=copyBlock(j.copy);
if(j.status==='awaiting_confirmation')h+='<details class="execution-details"><summary>查看本次生成明细（只读）</summary><button data-plan>加载逐页提示词与图片输入</button><div class="execution-plan"></div></details>';
if(j.status==='awaiting_confirmation')h+=`<div class="confirm-bar"><div><strong>内容准备好了，确认后开始生图。</strong><p>${esc(j.presentation?.confirmation||'沿用本任务保存的玩法规则')}，共 ${j.pages.length} 次生图调用。</p></div><button class="primary" data-action="confirm">确认内容，生成整套图片 →</button></div><p class="model-note">点击后将调用你配置的生图服务，按该服务规则计费。</p>`;
$(surface==='history'?'#history-content':'#job-content').innerHTML=h;}
async function loadJob(id){const j=await api('/api/jobs/'+id);if(j.example){await showLibraryDetail(j.product);return;}historySignature=JSON.stringify(j);renderJob(j,'history');view('history');}
async function act(action,index,surface){const selected=surface==='history'?historyCurrent:current;const j=await api(`/api/jobs/${selected.id}/${action}`,index===undefined?{}:{index});signature='';historySignature='';renderJob(j,surface);if(current?.id===j.id&&surface==='history')renderJob(j,'create');await refreshHistory();}
function reset(key){
 if(current&&['planning','generating'].includes(current.status)){view('create');notify('当前任务正在生成，请等它完成或停止后再新建。');return;}
 current=null;signature='';$('#job-content').hidden=true;$('#welcome').hidden=false;$('#area-title').textContent='本次生成';$('#area-badge').textContent='等待输入';$('#start').disabled=!catalog.length;
 ['plan','review','image','done'].forEach(step=>$('#step-'+step).classList.remove('reached'));
 if(key)$('#product').value=key;chooseProduct();view('create');
}
$('#nav-create').onclick=()=>view('create');$('#nav-library').onclick=()=>{$('#library-list').hidden=false;$('#library-detail').hidden=true;view('library');};
$('#nav-history').onclick=()=>{showHistoryList();refreshHistory().catch(e=>notify(e.message,true));};$('#nav-settings').onclick=()=>view('settings');$('#back-history').onclick=showHistoryList;
$('#new-from-history').onclick=()=>reset();$('#new-task').onclick=()=>reset();$('#product').onchange=chooseProduct;
$('#create-form').onsubmit=async e=>{e.preventDefault();$('#start').disabled=true;try{const values=Object.fromEntries(new FormData(e.target));renderJob(await api('/api/jobs',{product:$('#product').value,values}));await refreshHistory();}catch(e){notify(e.message,true);$('#start').disabled=false;}};
$('#settings-form').onsubmit=async e=>{e.preventDefault();const form=new FormData(e.target),body={};for(const k of ['text','image']){body[k]={};for(const f of ['base_url','model','api_key',...(k==='image'?['size']:[])])body[k][f]=form.get(k+'-'+f);}try{settings=await api('/api/settings',body);e.target.querySelectorAll('input[type=password]').forEach(n=>n.value='');fillSettings();connections();notify('连接设置已保存。尚未调用模型。');}catch(e){notify(e.message,true);}};
function fillSettings(){for(const k of ['text','image']){for(const f of ['base_url','model',...(k==='image'?['size']:[])])$(`[name="${k}-${f}"]`).value=settings[k]?.[f]||'';$('#'+k+'-key-state').textContent=settings[k]?.has_key?'已保存密钥，留空可保留。':'尚未配置。';}}
document.addEventListener('click',async e=>{const b=e.target.closest('button,[data-settings]');if(!b||b.disabled)return;try{
const surface=b.closest('#history-detail')?'history':'create';const selected=surface==='history'?historyCurrent:current;
if(b.hasAttribute('data-settings'))view('settings');
if(b.dataset.use)reset(b.dataset.use);
if(b.hasAttribute('data-new'))reset();
if(b.dataset.details)await showLibraryDetail(b.dataset.details);
if(b.dataset.detailTab)detailTab(b.dataset.detailTab);
if(b.dataset.jumpPrompt!==undefined){detailTab('prompts');const panel=$('#template-prompt-'+b.dataset.jumpPrompt);panel.open=true;panel.scrollIntoView({behavior:'smooth',block:'start'});}
if(b.dataset.copyPrompt!==undefined){await navigator.clipboard.writeText(promptText(detailFlow.steps[Number(b.dataset.copyPrompt)]));b.textContent='已复制';}
if(b.hasAttribute('data-back-library')){$('#library-list').hidden=false;$('#library-detail').hidden=true;}
if(b.dataset.exampleImage){const sample=examples.find(j=>j.id===b.dataset.exampleId);$('#large-image').src=media(sample,b.dataset.exampleImage);$('#large-download').href=media(sample,b.dataset.exampleImage);$('#lightbox').showModal();}
if(b.dataset.job)await loadJob(b.dataset.job);
if(b.hasAttribute('data-plan')){const d=await api('/api/jobs/'+selected.id+'/plan');$(surface==='history'?'#history-content .execution-plan':'#job-content .execution-plan').innerHTML=d.pages.map(p=>`<details><summary>第 ${p.index} 页 · ${p.image_inputs?'1 张固定角色参考图':'纯文字 · 无参考图'} · 1次生图</summary><pre>${esc(p.prompt)}</pre></details>`).join('');b.hidden=true;}
if(b.dataset.action){b.disabled=true;await act(b.dataset.action,undefined,surface);}
if(b.dataset.redraw!==undefined){b.disabled=true;await act('redraw',Number(b.dataset.redraw),surface);}
if(b.dataset.image){$('#large-image').src=media(selected,b.dataset.image);$('#large-download').href=media(selected,b.dataset.image);$('#lightbox').showModal();}
if(b.dataset.copy){const c=selected.copy;await navigator.clipboard.writeText(b.dataset.copy==='title'?c.title:c.title+'\n\n'+c.body+'\n\n'+c.tags.map(t=>'#'+t).join(' '));notify('已复制。');}
}catch(e){notify(e.message,true);b.disabled=false;}});
$('#close-lightbox').onclick=()=>$('#lightbox').close();$('#lightbox').onclick=e=>{if(e.target===$('#lightbox'))$('#lightbox').close();};
async function init(){
 [catalog,settings]=await Promise.all([api('/api/catalog'),api('/api/settings')]);$('#product').innerHTML=catalog.map(p=>`<option value="${esc(p.key)}">${esc(p.name)}</option>`).join('');chooseProduct();fillSettings();connections();await refreshHistory();
 $('#area-title').textContent='本次生成';$('#area-badge').textContent='等待输入';
 if(!catalog.length){$('#start').disabled=true;notify('尚未安装玩法，请让 Agent 使用搭建 Skill 添加。');}
}
init().catch(e=>notify(e.message,true));
let polling=false;
setInterval(async()=>{
 if(polling)return;polling=true;
 try{
  const targets=[...new Set([current,historyCurrent].filter(j=>j&&['planning','generating'].includes(j.status)).map(j=>j.id))];
  for(const id of targets){const j=await api('/api/jobs/'+id),data=JSON.stringify(j);
   if(current?.id===id&&data!==signature){signature=data;renderJob(j,'create');}
   if(historyCurrent?.id===id&&data!==historySignature){historySignature=data;renderJob(j,'history');}
   if(!['planning','generating'].includes(j.status))await refreshHistory();
  }
 }catch(e){notify('工作台连接中断。重新启动后可恢复已保存任务。',true);}finally{polling=false;}
},1800);
