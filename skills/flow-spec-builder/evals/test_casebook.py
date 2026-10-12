#!/usr/bin/env python3
"""Synthetic regression only: no model calls and no real user acceptance."""
import copy,hashlib,importlib.util,json,os
from pathlib import Path
import shutil,subprocess,sys,tempfile,unittest,zipfile
SKILL=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SKILL/'scripts'))
import casebook as cb
import render_review as rr

class CasebookTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='复刻流程测试 ');self.root=Path(self.tmp.name)/'案例'
  cb.init(self.root,'合成测试，不是真实生成');shutil.copy2(SKILL/'examples/offline_walkthrough/flow.json',self.root/'flow.json')
  (self.root/'examples').mkdir()
  for name in ['txt_copy.txt','img_card.txt']:shutil.copy2(SKILL/'examples/offline_walkthrough'/name,self.root/'examples'/name)
  self.files=['flow.json','examples']
 def tearDown(self):self.tmp.cleanup()
 def view(self,question=False):
  d={'title':'合成测试节点','summary':'仅测试文件和页面行为，未生图。','status':'合成记录','groups':[cb.group('已记录内容',[cb.item('输入与输出','工具夹具',[cb.tab('说明','code','原样保留{{slot}}\n</script><script>alert(1)</script>')])])]}
  if question:d['question']={'text':'合成问题：是否继续？'}
  return d
 def complete(self):
  for k in cb.STAGES:
   if k=='08-delivery':continue
   cb.publish(self.root,k,self.view(),state='skipped' if k.endswith('result') else 'recorded',reason='合成测试，不运行模型' if k.endswith('result') else None)
 def test_immutable_and_latest_routing(self):
  p=cb.publish(self.root,'01-intake',self.view(True),'awaiting_user');before=p.read_bytes()
  cb.publish(self.root,'01-intake',self.view(),quote='合成测试回复（非真实用户）')
  self.assertEqual(before,p.read_bytes());index=cb.load(self.root/'view.json')
  self.assertEqual(index['routes']['01-intake'],'stages/01-intake/r2/index.html')
  self.assertIn('../../../index.html?stage=08-delivery',p.read_text())
  self.assertGreater(cb.check_links(self.root),0)
 def test_question_and_pending_not_silently_accepted(self):
  with self.assertRaises(ValueError):cb.publish(self.root,'01-intake',self.view(True),'recorded')
  self.complete();cb.publish(self.root,'06-review',self.view(True),'awaiting_user')
  with self.assertRaisesRegex(ValueError,'仍在等待'):cb.deliver(self.root,self.files)
 def test_package_portable_and_rebuild_exact(self):
  self.complete();out=cb.deliver(self.root,self.files);folder=Path(out['package'])
  self.assertEqual(cb.load(folder/'flow.json'),cb.load(self.root/'flow.json'))
  self.assertEqual((folder/'flow.html').read_text(),rr.render(cb.load(folder/'view.json')))
  for st in cb.load(folder/'flow.json')['steps']:
   self.assertIn('\n'.join(st['prompt']),(folder/'spec.md').read_text())
  self.assertNotIn('/Users/',(folder/'flow.html').read_text())
  self.assertGreater(cb.check_links(self.root),0)
  self.assertEqual(out['file_count'],len([f for f in folder.rglob('*') if f.is_file()]))
  original_zip=Path(out['zip']).read_bytes();out2=cb.deliver(self.root,self.files)
  self.assertTrue(out2['package'].endswith('v2'));self.assertEqual(Path(out['zip']).read_bytes(),original_zip)
  unpack=Path(self.tmp.name)/'另一台机器目录'
  with zipfile.ZipFile(out['zip']) as z:self.assertIsNone(z.testzip());z.extractall(unpack)
  p=unpack/'delivery';subprocess.run([sys.executable,str(p/'scripts/render_review.py'),str(p/'view.json'),'-o',str(p/'flow.html')],check=True,capture_output=True)
  self.assertEqual(cb.check_links(p),cb.check_links(folder))
 def test_missing_assets_abort_before_publish(self):
  self.complete();f=cb.load(self.root/'flow.json');f['reference']['images']=[{'file':'reference/missing.png'}];cb.save(self.root/'flow.json',f)
  with self.assertRaises(ValueError):cb.deliver(self.root,self.files)
  self.assertNotIn('08-delivery',cb.project(self.root)['stages'])
  self.assertFalse((self.root/'deliveries/v1').exists())
 def test_missing_contract_or_slots(self):
  f=cb.load(self.root/'flow.json');del f['handoff']['slot_contracts']['topic']
  with self.assertRaisesRegex(ValueError,'槽位'):cb.build_final(self.root,f)
  f=cb.load(self.root/'flow.json');del f['handoff']['step_contracts']['txt_copy']['system_prompt']
  with self.assertRaisesRegex(ValueError,'system_prompt'):cb.build_final(self.root,f)
 def test_resource_and_file_boundaries(self):
  for name in ['../outside','/etc/passwd','https://example.test/x']:
   with self.assertRaises(ValueError):cb.local(self.root,name)
  (self.root/'secret.env').write_text('fake fixture');(self.root/'link').symlink_to(self.root/'secret.env')
  with self.assertRaises(ValueError):cb.source_files(self.root,['link'])
  (self.root/'.env').write_text('fake fixture')
  with self.assertRaises(ValueError):cb.source_files(self.root,['.env'])
  d=self.view();d['links']=[{'label':'broken','href':'not-here.png'}]
  with self.assertRaises(ValueError):cb.publish(self.root,'02-analysis',d)
  self.assertNotIn('02-analysis',cb.project(self.root)['stages'])
 def test_final_extra_link_missing(self):
  self.complete();v=self.view();v['groups'][0]['items'][0]['image']='not-here.png'
  with self.assertRaises(ValueError):cb.deliver(self.root,self.files,v)
  self.assertFalse((self.root/'deliveries/v1').exists())
 def test_renderer_injection_and_route_safety(self):
  d=self.view();html=rr.render(d);self.assertNotIn('</script><script>alert(1)',html)
  d['routes']={'01-intake':'javascript:alert(1)'}
  with self.assertRaises(ValueError):rr.render(d)
  d['routes']={'01-intake':'/etc/passwd'}
  with self.assertRaises(ValueError):rr.render(d)
 def test_no_final_without_workflow_record(self):
  with self.assertRaisesRegex(ValueError,'缺少阶段'):cb.deliver(self.root,self.files)

if __name__=='__main__':unittest.main(verbosity=2)
