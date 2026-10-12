"""Reviewed topic-to-six-page mint adapter; no imported scripts are executed."""
import re
from pathlib import Path

def check(f):
    from engine import read,require,prompt
    expected=read(Path(__file__).with_name('mint-contract.json'))
    actual={k:f.get(k) for k in expected}
    actual['steps']=[{k:v for k,v in s.items() if k not in ['prompt','name','design','checks']} for s in f.get('steps',[])]
    require(actual==expected,'青绿玩法契约发生变化，需要适配新版本')
    require(all(isinstance(prompt(s),str) and prompt(s).strip() for s in f['steps']),'提示词为空')
    return f

def validate(data):
    from engine import require,validate_copy
    require(isinstance(data,dict) and set(data)=={'headline','lines','cover_previews','related','copy'},'整套方案字段不完整')
    for key,count,limit in [('headline',2,20),('lines',8,180),('cover_previews',3,45),('related',6,18)]:
        rows=data[key];require(isinstance(rows,list) and len(rows)==count,key+'数量不符')
        require(all(isinstance(x,str) and x.strip() and len(x)<=limit and '\n' not in x and '\r' not in x for x in rows),key+'内容为空、过长或含换行')
    for i,line in enumerate(data['lines'],1):
        parts=line.split('｜');require(len(parts)==5 and parts[0]==str(i),'正文应按1—8编号，并包含5个字段')
        require(all(parts[k].strip() and len(parts[k])<=n for k,n in [(1,16),(2,70),(3,45)]) and len(parts[4])<=30,'正文超出页内容量或缺少操作说明')
    require(all(len(x.split('｜'))==2 and all(y.strip() for y in x.split('｜')) for x in data['cover_previews']),'封面预览须为标题与摘要')
    validate_copy(data['copy']);require(len(data['copy']['title'])<=20 and 150<=len(data['copy']['body'])<=300,'发布标题或正文长度不符')
    return data

def pages_and_prompts(f,data):
    from engine import prompt,require
    d=validate(data);fixed=f['fixed'];values={**d,**fixed,'lines.all':'\n'.join(d['lines'])}
    def fill(step,group=None):
        def sub(m):
            key=m[1]
            if key.startswith('group['):return group[int(key[6:-1])-1]
            if key.startswith('headline['):return d['headline'][int(key[9:-1])-1]
            require(key in values,'未知青绿提示词槽位：'+key)
            value=values[key];return '\n'.join(value) if isinstance(value,list) else str(value)
        return re.sub(r'{{([^{}]+)}}',sub,prompt(step))
    rows=[];prompts=[]
    def add(title,text,step,group=None):
        rows.append({'content':{'index':len(rows)+1,'title':title,'display_text':text},'file':None,'versions':[]});prompts.append(fill(step,group))
    add(' · '.join(d['headline']),d['headline']+d['cover_previews'],f['steps'][1])
    for i in range(0,8,2):
        group=d['lines'][i:i+2];add(' / '.join(x.split('｜')[1] for x in group),group,f['steps'][2],group)
    add('收尾与相关主题',fixed['outro']+d['related']+([fixed['account']] if fixed['account'] else []),f['steps'][3])
    return rows,prompts

def expand_job(store,j,f):
    rows,prompts=pages_and_prompts(f,j['mint_plan'])
    from engine import require
    require([x['content'] for x in rows]==[x['content'] for x in j['pages']],'已确认内容与方案不一致')
    folder=store.jobpath(j['id'])/'evidence';folder.mkdir(exist_ok=True)
    for i,p in enumerate(prompts,1):(folder/f'image-{i:02d}.txt').write_text(p,encoding='utf-8')

def plan(store,jid,cfg):
    from engine import expand,prompt
    j=store.job(jid);f,_=store.product(j['product'])
    d=store.text_step(jid,f['steps'][0],{'topic':j['topic']},cfg,'mint',f)
    rows,_=pages_and_prompts(f,d)
    with store.lock:
        j=store.job(jid);j.update(mint_plan=d,pages=rows,copy=d['copy']);store.put(j)
    expand_job(store,j,f)
    with store.lock:
        j=store.job(jid);j.update(status='awaiting_confirmation',stage='review',error='');store.put(j)
