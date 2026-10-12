#!/usr/bin/env python3
"""Create a reviewed workbench atomically, never replace an existing target."""
import argparse,shutil,subprocess,sys,tempfile
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--target',required=True);p.add_argument('--delivery',required=True);p.add_argument('--accepted-example',action='store_true');a=p.parse_args()
    source=Path(a.delivery).resolve();target=Path(a.target).resolve();skill=Path(__file__).resolve().parents[1];base=skill/'assets/base'
    if target.exists():raise SystemExit('目标已存在。请用add_playbook.py追加玩法，不覆盖整个目录。')
    if not (source/'flow.json').is_file():raise SystemExit('交付目录缺少flow.json')
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.workbench-build-',dir=target.parent) as temp:
        stage=Path(temp)/'workbench';shutil.copytree(base,stage,ignore=shutil.ignore_patterns('__pycache__','*.pyc','data','products','catalog.json'))
        shutil.copy2(skill/'DESIGN.md',stage/'DESIGN.md');(stage/'assets').mkdir(exist_ok=True)
        for shot in (skill/'assets').glob('design-*.jpg'):shutil.copy2(shot,stage/'assets'/shot.name)
        command=[sys.executable,str(stage/'manage.py'),str(source),'--app',str(stage)]
        if a.accepted_example:command+=['--sample-data',str(stage/'data')]
        subprocess.run(command,check=True,cwd=stage)
        if target.exists():raise SystemExit('目标在构建期间出现，已停止，未覆盖。')
        stage.rename(target)
    print('工作台已生成：'+str(target));print('启动：'+sys.executable+' '+str(target/'server.py'))
if __name__=='__main__':main()
