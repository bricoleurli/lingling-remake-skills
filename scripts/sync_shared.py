from pathlib import Path
import argparse,shutil
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');args=p.parse_args()
for skill in sorted((ROOT/'skills').iterdir()):
 if not (skill/'SKILL.md').exists():continue
 for name in ['model-access.md','AUTHORS.md','model-access-status.example.json']:
  dst=skill/'references'/name;src=ROOT/'shared'/name
  if args.check:
   if not dst.exists() or dst.read_bytes()!=src.read_bytes():raise SystemExit('共同规则不同步：'+str(dst))
  else:dst.parent.mkdir(exist_ok=True);shutil.copy2(src,dst)
print('共同规则一致' if args.check else '共同规则已同步')
