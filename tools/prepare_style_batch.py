#!/usr/bin/env python3
"""Bind verified materials to references without changing their original content."""
from __future__ import annotations
import argparse,copy,csv,hashlib,json,random,subprocess
from collections import Counter,defaultdict,deque
from datetime import datetime,timezone
from pathlib import Path
from ppt_rl.io import read_json,write_json,sha256
from ppt_rl.inference import QUERY

def matching(counts,styles,capacity,forbidden,seed):
    rng=random.Random(seed);domains=sorted(counts);nodes=['S']+domains+styles+['T'];edges={n:[] for n in nodes}
    def add(u,v,c):
        a=[v,c,None];b=[u,0,a];a[2]=b;edges[u].append(a);edges[v].append(b);return a
    for d in domains:add('S',d,counts[d])
    selected={}
    for d in rng.sample(domains,len(domains)):
        for s in rng.sample(styles,len(styles)):
            if (d,s) not in forbidden:selected[d,s]=add(d,s,1)
    for s in styles:add(s,'T',capacity)
    total=0
    while True:
        previous={'S':None};q=deque(['S'])
        while q and 'T' not in previous:
            u=q.popleft()
            for e in edges[u]:
                if e[1]>0 and e[0] not in previous:previous[e[0]]=(u,e);q.append(e[0])
        if 'T' not in previous:break
        v='T';amount=10**9
        while previous[v]:u,e=previous[v];amount=min(amount,e[1]);v=u
        v='T'
        while previous[v]:u,e=previous[v];e[1]-=amount;e[2][1]+=amount;v=u
        total+=amount
    if total!=sum(counts.values()):return None
    return {pair for pair,e in selected.items() if e[1]==0}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--sandbox-image',required=True);p.add_argument('--seed',type=int,default=20261104);a=p.parse_args()
    root=a.root.resolve();plan_path=root/'plan.json'
    if plan_path.exists():raise ValueError('Refuse to replace a frozen plan')
    materials=read_json(root/'material-packages.json');catalog=read_json(root/'frozen-reference-inputs.json');styles=sorted(catalog)
    assert len(materials)==200 and len(styles)==20
    assert Counter(v['language'] for v in materials.values())=={'en':140,'zh':60}
    assert set(Counter(v['domain'] for v in materials.values()).values())=={10}
    groups=defaultdict(list)
    for v in materials.values():groups[v['domain'],v['language']].append(v)
    domains=sorted({v['domain'] for v in materials.values()});assigned=None
    for attempt in range(100):
        zh=matching({d:len(groups[d,'zh']) for d in domains},styles,3,set(),a.seed+attempt*2)
        if zh is None:continue
        en=matching({d:len(groups[d,'en']) for d in domains},styles,7,zh,a.seed+attempt*2+1)
        if en is not None:assigned={'zh':zh,'en':en};break
    if assigned is None:raise ValueError('Cannot satisfy the requested source/reference balance')
    by_style=defaultdict(list);rng=random.Random(a.seed)
    for language,pairs in assigned.items():
        for domain in domains:
            values=sorted(groups[domain,language],key=lambda v:v['source_task_id']);rng.shuffle(values)
            targets=sorted(s for d,s in pairs if d==domain);rng.shuffle(targets)
            assert len(values)==len(targets)
            for material,style in zip(values,targets):by_style[style].append(material)
    for values in by_style.values():rng.shuffle(values)
    tasks=[];style_order=rng.sample(styles,len(styles))
    for round_index in range(10):
        for style in style_order:
            v=by_style[style][round_index];index=len(tasks)+1
            tasks.append(dict(v,sample_id=f'style200-{index:04d}',style_id=style,query=QUERY,base_seed=400000+index*1000,reference_role='style_reference_only'))
    references=copy.deepcopy(catalog)
    for ref in references.values():
        if ref['kind']=='pptx':ref['rendered_full_pdf_catalog_only']=ref['original_pdf'];ref['original_pdf']=None
    checkout=Path(__file__).resolve().parents[1];commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=checkout,text=True).strip()
    if subprocess.check_output(['git','status','--porcelain'],cwd=checkout,text=True).strip():raise ValueError('Commit the runtime before freezing collection')
    plan={'schema_version':3,'experiment_id':'ppt-style-200-20261104','created_utc':datetime.now(timezone.utc).isoformat(),'planned_attempts':200,'mode':'one_style_condition_per_distinct_material','model':'qwen3.8-flash-next-unsloth-q8_0','pi_version':'1.4.0','upstream':'http://127.0.0.1:8099','proxy_host':'172.17.0.1','proxy_base_port':19000,'code_commit':commit,'sandbox_image':a.sandbox_image,'sandbox_fonts':'fonts','max_turns':60,'sampling':{'temperature':1.0,'top_p':0.95,'top_k':20,'max_tokens':32768,'thinking_budget':8192},'tool_policy':{'bash_timeout_seconds':180,'finish_after_structural_export':True,'continue_missing_submission':True,'filesystem_scope':'per_task_container'},'input_policy':{'factual_markdown':'complete original material.md in the first user request; no truncation','reference_images':'four complete source-page PNGs in the first user request, style-only','automatic_continuation':'At most one same-session reminder when no PPTX exists after normal generation; native messages and reminder event retained.','style_source_facts':'Reference example facts are not the task content.','posthoc_rendering':'Separate from model generation; no generated-preview image feedback.'},'assignment_seed':a.seed,'material_packages_sha256':sha256(root/'material-packages.json'),'frozen_reference_inputs_sha256':sha256(root/'frozen-reference-inputs.json'),'font_manifest_sha256':sha256(root/'font-manifest.json'),'source_material_language_counts':{'en':140,'zh':60},'source_domain_counts':dict(Counter(t['domain'] for t in tasks)),'style_counts':dict(Counter(t['style_id'] for t in tasks)),'source_image_or_pdf_count':sum(t['has_source_image_or_pdf'] for t in tasks),'output_language':'English','replacements_allowed':False,'qualified_dataset_upload':False,'references':references,'tasks':tasks}
    assert len({t['source_task_id'] for t in tasks})==200
    assert len({(t['domain'],t['style_id']) for t in tasks})==200
    for s in styles:assert Counter(t['language'] for t in tasks if t['style_id']==s)=={'en':7,'zh':3}
    write_json(plan_path,plan);plan_path.chmod(0o444)
    with (root/'dataset.jsonl').open('w') as f:
        for t in tasks:f.write(json.dumps(t,ensure_ascii=False)+'\n')
    fields=['sample_id','source_task_id','title','domain','language','style_id','query','materials_dir','image_count','pdf_page_count']
    with (root/'tasks.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(tasks)
    summary={'tasks':200,'styles':20,'domains':20,'language_counts':{'en':140,'zh':60},'per_style':{'tasks':10,'english':7,'chinese':3,'distinct_domains':10},'source_image_or_pdf_count':plan['source_image_or_pdf_count'],'all_queries_english':True,'original_fact_text_changed':False,'plan_sha256':sha256(plan_path),'code_commit':commit,'frozen_reference_inputs_sha256':plan['frozen_reference_inputs_sha256'],'qualified_dataset_upload':False}
    write_json(root/'task-composition-summary.json',summary);print(json.dumps(summary))

if __name__=='__main__':main()
