"""Regenerate the stable suite file manifest before committing a release."""
import importlib.util, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('updater', ROOT / 'skills/remake-suite-updater/scripts/update_suite.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
manifest = {'format': 1, 'version': '0.2.0', 'repository': m.REPO, 'skills': {}}
for name in m.NAMES:
    root = ROOT / 'skills' / name
    manifest['skills'][name] = {'version': json.loads((root / 'manifest.json').read_text())['version'], 'files': m.hashes(root)}
(ROOT / 'suite-release.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
print('发布清单已生成；发布前请执行测试')
