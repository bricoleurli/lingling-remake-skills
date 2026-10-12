"""Data-only playbook presentation; source files never supply executable UI."""
from pathlib import Path
import json

def load(flow,folder):
    path=Path(folder)/'workbench.json'
    if not path.exists():
        adapter='mint_topic_v1' if flow['id']=='mint_action_topic' else 'knowledge_cards_v1'
        path=Path(__file__).parent/'ui-profiles'/f'{adapter}.json'
    data=json.loads(path.read_text(encoding='utf-8'));validate(data,flow);return data

def validate(data,flow):
    from engine import require
    fields={'result','asset_title','asset_note','asset_summary','intro','calls','reference_note','confirmation','stages'}
    require(isinstance(data,dict) and set(data)==fields,'玩法展示字段不完整或含未知字段')
    require(all(isinstance(data[k],str) and 0<len(data[k])<=1000 for k in fields-{'stages'}),'玩法展示文字无效')
    steps={s['id'] for s in flow['steps']}
    require(isinstance(data['stages'],list) and 1<=len(data['stages'])<=30,'流程展示步骤数量无效')
    for stage in data['stages']:
        require(isinstance(stage,dict) and set(stage)=={'title','actor','description','prompt_step'},'流程展示结构无效')
        require(all(isinstance(stage[k],str) and 0<len(stage[k])<=1000 for k in ['title','actor','description']),'流程展示内容无效')
        require(stage['prompt_step'] is None or stage['prompt_step'] in steps,'展示引用了不存在的提示词步骤')
    return data
