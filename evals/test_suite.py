from pathlib import Path
import unittest,json
ROOT=Path(__file__).resolve().parents[1]
class SuiteTest(unittest.TestCase):
 def test_shared_files_and_entry_references(self):
  for skill in (ROOT/'skills').iterdir():
   if not (skill/'SKILL.md').exists():continue
   text=(skill/'SKILL.md').read_text()
   self.assertIn('references/model-access.md',text)
   for n in ['model-access.md','AUTHORS.md']:self.assertEqual((ROOT/'shared'/n).read_bytes(),(skill/'references'/n).read_bytes())
   meta=json.loads((skill/'manifest.json').read_text());self.assertEqual(meta['author'],'李加炜');self.assertEqual(meta['brand'],'灵凌同学')
 def test_pinned_first_scripts_unchanged(self):
  import hashlib
  hashes=json.loads((ROOT/'evals/confirmed-source-hashes.json').read_text())
  for name,digest in hashes.items():self.assertEqual(hashlib.sha256((ROOT/'skills/flow-spec-builder'/name).read_bytes()).hexdigest(),digest)
 def test_routes_point_to_real_skills(self):
  for n in ['flow-spec-builder','remake-workbench-builder']:self.assertTrue((ROOT/'skills'/n/'SKILL.md').is_file())
  text=(ROOT/'skills/remake-studio/references/routing.md').read_text();self.assertIn('一次性作图',text);self.assertIn('只授权第一阶段',text)
if __name__=='__main__':unittest.main()
