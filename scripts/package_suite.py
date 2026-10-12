"""Make a portable local ZIP; never install or alter global skills."""
import hashlib,json,zipfile,argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args();out=Path(a.output).resolve()
if out.is_relative_to(ROOT):raise SystemExit('输出ZIP必须在套件源目录外')
if out.exists():raise SystemExit('目标已存在，请使用新文件名，避免覆盖历史包')
files=[]
for f in sorted(ROOT.rglob('*')):
 rel=f.relative_to(ROOT)
 if any(part.startswith('.') or part in {'__pycache__','node_modules'} for part in rel.parts):continue
 if f.is_symlink():raise SystemExit('拒绝打包符号链接：'+str(rel))
 if not f.is_file() or f.suffix in {'.pyc','.zip'}:continue
 if f.name in {'flowkit.env','settings.json'} or 'data' in rel.parts or 'products' in rel.parts:raise SystemExit('发现不应打包的运行数据：'+str(rel))
 files.append((f,rel.as_posix()))
out.parent.mkdir(parents=True,exist_ok=True)
manifest={'format_version':1,'name':'爆款复刻技能套件','author':'李加炜','brand':'灵凌同学','website':'https://xhs.typoai.xin','files':{n:hashlib.sha256(f.read_bytes()).hexdigest() for f,n in files}}
with zipfile.ZipFile(out,'x',zipfile.ZIP_DEFLATED) as z:
 for f,n in files:z.write(f,'remake-suite/'+n)
 z.writestr('remake-suite/FILES.json',json.dumps(manifest,ensure_ascii=False,indent=2))
print(json.dumps({'file':str(out),'file_count':len(files)+1,'bytes':out.stat().st_size,'sha256':hashlib.sha256(out.read_bytes()).hexdigest()},ensure_ascii=False))
