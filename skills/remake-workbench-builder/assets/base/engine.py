"""Local workbench. Standard library only. Product adapters are reviewed application code."""
import base64, copy, hashlib, io, json, mimetypes, re, secrets, shutil, threading, urllib.error, urllib.parse, urllib.request, uuid, zipfile
from datetime import datetime
from pathlib import Path
from card_validation import validate as validate_cards
from presentation import load as presentation

MAX=40*1024*1024
BUSY={'planning','generating'}
class Problem(ValueError): pass
def require(ok,msg):
    if not ok: raise Problem(msg)
def read(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def save(p,d):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_name(p.name+'.tmp');t.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8');t.replace(p)
def safe(root,name):
    require(isinstance(name,str) and name and not '\\' in name and not ':' in name,'路径无效')
    p=(Path(root)/name).resolve();require(p.is_relative_to(Path(root).resolve()),'路径越界');return p
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def image_ext(b):
    if b.startswith(b'\x89PNG\r\n\x1a\n'):return 'png'
    if b.startswith(b'\xff\xd8\xff'):return 'jpg'
    if b[:4]==b'RIFF' and b[8:12]==b'WEBP':return 'webp'
    raise Problem('模型没有返回 PNG / JPEG / WebP 图片')
def prompt(step):
    p=step['prompt'];return '\n'.join(p) if isinstance(p,list) else p

def check_flow(f):
    if f.get("id")=="mint_action_topic":
        from mint_adapter import check
        return check(f)
    require(f.get('spec_version')=='0.4','需要 flow.json 0.4')
    baseline=read(Path(__file__).resolve().parent/'adapter-contract.json')
    require(set(f.get('inputs',{}))<= {'texts','image'},'额外输入配置需适配')
    for actual,expected in zip(f.get('steps',[]),baseline['steps']):
        for k in set(actual)|set(expected):
            if k not in {'prompt','name','design','checks','image_size'}:
                require(actual.get(k)==expected.get(k),'步骤契约发生变化，需适配：'+str(actual.get('id'))+'.'+k)
    require(f.get('outputs')==baseline['outputs'],'交付规则发生变化，需适配')
    require(set(f.get('handoff',{}).get('layout_catalog',{}))==set(baseline['layouts']),'页型目录发生变化，需适配')
    require(re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',f.get('id','')),'玩法 ID 无效')
    st=f.get('steps',[])
    require([s.get('id') for s in st]==['txt_plan','txt_copy','img_pages'],'此适配器仅承接已审阅的知识卡流程；其他流程需由 Agent 编写适配器')
    require([s.get('type') for s in st]==['text','text','image'],'步骤类型不匹配')
    require(st[0].get('consume')=='once' and st[1].get('consume')=='once' and st[1].get('confirm') is True,'文字步骤或确认点不匹配')
    require(st[2].get('consume')=='per_item' and st[2].get('over')=='pages' and st[2].get('ref_mode')=='fixed','图片依赖需要单独适配')
    require([s.get('output',{}).get('id') for s in st]==['pages','copy','images'],'输出变量不匹配')
    require([x.get('id') for x in f['inputs']['texts']]==['topic'] and not f['inputs'].get('image',{}).get('enabled'),'输入需要单独适配')
    require(len(f.get('presets',[]))==1 and f['presets'][0]['id']=='preset_1','角色依赖需要单独适配')
    require(all(not any(k in s for k in ['when','prompt_file']) for s in st),'条件或外部提示词文件需单独适配')
    require(not st[0].get('confirm') and not st[2].get('confirm'),'新增确认点需要适配')
    for s in st:
        c=f.get('handoff',{}).get('step_contracts',{}).get(s['id'],{})
        require(not c.get('system_prompt') and not c.get('runtime_suffix'),'额外消息或后缀需要显式适配')
    return f

def expand(template,values):
    def sub(m):
        k=m[1];v=values
        for part in k.split('.'):
            require(isinstance(v,dict) and part in v,'未实现的槽位：'+k);v=v[part]
        if isinstance(v,list) and all(isinstance(x,str) for x in v):return '\n'.join(v)
        return json.dumps(v,ensure_ascii=False,indent=2) if isinstance(v,(list,dict)) else str(v)
    return re.sub(r'{{([^{}]+)}}',sub,template)

def validate_copy(c):
    require(isinstance(c,dict) and all(isinstance(c.get(k),str) and c[k].strip() for k in ['title','body']),'发布标题和正文须为非空文字')
    require(isinstance(c.get('tags'),list) and 3<=len(c['tags'])<=5 and all(isinstance(x,str) and x and '#' not in x for x in c['tags']),'标签须为3—5个不含#的文字')
    return c

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kw):raise Problem('接口发生重定向，请填写最终地址')
def valid_url(url):
    u=urllib.parse.urlsplit(url)
    require(u.scheme in ['https','http'] and u.hostname and not u.username and not u.password and not u.query and not u.fragment,'接口地址须为完整 http(s) 地址，不含密钥、参数或账号')
    require(u.scheme=='https' or u.hostname in ['localhost','127.0.0.1','::1'],'远程模型接口须使用 HTTPS')
def request(url,payload,config,ctype='application/json'):
    req=urllib.request.Request(url,data=payload,headers={'Content-Type':ctype,'Authorization':'Bearer '+config['api_key']})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=240) as r:b=r.read(MAX+1)
        require(len(b)<=MAX,'模型响应过大');return json.loads(b)
    except urllib.error.HTTPError as e:
        code=e.code;e.close();raise Problem(f'模型接口返回 HTTP {code}，请检查模型、地址和额度') from None
    except (urllib.error.URLError,TimeoutError):raise Problem('模型连接失败或超时；供应商可能已处理请求，重试前请检查') from None

