import copy,json,sys,tempfile,unittest,base64
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import Store,Problem,read,image_call
from manage import add
from mint_adapter import validate,pages_and_prompts
SOURCE=Path(__file__).parent/'fixtures/mint'
PNG=b'\x89PNG\r\n\x1a\nmock'
def fixture():
 return {'headline':['整理书桌','做好这8件事'],'lines':[f'{i}｜整理第{i}区｜拿起物品放到指定区域｜方便寻找｜先做一小步' for i in range(1,9)],'cover_previews':['整理桌面｜留出空间','归放工具｜方便取用','清洁区域｜保持整齐'],'related':['收纳工具','桌面清洁','物品归位','整理线缆','保持习惯','减少杂物'],'copy':{'title':'整理书桌的8件事','body':'从小区域开始整理，依次归放物品并留出工作空间。'*7,'tags':['整理','书桌','生活']}}
class MintTest(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();app=Path(self.t.name)/'app';app.mkdir();self.key=add(app,SOURCE);self.s=Store(Path(self.t.name)/'data',app);self.f=read(SOURCE/'flow.json');self.cfg={'base_url':'https://example.invalid/v1','model':'test','api_key':'test-key','size':'768x1024'};self.s.configs=lambda kind:copy.deepcopy(self.cfg);self.s.launch=lambda jid,action,cfg,index=None:self.s.work(jid,action,cfg,index)
 def tearDown(self):self.t.cleanup()
 def start(self):
  with patch('engine.text_call',return_value=json.dumps(fixture(),ensure_ascii=False)) as text:
   j=self.s.start(self.key,{'topic':'整理书桌'});self.assertEqual(text.call_count,1)
  j=self.s.job(j['id']);self.assertEqual(j['status'],'awaiting_confirmation',j.get('error'));return j
 def test_confirm_six_images_and_recovery(self):
  j=self.start();self.assertEqual(len(j['pages']),6)
  with patch('engine.image_call',side_effect=[PNG,Problem('模拟中断')]):self.s.action(j['id'],'confirm')
  j=self.s.job(j['id']);self.assertEqual(j['status'],'failed');first=j['pages'][0]['file']
  with patch('engine.image_call',return_value=PNG) as image:
   self.s.action(j['id'],'retry');self.assertEqual(image.call_count,5);self.assertTrue(all(c.args[2] is None and c.args[0]['size']=='1024x1536' for c in image.call_args_list))
  j=self.s.job(j['id']);self.assertEqual(j['status'],'completed');self.assertEqual(j['pages'][0]['file'],first);self.assertTrue(all(a['image_input_count']==0 for a in j['attempts']))
  with patch('engine.image_call',return_value=PNG):self.s.action(j['id'],'redraw',0)
  self.assertEqual(len(self.s.job(j['id'])['pages'][0]['versions']),2);self.assertTrue(self.s.export(j['id']))
 def test_grouping_and_validation(self):
  pages,ps=pages_and_prompts(self.f,fixture());self.assertEqual(len(ps),6)
  for i in range(4):self.assertIn(f'上方模块：{i*2+1}｜',ps[i+1]);self.assertIn(f'下方模块：{i*2+2}｜',ps[i+1])
  self.assertTrue(all('{{' not in p for p in ps));self.assertNotIn('@活力少女',ps[-1]);d=fixture();d['lines'].pop()
  with self.assertRaises(Problem):validate(d)
  d=fixture();d['lines'][1]=d['lines'][0]
  with self.assertRaises(Problem):validate(d)
 def test_generation_endpoint_no_reference(self):
  with patch('engine.request',return_value={'data':[{'b64_json':base64.b64encode(PNG).decode()}]}) as call:self.assertEqual(image_call(self.cfg,'prompt',None),PNG)
  self.assertTrue(call.call_args.args[0].endswith('/images/generations'));self.assertNotIn('image',json.loads(call.call_args.args[1]))
if __name__=='__main__':unittest.main()
