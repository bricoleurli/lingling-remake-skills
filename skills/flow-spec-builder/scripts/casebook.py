#!/usr/bin/env python3
"""Offline case records, immutable stage HTML, and portable delivery packages.
No model calls, network requests, installation, or automatic user approvals.
"""
import argparse
import copy
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from urllib.parse import unquote, urlsplit
import zipfile
from render_review import render, validate

SKILL = Path(__file__).resolve().parents[1]
STAGES = dict(zip(
    ['01-intake','02-analysis','03-plan','03-result','04-reuse','05-spec','06-review','07-plan','07-result','08-delivery'],
    ['接收资料','分析参考图文','原内容还原·方案','原内容还原·结果','确认复用方式','编写完整流程','审阅完整流程','补充测试·方案','补充测试·结果','定稿交付']))
NAV = ['01-intake','02-analysis','03-plan','04-reuse','05-spec','06-review','07-plan','08-delivery']
STATES = {'awaiting_user':'等待你的回复','recorded':'已记录','skipped':'已说明跳过','blocked':'待解决'}

def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def local(root, value):
    """File allowlist paths: relative, within root, no symlink components."""
    if not isinstance(value,str) or not value or '\\' in value or any(ord(c)<32 for c in value):
        raise ValueError('无效本地路径：'+str(value))
    p = PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts or urlsplit(value).scheme:
        raise ValueError('路径必须相对案例目录，不能越界：'+value)
    cur=root
    for part in p.parts:
        cur=cur/part
        if cur.is_symlink(): raise ValueError('不打包符号链接：'+value)
    result=(root/value).resolve()
    if not result.is_relative_to(root.resolve()): raise ValueError('路径越界：'+value)
    return result

def resource_slots(data):
    if data.get('home'): yield data, 'home'
    for key in ['navigation','subnavigation','links']:
        for n in data.get(key,[]):
            if n.get('href'):yield n,'href'
    for key in data.get('routes',{}):yield data['routes'],key
    for g in data.get('groups',[]):
        for i in g['items']:
            for key in ['href','image']:
                if i.get(key):yield i,key
            for t in i.get('tabs',[]):
                if t['type']=='images':
                    for im in t['content']:yield im,'src'

def rebase(data, source, target, boundary):
    data=copy.deepcopy(data)
    for obj,key in resource_slots(data):
        u=urlsplit(obj[key]);raw=unquote(u.path)
        if u.scheme or u.netloc or raw.startswith('/') or '\\' in raw:
            raise ValueError('资源必须为本地相对路径：'+obj[key])
        path=(source/raw).resolve()
        if not path.is_relative_to(boundary.resolve()):raise ValueError('资源超出案例目录：'+obj[key])
        obj[key]=os.path.relpath(path,target)+('?' + u.query if u.query else '')+('#'+u.fragment if u.fragment else '')
    return data

def write_view(base,data):
    base.mkdir(parents=True,exist_ok=True)
    save(base/'view.json',data)
    (base/'index.html').write_text(render(data),encoding='utf-8')

def project(root):
    return load(root/'case.json')

def refresh(root):
    p=project(root);items=[];routes={}
    for stage in STAGES:
        history=p['stages'].get(stage,[])
        if not history:continue
        last=history[-1];href=last['path'];routes[stage]=href
        items.append({'title':STAGES[stage], 'description':last['summary'], 'caption':last['status'],
                      'number':stage[:2], 'href':href, 'action':'打开阶段', 'tabs':[]})
    # The same fixed index routes old stage navigation to the latest available revision.
    for stage in NAV:
        if stage not in routes:
            related=[k for k in routes if k[:2]==stage[:2]]
            if related:routes[stage]=routes[related[-1]]
    data={'title':p['title'],'stage_label':'完整流程审阅','status':'已保存'+str(len(items))+'个节点',
          'summary':'从这里查看最新进度。每一步单独保留版本；保存新内容后刷新此页。',
          'home':'index.html','routes':routes,
          'navigation':[{'label':k[:2].lstrip('0')+' '+STAGES[k], **({'href':routes[k]} if k in routes else {})} for k in NAV],
          'groups':[{'title':'各阶段产出','items':items}],
          'footer':'这是审阅页面；请在对话中回复。页面不提交决定或调用模型。'}
    write_view(root,data)

