from pathlib import Path
import json,re,sys
ROOT=Path(__file__).resolve().parents[1]
def validate(pages,flow):
 if not isinstance(pages,list) or not 6<=len(pages)<=14:raise ValueError('页数必须为6—14')
 for i,p in enumerate(pages,1):
  if p['index']!=i:raise ValueError('index必须从1连续')
  expected='cover' if i==1 else 'summary' if i==len(pages) else 'detail'
  if p['role']!=expected:raise ValueError(f'第{i}页role顺序不符')
  if p['template_id'] not in flow['handoff']['layout_catalog']:raise ValueError('未知模板')
  if expected!='detail' and p['template_id']!=expected:raise ValueError('封面/总结模板不符')
  if expected=='detail' and p['template_id'] in ['cover','summary']:raise ValueError('正文模板不符')
  for k in ['title','intro','footer']:
   if not isinstance(p[k],str):raise ValueError(k+'必须为字符串')
  if not p['title']:raise ValueError('标题不能为空')
  if not isinstance(p['sections'],list):raise ValueError('sections必须为列表')
  wanted=[p['title']]+([p['intro']] if p['intro'] else [])
  for s in p['sections']:
   if not isinstance(s['heading'],str) or not isinstance(s['lines'],list) or not s['lines'] or any(not isinstance(v,str) or not v for v in s['lines']):raise ValueError('section结构错误')
   wanted+=([s['heading']] if s['heading'] else [])+s['lines']
  wanted+=([p['footer']] if p['footer'] else [])
  if wanted!=p['display_text']:raise ValueError(f'第{i}页display_text与结构不一致')
  t=p['template_id'];ns=len(p['sections']);counts=[len(s['lines']) for s in p['sections']]
  if t=='cover' and (counts!=[3,1] or p['footer']!=p['sections'][1]['lines'][0]):raise ValueError('封面需3小标签+1主标签与对应footer')
  if t=='summary' and (counts!=[1,1,1] or not p['footer']):raise ValueError('总结需3要点+下一步')
  if t in ['action','routine','cautions','dense','principle'] and ns!=3:raise ValueError(t+'必须3个区')
  if t=='compare' and counts!=[3,2]:raise ValueError('compare需3/2条')
  if t=='dense' and counts[1:]!=[4,3]:raise ValueError('dense需4项清单与3种方法')
  if t=='principle' and counts[1:]!=[6,3]:raise ValueError('principle需两列各3条与负向3条')
 return pages

def main():
 flow=json.loads((ROOT/'flow.json').read_text());pages=validate(json.loads(Path(sys.argv[1]).read_text()),flow);out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
 template='\n'.join(next(s for s in flow['steps'] if s['id']=='img_pages')['prompt']);jobs=[]
 for p in pages:
  def sub(m):
   key=m[1]
   if key=='img:preset_1':return '【图1】'
   if key=='item':return json.dumps(p,ensure_ascii=False,indent=2)
   if key.startswith('item.'):
    v=p[key[5:]];return '\n'.join(v) if isinstance(v,list) else str(v)
   raise ValueError('未识别槽位'+key)
  prompt=re.sub(r'\{\{([^}]+)\}\}',sub,template);name=f'{p["index"]:02d}.txt';(out/name).write_text(prompt)
  jobs.append({'index':p['index'],'prompt_file':name,'reference_images':['presets/character.png'],'image_input_count':1,'status':'offline_example_not_executed'})
 (out/'jobs.json').write_text(json.dumps(jobs,ensure_ascii=False,indent=2));print(f'离线校验与展开完成：{len(jobs)}页；未调用模型。')
if __name__=='__main__':
 try:main()
 except (ValueError,KeyError,TypeError) as e:print('停止：'+str(e),file=sys.stderr);sys.exit(1)
