#!/usr/bin/env python3
"""Register a reviewed delivery using the existing workbench's own runtime."""
import argparse,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--workbench',required=True);p.add_argument('--delivery',required=True);a=p.parse_args()
app=Path(a.workbench).resolve();source=Path(a.delivery).resolve()
if not (app/'manage.py').is_file() or not (source/'flow.json').is_file():raise SystemExit('找不到工作台 manage.py 或交付 flow.json')
subprocess.run([sys.executable,str(app/'manage.py'),str(source),'--app',str(app)],check=True,cwd=app)
print('玩法已登记。刷新现有工作台查看；原设置和历史保留。')