def init(root,title):
    root=root.resolve()
    if (root/'case.json').exists():raise ValueError('案例已存在，请继续使用；不要重复初始化。')
    if (root/'index.html').exists() or (root/'view.json').exists():raise ValueError('目录已有其他入口，请选择新案例目录，不能覆盖。')
    root.mkdir(parents=True,exist_ok=True)
    save(root/'case.json',{'schema_version':1,'title':title,'skill_version':(SKILL/'VERSION').read_text().strip(),'stages':{}})
    refresh(root)
    return root/'index.html'

def publish(root,stage,view,state='recorded',quote=None,reason=None):
    root=root.resolve()
    p=project(root);view=copy.deepcopy(view)
    if stage not in STAGES:raise ValueError('未知节点')
    if not view.get('summary') or not view.get('status') or not view.get('groups'):
        raise ValueError('阶段页面必须有summary、status和实际groups内容')
    if state=='awaiting_user' and not view.get('question',{}).get('text'):
        raise ValueError('等待回复时必须明确写question.text')
    if state in ('skipped','blocked') and not reason:raise ValueError('跳过/受阻需写明原因')
    if state=='recorded' and view.get('question') and not quote:
        # A question can describe a historical node, but must not look unanswered.
        raise ValueError('页面仍有问题：请标awaiting_user；已回复请附真实quote或移除当前问题')
    history=p['stages'].setdefault(stage,[]);revision=len(history)+1
    dest=root/'stages'/stage/f'r{revision}'
    if dest.exists():raise ValueError('阶段版本路径已占用，保留现场，检查case.json记录')
    # Source view resource paths are ALWAYS relative to case root, not the draft JSON file.
    view=rebase(view,root,dest,root)
    view['home']='../../../index.html';view['stage_label']='第'+stage[:2].lstrip('0')+'步 · '+STAGES[stage]
    view['navigation']=[{'label':k[:2].lstrip('0')+' '+STAGES[k], 'href':'../../../index.html?stage='+k,
                         'current':k[:2]==stage[:2]} for k in NAV]
    view['subnavigation']=[{'label':STAGES[k],'href':'../../../index.html?stage='+k,'current':k==stage}
                           for k in STAGES if k[:2]==stage[:2] and (k in p['stages'] or k==stage)]
    if history:view.setdefault('links',[]).append({'label':'上一版记录','href':'../r'+str(revision-1)+'/index.html'})
    if quote:
        view['question']={'title':'已收到的用户回复','text':quote,'hint':'仅记录实际回复；认可范围以本页说明为准。'}
    validate(view)
    # Resolve the stage's content links before writing anything; future nav uses existing index.
    for obj,key in resource_slots(view):
        u=urlsplit(obj[key]);path=(dest/unquote(u.path)).resolve()
        if not path.exists():raise ValueError('阶段引用文件不存在：'+obj[key])
    write_view(dest,view)
    history.append({'revision':revision,'path':str((dest/'index.html').relative_to(root)),
                    'status':view['status'],'summary':view['summary'],'state':state,
                    'user_quote':quote,'reason':reason})
    save(root/'case.json',p);refresh(root)
    return dest/'index.html'

class Links(HTMLParser):
    def __init__(self):super().__init__();self.urls=[];self.capture=False;self.payload=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        for key in ('href','src'):
            if a.get(key):self.urls.append(a[key])
        if tag=='script' and a.get('id')=='review-data':self.capture=True
    def handle_endtag(self,tag):
        if tag=='script':self.capture=False
    def handle_data(self,data):
        if self.capture:self.payload.append(data)

def check_links(root):
    root=root.resolve()
    errors=[];count=0
    for page in root.rglob('*.html'):
        parser=Links();text=page.read_text(encoding='utf-8');parser.feed(text)
        payload=''.join(parser.payload)
        if payload and payload!='__REVIEW_DATA__':
            try:
                for obj,key in resource_slots(json.loads(payload)):parser.urls.append(obj[key])
            except (ValueError,TypeError,KeyError) as e:errors.append(str(page)+': '+str(e))
        for url in parser.urls:
            if url.startswith('#'):continue
            u=urlsplit(url);count+=1
            if u.scheme or u.netloc or u.path.startswith('/') or '\\' in unquote(u.path):
                errors.append(str(page.relative_to(root))+': 非本地链接 '+url);continue
            dest=(page.parent/unquote(u.path)).resolve()
            if not dest.is_relative_to(root.resolve()) or not dest.exists():errors.append(str(page.relative_to(root))+': 缺失或越界 '+url)
    if errors:raise ValueError('\n'.join(errors))
    return count

