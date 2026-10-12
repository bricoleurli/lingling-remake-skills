import importlib.util,json,tempfile,unittest,io,zipfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('updater',ROOT/'skills/remake-suite-updater/scripts/update_suite.py');u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)
class UpdateTest(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name);self.src=self.root/'source';self.target=self.root/'skills';self.target.mkdir();self.home=u.locations(self.target);self.release('1')
 def tearDown(self):self.t.cleanup()
 def release(self,version):
  manifest={'format':1,'version':version,'skills':{}}
  for name in u.NAMES:
   p=self.src/'skills'/name;p.mkdir(parents=True,exist_ok=True);(p/'SKILL.md').write_text('version '+version)
   manifest['skills'][name]={'version':version,'files':u.hashes(p)}
  (self.src/'suite-release.json').write_text(json.dumps(manifest))
 def install(self,**kw):return u.install(self.src,self.target,self.home,**kw)
 def test_install_update_rollback_and_unrelated_data(self):
  other=self.target/'unrelated';other.mkdir();(other/'SKILL.md').write_text('custom')
  data=self.root/'workbench';data.mkdir();(data/'settings.json').write_text('private config')
  self.assertFalse(self.install()['applied']);self.assertFalse((self.target/u.NAMES[0]).exists())
  self.install(apply=True);self.assertEqual(self.install(apply=True)['changes'],[])
  self.release('2');result=self.install(apply=True);self.assertTrue(result['applied'])
  self.assertEqual((self.target/u.NAMES[0]/'SKILL.md').read_text(),'version 2')
  u.rollback(self.target,self.home)
  self.assertEqual((self.target/u.NAMES[0]/'SKILL.md').read_text(),'version 1')
  self.assertEqual((other/'SKILL.md').read_text(),'custom');self.assertEqual((data/'settings.json').read_text(),'private config')
 def test_local_edit_and_legacy_blocked(self):
  p=self.target/u.NAMES[0];p.mkdir();(p/'SKILL.md').write_text('legacy')
  with self.assertRaises(ValueError):self.install(apply=True)
  self.install(apply=True,accept=True);(p/'custom.txt').write_text('mine');self.release('2')
  with self.assertRaises(ValueError):self.install(apply=True)
  self.assertEqual((p/'custom.txt').read_text(),'mine')
 def test_corrupt_release_no_mutation(self):
  self.install(apply=True);self.release('2');(self.src/'skills'/u.NAMES[1]/'SKILL.md').write_text('tamper')
  with self.assertRaises(ValueError):self.install(apply=True)
  self.assertEqual((self.target/u.NAMES[0]/'SKILL.md').read_text(),'version 1')
 def test_failure_restores_old_install(self):
  self.install(apply=True);self.release('2');move=u.shutil.move;calls=[]
  def fail_once(*args,**kw):
   calls.append(1)
   if len(calls)==2:raise OSError('simulated failure')
   return move(*args,**kw)
  with patch.object(u.shutil,'move',side_effect=fail_once):
   with self.assertRaises(OSError):self.install(apply=True)
  for n in u.NAMES:self.assertEqual((self.target/n/'SKILL.md').read_text(),'version 1')
  self.assertEqual(u.read(self.home/'state.json')['version'],'1')
 def test_rollback_refuses_modified_files_and_backup(self):
  self.install(apply=True);self.release('2');result=self.install(apply=True)
  p=self.target/u.NAMES[0]/'SKILL.md';p.write_text('mine')
  with self.assertRaises(ValueError):u.rollback(self.target,self.home)
  p.write_text('version 2');(Path(result['backup'])/u.NAMES[0]/'SKILL.md').write_text('broken')
  with self.assertRaises(ValueError):u.rollback(self.target,self.home)
 def test_symlink_and_lock(self):
  (self.target/u.NAMES[0]).symlink_to(self.src/'skills'/u.NAMES[0],target_is_directory=True)
  with self.assertRaises(ValueError):self.install(apply=True)
  with u.locked(self.home):
   with self.assertRaises(ValueError):
    with u.locked(self.home):pass
 def test_existing_identical_install_gets_baseline(self):
  self.install(apply=True);(self.home/'state.json').unlink()
  self.install(apply=True);self.release('2');self.assertTrue(self.install(apply=True)['applied'])
 def test_download_pins_commit_and_rejects_traversal(self):
  sha='a'*40;buffer=io.BytesIO()
  with zipfile.ZipFile(buffer,'w') as z:z.writestr('repo/../outside','bad')
  with patch.object(u,'download',side_effect=[json.dumps({'sha':sha}).encode(),buffer.getvalue()]) as dl:
   with self.assertRaises(ValueError):u.fetch(self.root/'download')
   self.assertIn(sha,dl.call_args_list[1].args[0])
  self.assertFalse((self.root/'outside').exists())
 def test_real_bundle_manifest(self):u.validate(ROOT)
if __name__=='__main__':unittest.main()
