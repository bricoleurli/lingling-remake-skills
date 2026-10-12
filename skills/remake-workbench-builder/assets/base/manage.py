"""Agent-facing product installation; never execute scripts from imported deliveries."""
import argparse, hashlib, json, shutil, uuid
from pathlib import Path
from presentation import load as presentation
from engine import check_flow, read, save, digest, safe, validate_cards, validate_copy

def add(app,source):
    app=Path(app);source=Path(source).resolve();f=check_flow(read(source/'flow.json'))
    presentation(f,source)
    files=['flow.json']+[p['file'] for p in f['presets']]
    if (source/'workbench.json').exists():files.append('workbench.json')
    hashes={n:digest(safe(source,n)) for n in files}
    version=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()[:12]
    key=f['id']+'/'+version;dst=app/'products'/key
    # Validate sample data without executing any source scripts.
    if (source/'examples/pages.json').exists():validate_cards(read(source/'examples/pages.json'),f)
    if not dst.exists():
        dst.mkdir(parents=True)
        for n in files:
            q=safe(dst,n);q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(safe(source,n),q)
        save(dst/'product.json',{'adapter':'mint_topic_v1' if f['id']=='mint_action_topic' else 'knowledge_cards_v1','source':str(source),'files':hashes})
    catalog=read(app/'catalog.json') if (app/'catalog.json').exists() else []
    catalog=[x for x in catalog if x['id']!=f['id']]
    catalog.append({'id':f['id'],'key':key,'name':f['name'],'version':version,'description':f.get('summary','固定角色 · 手绘知识卡 · 6—14页自动分页'),'cover':f['presets'][0]['file'] if f['presets'] else None,'adapter':'mint_topic_v1' if f['id']=='mint_action_topic' else 'knowledge_cards_v1'})
    save(app/'catalog.json',catalog);return key

def sample(app,data,source,key):
    source=Path(source);test=source/'tests/spending-v1';f,_=check_flow(read(source/'flow.json')),None
    pages=validate_cards(read(test/'pages.json'),f);c=validate_copy(read(test/'copy.json'))
    data=Path(data);jid=hashlib.sha256(('accepted:'+key).encode()).hexdigest()[:16];folder=data/'jobs'/jid
    if (folder/'job.json').exists():return jid
    folder.mkdir(parents=True,exist_ok=True);(folder/'evidence').mkdir(exist_ok=True)
    rows=[]
    for i,p in enumerate(pages,1):
        name=f'{i:02d}.png';shutil.copy2(test/'raw'/name,folder/name)
        shutil.copy2(test/'prompts'/f'{i:02d}.txt',folder/'evidence'/f'image-{i:02d}.txt')
        rows.append({'content':p,'file':name,'versions':[name]})
    save(folder/'job.json',{'id':jid,'product':key,'name':f['name'],'topic':read(test/'input.json')['topic'],'created':'2026-10-09T00:00:00','status':'completed','stage':'images','pages':rows,'copy':c,'example':True,'error':'','flow_sha256':digest(source/'flow.json'),'bindings':{},'provenance':'此前由 Codex 配合内置生图完成，用户已认可7张整体效果；不是本工作台外部API新生成。'})
    return jid

def main():
    p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('--app',default=str(Path(__file__).resolve().parent));p.add_argument('--sample-data');a=p.parse_args()
    key=add(a.app,a.source);print('已登记玩法：'+key)
    if a.sample_data:print('已导入已验收案例：'+sample(a.app,a.sample_data,a.source,key))
if __name__=='__main__':main()