def tab(label,kind,content):return {'label':label,'type':kind,'content':content}
def item(title,description,tabs):return {'title':title,'description':description,'tabs':tabs}
def group(title,items):return {'title':title,'items':items}

def build_final(root,flow,extra=None):
    h=flow.get('handoff',{})
    for field in ['input_contract','page_rules','model_bindings','step_contracts','script_contract','verification']:
        if field not in h:raise ValueError('定稿缺少handoff.'+field)
    groups=[group('以后怎样使用这套流程',[
        item('输入、固定项与可变项','这一套流程的用户输入契约。',[tab('输入说明','facts',h['input_contract'])]),
        item('页数、调用与模型','计划调用与实际测试分别记录。',[tab('数量规则','facts',h['page_rules']),tab('模型与执行器','facts',h['model_bindings'])])])]
    stepitems=[]
    for s in flow.get('steps',[]):
        c=h['step_contracts'].get(s['id'])
        if not c:raise ValueError('缺少步骤交接信息：'+s['id'])
        for key in ['executor','model','input_sources','output','downstream','calls','image_inputs','status','retry','message_role','system_prompt','runtime_suffix']:
            if key not in c:raise ValueError(s['id']+'缺少'+key)
        prompt=s.get('prompt')
        if prompt is None and s.get('prompt_file'):prompt=local(root,s['prompt_file']).read_text(encoding='utf-8')
        if isinstance(prompt,list):prompt='\n'.join(prompt)
        if not isinstance(prompt,str):raise ValueError('步骤缺少完整提示词：'+s['id'])
        names=list(dict.fromkeys(re.findall(r'\{\{([^{}]+)\}\}',prompt)))
        slots=[]
        for name in names:
            info=h.get('slot_contracts',{}).get(name)
            if not isinstance(info,dict) or not info.get('source') or not info.get('description'):
                raise ValueError('需要补充handoff.slot_contracts槽位来源与含义：'+name)
            slots.append({'name':name,'value':info['source'],'description':info['description']})
        tabs=[tab('完整提示词','code',prompt),tab('槽位来源','slots',slots),tab('输入输出与执行','facts',c),
              tab('完整步骤定义','code',json.dumps(s,ensure_ascii=False,indent=2))]
        if c.get('system_prompt'):tabs.append(tab('自定义系统提示词','code',c['system_prompt']))
        if c.get('runtime_suffix'):tabs.append(tab('运行时后缀','code',c['runtime_suffix']))
        for key,label in [('example_prompt_file','展开提示词实例'),('example_output_file','输出实例')]:
            if c.get(key):tabs.append(tab(label,'code',local(root,c[key]).read_text(encoding='utf-8')))
        if not c.get('example_prompt_file') and not c.get('example_note'):
            raise ValueError(s['id']+'须提供example_prompt_file或example_note说明为何没有实例')
        stepitems.append(item(s.get('name',s['id']),str(c['status']),tabs))
    if not stepitems:raise ValueError('没有实际流程步骤')
    groups.append(group('完整流程与提示词',stepitems))
    reference=flow.get('reference',{})
    if reference:
        rt=[tab('来源与观察','facts',reference)]
        images=[{'src':i['file'],'label':i.get('note','参考图')} for i in reference.get('images',[]) if i.get('file')]
        if images:rt.append(tab('参考图片','images',images))
        groups.append(group('参考来源',[item('参考图文与分析依据','按用户提供的原顺序保存。',rt)]))
    script_tabs=[tab('作用与异常处理','facts',h['script_contract'])]
    for name in h['script_contract'].get('files',[]):script_tabs.append(tab(name,'code',local(root,name).read_text(encoding='utf-8')))
    groups.append(group('脚本、验证与交接',[
        item('确定处理脚本','脚本是否实现、由谁调用，见说明。',script_tabs),
        item('实际验证范围','认可范围、未验证项、版本变化和已知偏差。',[tab('真实记录','facts',h['verification'])])]))
    if extra:
        validate(extra);groups+=copy.deepcopy(extra.get('groups',[]))
    groups.append(group('机器可读的完整源',[
        item('flow.json','供其他工具读取；包含完整步骤与交接元数据。',[tab('完整源文件','code',json.dumps(flow,ensure_ascii=False,indent=2))])]))
    return {'title':flow.get('name','复刻流程')+' · 定稿交付','stage_label':'第8步 · 定稿交付',
            'status':'流程已整理交付','summary':flow.get('summary','完整流程与必要附件。'),
            'provenance':'以flow.json与所附测试证据为依据。交付完成不代表所有步骤实测通过，验证范围见下。',
            'home':'flow.html','groups':groups,'links':[{'label':'结构化源文件','href':'flow.json'},
             {'label':'完整文字规格','href':'spec.md'},{'label':'全部文件清单','href':'FILES.md'}],
            'footer':'离线审阅：抽屉查看详情、复制全文、黄色标记槽位；请连同附件搬运。'}

