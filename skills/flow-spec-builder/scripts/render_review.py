#!/usr/bin/env python3
"""Render a self-contained, offline review HTML from a stage view JSON.
Default: never replace an existing stage with different content. --draft is
only for an unpublished UI draft. This does not execute any model or change flow.json.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

SHELL = Path(__file__).resolve().parents[1] / 'assets' / 'review-shell.html'

def validate(data):
    if not isinstance(data, dict) or not isinstance(data.get('title'), str):
        raise ValueError('需要字符串 title')
    for group in data.get('groups', []):
        if not isinstance(group.get('title'), str) or not isinstance(group.get('items'), list):
            raise ValueError('每个 group 需要 title 与 items')
        for item in group['items']:
            if not isinstance(item.get('title'), str):
                raise ValueError('每项需要 title')
            for tab in item.get('tabs', []):
                if tab.get('type') not in ('text','code','facts','slots','images') or not isinstance(tab.get('label'), str):
                    raise ValueError('无效详情标签页')
                content=tab.get('content')
                if tab['type'] in ('text','code') and not isinstance(content,str):raise ValueError('text/code内容必须为字符串')
                if tab['type']=='facts' and not isinstance(content,dict):raise ValueError('facts内容必须为对象')
                if tab['type'] in ('slots','images') and not isinstance(content,list):raise ValueError('slots/images内容必须为列表')
                if tab['type']=='slots':
                    for slot in content:
                        if not isinstance(slot,dict) or not isinstance(slot.get('name'),str) or 'value' not in slot:raise ValueError('每个槽位需要name和value')
                if tab['type']=='images':
                    for image in content:
                        if not isinstance(image,dict) or not isinstance(image.get('src'),str):raise ValueError('每张图片需要src')
    # Validate only actual resource fields. Text inside facts is not a URL.
    urls=[data.get('home')]+list(data.get('routes',{}).values())
    for key in ('navigation','subnavigation','links'):
        urls.extend(n.get('href') for n in data.get(key, []))
    for group in data.get('groups', []):
        for item in group['items']:
            urls.extend([item.get('image'),item.get('href')])
            for tab in item.get('tabs', []):
                if tab['type']=='images':urls.extend(im.get('src') for im in tab['content'])
    for x in urls:
        if x is None:continue
        if not isinstance(x,str):raise ValueError('资源链接必须为字符串')
        parsed=urlsplit(x)
        if parsed.scheme or parsed.netloc or x.startswith('/') or chr(92) in x or any(ord(c)<32 for c in x):
            raise ValueError('资源链接必须是普通本地相对路径：'+x)

def render(data):
    validate(data)
    payload=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    shell=SHELL.read_text(encoding='utf-8')
    if shell.count('__REVIEW_DATA__')!=1:raise ValueError('模板占位符必须恰好1个')
    digest=hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return '<!-- stage-data-sha256: '+digest+' -->\n'+shell.replace('__REVIEW_DATA__',payload)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('view_json',type=Path);ap.add_argument('-o','--output',required=True,type=Path)
    ap.add_argument('--draft',action='store_true',help='仅允许覆盖尚未交付的界面草稿，不用于历史阶段快照')
    args=ap.parse_args()
    data=json.loads(args.view_json.read_text(encoding='utf-8'));content=render(data)
    if args.output.resolve()==args.view_json.resolve():raise ValueError('输出不能覆盖源数据')
    if args.output.exists() and args.output.read_text(encoding='utf-8')!=content and not args.draft:
        raise ValueError('阶段快照已存在；请另存新版本，不覆盖历史')
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(content,encoding='utf-8')
    print(args.output)

if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,TypeError) as e:
        print('未生成：'+str(e),file=sys.stderr);sys.exit(1)