def text_call(config,p):
    r=request(config['base_url'].rstrip('/')+'/chat/completions',json.dumps({'model':config['model'],'messages':[{'role':'user','content':p}]}).encode(),config)
    try:return r['choices'][0]['message']['content']
    except (KeyError,IndexError,TypeError):raise Problem('文字接口响应格式不受支持')
def parse_text(t,kind):
    require(isinstance(t,str),'文字模型返回内容为空')
    a,b=('[',']') if kind=='pages' else ('{','}')
    try:return json.loads(t[t.index(a):t.rindex(b)+1])
    except (ValueError,TypeError):raise Problem('文字模型没有返回有效 JSON')
def image_call(config,p,ref):
    fields={'model':config['model'],'prompt':p,'n':1,'size':config['size']}
    if ref is None:
        r=request(config['base_url'].rstrip('/')+'/images/generations',json.dumps(fields).encode(),config)
        return decode_image(r)
    boundary='wb'+secrets.token_hex(20);chunks=[]
    for k,v in fields.items():chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    b=Path(ref).read_bytes();ext=image_ext(b);mime='image/jpeg' if ext=='jpg' else 'image/'+ext
    chunks.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; filename="character.{ext}"\r\nContent-Type: {mime}\r\n\r\n'.encode(),b,b'\r\n',f'--{boundary}--\r\n'.encode()])
    r=request(config['base_url'].rstrip('/')+'/images/edits',b''.join(chunks),config,'multipart/form-data; boundary='+boundary)
    return decode_image(r)

def decode_image(r):
    try:
        item=r['data'][0]
        if item.get('b64_json'):data=base64.b64decode(item['b64_json'],validate=True)
        else:
            url=item['url'];u=urllib.parse.urlsplit(url)
            require(u.scheme=='https' and u.hostname and not u.username,'返回图片 URL 须为 HTTPS')
            with urllib.request.build_opener(NoRedirect()).open(url,timeout=120) as response:data=response.read(MAX+1)
        require(len(data)<=MAX,'图片过大');image_ext(data);return data
    except (KeyError,IndexError,TypeError,ValueError):raise Problem('图片返回格式无效，需要 b64_json 或 HTTPS 图片 URL')