def markdown(view):
    lines=['# '+view['title'],'',view['summary'],'',view['provenance'],'']
    for g in view['groups']:
        lines+=['## '+g['title'],'']
        for i in g['items']:
            lines+=['### '+i['title'],'',i.get('description',''),'']
            if i.get('href'):lines+=['[打开文件]('+i['href']+')','']
            for t in i.get('tabs',[]):
                lines+=['#### '+t['label'],''];v=t['content']
                if t['type']=='text':lines+=[v,'']
                elif t['type']=='images':lines+=['\n'.join('- ['+im.get('label','图片')+']('+im['src']+')' for im in v),'']
                else:
                    text=v if isinstance(v,str) else json.dumps(v,ensure_ascii=False,indent=2)
                    # Longer than any content fence, so prompts remain intact.
                    fence='`'*max(3,1+max([len(x) for x in re.findall(r'`+',text)]+[0]))
                    lines+=[fence,text,fence,'']
    return '\n'.join(lines)

def source_files(root,names):
    root=root.resolve()
    result={}
    for name in names:
        p=local(root,name)
        if not p.exists():raise ValueError('交付文件不存在：'+name)
        found=[p] if p.is_file() else list(p.rglob('*'))
        for f in found:
            rel=f.relative_to(root).as_posix();f=local(root,rel)
            if not f.is_file():continue
            if f.name.startswith('.env') or f.suffix in ('.pem','.key','.pyc') or any(x in f.parts for x in ['.git','__pycache__']):
                raise ValueError('不打包环境密钥/缓存：'+rel)
            if rel in ('flow.html','spec.md','view.json','FILES.md','files.json','build_delivery.py') or rel.startswith(('deliveries/','assets/review-shell.html','scripts/render_review.py')):
                raise ValueError('交付清单包含生成文件或保留路径：'+rel)
            result[rel]=f
    return result

