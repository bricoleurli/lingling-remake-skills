import json,subprocess,sys,tempfile,unittest
from pathlib import Path
SKILL=Path(__file__).resolve().parents[1]
class InstallTest(unittest.TestCase):
 def run_script(self,name,*args,ok=True):
  p=subprocess.run([sys.executable,str(SKILL/'scripts'/name),*map(str,args)],capture_output=True,text=True)
  self.assertEqual(p.returncode==0,ok,p.stderr);return p
 def test_fresh_then_append_and_repeat_preserves_data(self):
  with tempfile.TemporaryDirectory() as temp:
   app=Path(temp)/'workbench';self.run_script('create_workbench.py','--target',app,'--delivery',SKILL/'examples/knowledge-cards')
   (app/'data').mkdir();(app/'data/keep.txt').write_text('history sentinel')
   for _ in range(2):self.run_script('add_playbook.py','--workbench',app,'--delivery',SKILL/'examples/mint-topic')
   catalog=json.loads((app/'catalog.json').read_text());self.assertEqual(len(catalog),2);self.assertEqual((app/'data/keep.txt').read_text(),'history sentinel')
   self.run_script('create_workbench.py','--target',app,'--delivery',SKILL/'examples/mint-topic',ok=False)
   p=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-q'],cwd=app,capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr)
   html=(app/'static/index.html').read_text()
   for label in ['生成工作台','玩法库','生成历史','设置']:self.assertIn(label,html)
   self.assertFalse((app/'data/settings.json').exists())
 def test_reject_bad_delivery_without_partial_target(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);source=root/'source';source.mkdir();(source/'flow.json').write_text('{"id":"unsupported"}');app=root/'workbench'
   self.run_script('create_workbench.py','--target',app,'--delivery',source,ok=False)
   self.assertFalse(app.exists());self.assertEqual(list(root.glob('.workbench-build-*')),[])
if __name__=='__main__':unittest.main()