class Store:
    def __init__(self,root,app):
        self.root=Path(root).resolve();self.app=Path(app).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();(self.root/'jobs').mkdir(exist_ok=True)
        for p in (self.root/'jobs').glob('*/job.json'):
            j=read(p)
            if j['status'] in BUSY:j.update(status='interrupted',error='工作台曾关闭，已完成的步骤已保存。');save(p,j)
    def catalog(self):
        c=read(self.app/'catalog.json') if (self.app/'catalog.json').exists() else []
        result=[]
        for x in c:
            f,p=self.product(x['key']);result.append(dict(x,inputs=f['inputs'],presentation=presentation(f,p)))
        return result
    def product(self,key):
        p=safe(self.app/'products',key);f=check_flow(read(p/'flow.json'));meta=read(p/'product.json')
        for name,sha in meta['files'].items():require(digest(safe(p,name))==sha,'玩法文件已改变，请由 Agent 新建版本后再使用')
        return f,p
    def settings(self,private=False):
        d=read(self.root/'settings.json') if (self.root/'settings.json').exists() else {}
        if private:return d
        return {k:dict({a:b for a,b in v.items() if a!='api_key'},has_key=bool(v.get('api_key'))) for k,v in d.items()}
    def set_settings(self,body):
        with self.lock:
            d=self.settings(True)
            for k in ['text','image']:
                v=body.get(k,{});old=d.get(k,{})
                if not v:continue
                valid_url(v.get('base_url',''));require(isinstance(v.get('model'),str) and v['model'].strip(),'请填写模型名称')
                api=v.get('api_key') or old.get('api_key','');require(isinstance(api,str) and api,'请填写 API 密钥')
                d[k]={'base_url':v['base_url'].rstrip('/'),'model':v['model'].strip(),'api_key':api}
                if k=='image':
                    size=v.get('size','');require(bool(re.fullmatch(r'[1-9][0-9]{2,4}x[1-9][0-9]{2,4}',size)),'填写供应商支持的近3:4图片尺寸，例如768x1024')
                    w,h=map(int,size.split('x'));require(abs(w/h-.75)<.035,'此玩法需要近3:4尺寸，不能沿用未验证的2:3占位值');d[k]['size']=size
            save(self.root/'settings.json',d)
            try:(self.root/'settings.json').chmod(0o600)
            except OSError:pass
            return self.settings()
    def configs(self,kind):
        d=self.settings(True);require(kind in d and d[kind].get('api_key'),f'请先在设置中配置{"文字" if kind=="text" else "生图"}服务');return copy.deepcopy(d[kind])
    def jobpath(self,jid):
        require(bool(re.fullmatch(r'[a-f0-9]{16}',jid)),'任务编号无效');return self.root/'jobs'/jid
    def job(self,jid):return read(self.jobpath(jid)/'job.json')
    def put(self,j):save(self.jobpath(j['id'])/'job.json',j)
    def jobs(self):
        with self.lock:
            jobs=[read(p) for p in (self.root/'jobs').glob('*/job.json')]
            return sorted([dict(id=j['id'],topic=j['topic'],name=j['name'],status=j['status'],created=j['created'],example=j.get('example',False),count=len(j.get('pages',[]))) for j in jobs],key=lambda j:j['created'],reverse=True)
    def start(self,key,values):
        f,p=self.product(key);field=f['inputs']['texts'][0];topic=values.get('topic','').strip()
        require(topic and len(topic.splitlines())==1 and len(topic)<=field['max_chars'],'请填写一行主题，最多120字')
        cfg=self.configs('text')
        with self.lock:
            require(not any(j['status'] in BUSY for j in self.jobs()),'已有任务正在执行，请等它完成或停止后再开始')
            j={'id':uuid.uuid4().hex[:16],'product':key,'name':f['name'],'topic':topic,'created':datetime.now().isoformat(timespec='seconds'),'status':'planning','stage':'txt_plan','pages':[],'copy':{},'error':'','cancel':False,'presentation':presentation(f,p),'adapter':'mint_topic_v1' if f['id']=='mint_action_topic' else 'knowledge_cards_v1','image_inputs':0 if f['id']=='mint_action_topic' else 1,'flow_sha256':digest(p/'flow.json'),'bindings':{'text':{'model':cfg['model'],'base_url':cfg['base_url']}}}
            self.put(j);self.launch(j['id'],'plan',cfg);return j
    def launch(self,jid,action,cfg,index=None):threading.Thread(target=self.work,args=(jid,action,cfg,index),daemon=True).start()
    def stopped(self,jid):
        with self.lock:return self.job(jid).get('cancel',False)
    def work(self,jid,action,cfg,index=None):
        try:
            if action=='plan':self.plan(jid,cfg)
            else:self.images(jid,cfg,index)
        except Exception as e:
            with self.lock:
                j=self.job(jid);j['status']='stopped' if j.get('cancel') else 'failed';j['error']=str(e) if isinstance(e,(Problem,ValueError,KeyError)) else '执行失败，已保留中间结果，请检查接口后重试'
                # Do not persist provider bodies or credentials in errors.
                j['error']=j['error'].replace(cfg.get('api_key','\0'),'[密钥已隐藏]');self.put(j)
    def text_step(self,jid,step,values,cfg,kind,flow):
        p=expand(prompt(step),values);folder=self.jobpath(jid)/'evidence';folder.mkdir(exist_ok=True)
        (folder/(step['id']+'.txt')).write_text(p,encoding='utf-8')
        last=None
        for n in range(3):
            if self.stopped(jid):raise Problem('已停止，尚未继续调用模型')
            # Provider/network failures are never automatically retried.
            raw=text_call(cfg,p)
            (folder/(step['id']+f'-{uuid.uuid4().hex[:8]}.response.txt')).write_text(str(raw),encoding='utf-8')
            try:
                result=parse_text(raw,kind)
                if kind=='mint':
                    from mint_adapter import validate
                    return validate(result)
                return validate_cards(result,flow) if kind=='pages' else validate_copy(result)
            except (Problem,ValueError,KeyError,TypeError) as e:last=e
        raise Problem('内容结构连续3次不合规，已停止：'+str(last))
    def plan(self,jid,cfg):
        j=self.job(jid);f,_=self.product(j['product'])
        if f['id']=='mint_action_topic':
            from mint_adapter import plan
            return plan(self,jid,cfg)
        if not j['pages']:
            pages=self.text_step(jid,f['steps'][0],{'topic':j['topic']},cfg,'pages',f)
            with self.lock:
                j=self.job(jid);j['pages']=[dict(content=p,versions=[],file=None) for p in pages];j['stage']='txt_copy';self.put(j)
        c=self.text_step(jid,f['steps'][1],{'topic':j['topic'],'pages':[p['content'] for p in j['pages']]},cfg,'copy',f)
        with self.lock:
            j=self.job(jid);j['copy']=c;self.put(j)
        self.expand_images(jid)
        with self.lock:
            j=self.job(jid);j['status']='awaiting_confirmation';j['stage']='review';j['error']='';self.put(j)
    def expand_images(self,jid):
        j=self.job(jid);f,_=self.product(j['product'])
        if f['id']=='mint_action_topic':
            from mint_adapter import expand_job
            return expand_job(self,j,f)
        validate_cards([p['content'] for p in j['pages']],f)
        folder=self.jobpath(jid)/'evidence';folder.mkdir(exist_ok=True)
        for n,page in enumerate(j['pages'],1):
            s=prompt(f['steps'][2]).replace('{{img:preset_1}}','【图1】');p=expand(s,{'item':page['content']})
            (folder/f'image-{n:02d}.txt').write_text(p,encoding='utf-8')
    def action(self,jid,action,index=None):
        with self.lock:
            j=self.job(jid);require(not j.get('example'),'历史验收案例仅供查看，请新建任务生成自己的内容')
            if action=='stop':
                require(j['status'] in BUSY,'当前没有正在执行的任务');j['cancel']=True;self.put(j);return j
            require(j['status'] not in BUSY,'任务正在执行，不能重复提交')
            require(not any(x['status'] in BUSY for x in self.jobs()),'已有任务正在执行')
            if action=='confirm':require(j['status']=='awaiting_confirmation','任务尚未到确认阶段');mode='images'
            elif action=='retry':
                require(j['status'] in ['failed','interrupted','stopped'],'当前任务无需重试');mode='plan' if j['stage'] in ['txt_plan','txt_copy'] else 'images'
            elif action=='redraw':
                require(j['status'] in ['completed','failed','stopped'] and type(index) is int and 0<=index<len(j['pages']) and j['pages'][index].get('file'),'这张图片尚未生成');mode='images'
            else:raise Problem('未知操作')
            cfg=self.configs('text' if mode=='plan' else 'image')
            if mode=='images':
                if j.get('adapter')=='mint_topic_v1':cfg['size']='1024x1536'
                self.expand_images(jid);j['stage']='images';j['bindings']['image']={k:v for k,v in cfg.items() if k!='api_key'}
            if action=='redraw':j['redraw_index']=index
            if mode=='plan':j['bindings']['text']={k:v for k,v in cfg.items() if k!='api_key'}
            j.update(status='planning' if mode=='plan' else 'generating',error='',cancel=False);self.put(j);self.launch(jid,mode,cfg,j.get('redraw_index'));return j
    def images(self,jid,cfg,index=None):
        j=self.job(jid);f,product=self.product(j['product']);ref=safe(product,f['presets'][0]['file']) if f.get('presets') else None;folder=self.jobpath(jid)
        redraw_done=False
        targets=[index] if index is not None else [i for i,p in enumerate(j['pages']) if not p.get('file')]
        for i in targets:
            if self.stopped(jid):break
            with self.lock:j=self.job(jid);j['current_page']=i+1;self.put(j)
            p=(folder/'evidence'/f'image-{i+1:02d}.txt').read_text(encoding='utf-8')
            attempt={'id':uuid.uuid4().hex[:12],'page':i+1,'model':cfg['model'],'base_url':cfg['base_url'],'size':cfg['size'],'prompt_sha256':hashlib.sha256(p.encode()).hexdigest(),'reference_sha256':digest(ref) if ref else None,'image_input_count':1 if ref else 0,'status':'submitted'}
            with self.lock:
                j=self.job(jid);j.setdefault('attempts',[]).append(attempt);self.put(j)
            try:data=image_call(cfg,p,ref)
            except Exception:
                with self.lock:
                    j=self.job(jid);j['attempts'][-1]['status']='failed_or_unknown';self.put(j)
                raise
            ext=image_ext(data);name=f'{i+1:02d}-{uuid.uuid4().hex[:8]}.{ext}';(folder/name).write_bytes(data)
            with self.lock:
                j=self.job(jid);page=j['pages'][i];page['versions'].append(name);page['file']=name;j['attempts'][-1].update(status='completed',file=name);self.put(j)
            redraw_done=True
        with self.lock:
            j=self.job(jid);j['status']='completed' if all(p.get('file') for p in j['pages']) and (index is None or redraw_done) else 'stopped';j.pop('current_page',None);j['error']=''
            if j['status']=='completed':j.pop('redraw_index',None)
            self.put(j)
    def export(self,jid):
        j=self.job(jid);require(all(p.get('file') for p in j['pages']) and j['pages'],'整套图片尚未齐全，可先下载已完成的单张图片')
        b=io.BytesIO();folder=self.jobpath(jid)
        with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
            for i,p in enumerate(j['pages'],1):z.write(safe(folder,p['file']),f'{i:02d}.{Path(p["file"]).suffix.lstrip(".")}')
            c=j['copy'];z.writestr('发布文案.txt',c.get('title','')+'\n\n'+c.get('body','')+'\n\n'+' '.join('#'+t for t in c.get('tags',[])))
            z.writestr('制作记录.json',json.dumps(j,ensure_ascii=False,indent=2))
            for p in (folder/'evidence').glob('*.txt'):
                if '.response.' not in p.name:z.write(p,'执行提示词/'+p.name)
        return b.getvalue()
