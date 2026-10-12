import copy,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import read,save,Problem,Store
from presentation import load,validate
from manage import add
FIX=Path(__file__).parent/'fixtures/mint'
class PresentationTest(unittest.TestCase):
 def test_invalid_prompt_reference_rejected(self):
  f=read(FIX/'flow.json');ui=load(f,FIX);ui['stages'][0]['prompt_step']='missing'
  with self.assertRaises(Problem):validate(ui,f)
 def test_ui_is_versioned_and_old_job_contract_retained(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);src=root/'source';src.mkdir();app=root/'app';app.mkdir();f=read(FIX/'flow.json');save(src/'flow.json',f);ui=load(f,FIX);save(src/'workbench.json',ui)
   first=add(app,src);self.assertEqual(first,add(app,src));ui['result']='测试版新说明';save(src/'workbench.json',ui);second=add(app,src);self.assertNotEqual(first,second)
   self.assertNotEqual(read(app/'products'/first/'workbench.json')['result'],ui['result']);store=Store(root/'data',app);self.assertEqual(store.catalog()[0]['presentation']['result'],ui['result'])
   save(app/'products'/second/'workbench.json',dict(ui,result='篡改'))
   with self.assertRaises(Problem):store.product(second)
if __name__=='__main__':unittest.main()