def deliver(root,names,extra=None):
    root=root.resolve()
    p=project(root)
    missing=[k for k in ['01-intake','02-analysis','03-result','04-reuse','06-review'] if k not in p['stages']]
    if missing:raise ValueError('缺少阶段记录（跳过也需写明原因）：'+', '.join(missing))
    if not isinstance(names,list) or any(not isinstance(n,str) for n in names):raise ValueError('files清单必须是路径字符串列表')
    if not names or 'flow.json' not in names:raise ValueError('files清单须显式包含flow.json')
    selected=source_files(root,names)
    # Do not silently skip an unanswered gate. Exempt an older final delivery record.
    pending=[k for k,v in p['stages'].items() if k!='08-delivery' and v[-1]['state'] in ('awaiting_user','blocked')]
    if pending:raise ValueError('这些节点仍在等待/受阻，请先如实更新记录：'+', '.join(pending))
    flow=load(root/'flow.json');view=build_final(root,flow,extra)
    deliveries=root/'deliveries';deliveries.mkdir(exist_ok=True)
    version=1
    while (deliveries/f'v{version}').exists() or (deliveries/f'v{version}.zip').exists():version+=1
    dest=deliveries/f'v{version}';zipdest=deliveries/f'v{version}.zip'
    with tempfile.TemporaryDirectory(prefix='.delivery-',dir=root) as tmp:
        work=Path(tmp)/'package';work.mkdir()
        for name,src in selected.items():
            out=work/name;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,out)
        # Inputs referenced by flow, step prompts and examples must be included in the allowlist.
        refs=[i['file'] for i in flow.get('reference',{}).get('images',[]) if i.get('file')]+[i['file'] for i in flow.get('presets',[])]
        refs+=[s['prompt_file'] for s in flow['steps'] if s.get('prompt_file')]
        for c in flow['handoff']['step_contracts'].values():refs += [c[k] for k in ('example_prompt_file','example_output_file') if c.get(k)]
        refs += flow['handoff']['script_contract'].get('files',[])
        for name in refs:
            if not local(work,name).is_file():raise ValueError('交付清单漏掉所需文件：'+name)
        for name in ['scripts/render_review.py','assets/review-shell.html']:
            out=work/name;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(SKILL/name,out)
        shutil.copy2(SKILL/'LICENSE',work/'LICENSE.flow-spec-builder')
        save(work/'view.json',view);(work/'flow.html').write_text(render(view),encoding='utf-8')
        (work/'spec.md').write_text(markdown(view),encoding='utf-8')
        names_out=sorted([f.relative_to(work).as_posix() for f in work.rglob('*') if f.is_file()]+['FILES.md','files.json'])
        (work/'FILES.md').write_text('# 文件清单\n\n共'+str(len(names_out))+'个文件，含3个核心文件；其余为附件、证据及重建页面所需资源。\n\n'+ '\n'.join('- ['+n+']('+n+')' for n in names_out)+'\n',encoding='utf-8')
        save(work/'files.json',{'count':len(names_out),'checksum_scope':'除本清单自身外的全部文件',
             'files':[{'path':n,'sha256':sha(work/n)} for n in names_out if n!='files.json']})
        check_links(work)
        ztmp=Path(tmp)/'delivery.zip'
        with zipfile.ZipFile(ztmp,'w',zipfile.ZIP_DEFLATED) as z:
            for n in names_out:z.write(work/n,'delivery/'+n)
        shutil.move(str(work),dest);shutil.move(str(ztmp),zipdest)
    # Stage 8 contains the same content, now linked to the frozen deliverable.
    stageview=rebase(view,dest,root,root)
    stageview['links'].insert(0,{'label':'下载完整流程包 ZIP','href':zipdest.relative_to(root).as_posix()})
    stageview['links'].insert(1,{'label':'打开可搬运的最终流程页','href':(dest/'flow.html').relative_to(root).as_posix()})
    path=publish(root,'08-delivery',stageview)
    return {'entry':str(root/'index.html'),'stage':str(path),'package':str(dest),'zip':str(zipdest),'file_count':len(names_out)}

def main():
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='command',required=True)
    a=sub.add_parser('init');a.add_argument('root',type=Path);a.add_argument('--title',required=True)
    a=sub.add_parser('publish');a.add_argument('root',type=Path);a.add_argument('--stage',choices=STAGES,required=True);a.add_argument('--view',type=Path,required=True);a.add_argument('--state',choices=STATES,default='recorded');a.add_argument('--quote');a.add_argument('--reason')
    a=sub.add_parser('deliver');a.add_argument('root',type=Path);a.add_argument('--files',type=Path,required=True,help='JSON string list of explicitly included paths relative to root');a.add_argument('--extra-view',type=Path)
    a=sub.add_parser('check');a.add_argument('root',type=Path)
    args=ap.parse_args();root=args.root.resolve()
    if args.command=='init':result=init(root,args.title)
    elif args.command=='publish':result=publish(root,args.stage,load(args.view),args.state,args.quote,args.reason)
    elif args.command=='deliver':result=deliver(root,load(args.files),load(args.extra_view) if args.extra_view else None)
    else:result={'local_links_checked':check_links(root)}
    print(json.dumps(result,ensure_ascii=False,indent=2) if isinstance(result,dict) else result)

if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,TypeError) as e:raise SystemExit('停止：'+str(e))
