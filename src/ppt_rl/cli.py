from __future__ import annotations
from pathlib import Path
import argparse,getpass,json,os
from .io import read_json,write_json

def main():
    p=argparse.ArgumentParser(description='PPT-RL: infer -> text alignment -> disjoint groups -> screenshot duels -> reward tiers')
    sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('infer');q.add_argument('--materials',type=Path,required=True);q.add_argument('--out',type=Path,required=True);q.add_argument('--config',type=Path);q.add_argument('--node');q.add_argument('--query');q.add_argument('--style-template',type=Path);q.add_argument('--style-pdf',type=Path);q.add_argument('--style-manifest',type=Path);q.add_argument('--sandbox-image');q.add_argument('--inline-content',action='store_true');q.add_argument('--reference-images',type=Path);q.add_argument('--style-source-pdf',type=Path)
    q=sub.add_parser('extract');q.add_argument('pptx',type=Path);q.add_argument('--out',type=Path,required=True)
    q=sub.add_parser('align');q.add_argument('--a',type=Path,required=True);q.add_argument('--b',type=Path,required=True);q.add_argument('--out',type=Path,required=True);q.add_argument('--endpoint',default='http://127.0.0.1:8099');q.add_argument('--model',default='qwen3.8-flash-next-unsloth-q8_0')
    for name in ['duel','rank']:
        q=sub.add_parser(name);q.add_argument('--manifest',type=Path,required=True);q.add_argument('--out',type=Path,required=True);q.add_argument('--config',type=Path)
        if name=='rank':q.add_argument('--seed',type=int,default=20261103);q.add_argument('--slots',nargs=8)
    q=sub.add_parser('plan');q.add_argument('--ids',nargs=8,required=True);q.add_argument('--fixed',type=Path);q.add_argument('--out',type=Path,required=True)
    q=sub.add_parser('compare');q.add_argument('--a',type=Path,required=True);q.add_argument('--b',type=Path,required=True);q.add_argument('--out',type=Path,required=True)
    q=sub.add_parser('run-plan');q.add_argument('--manifest',type=Path,required=True);q.add_argument('--plan',type=Path,required=True);q.add_argument('--out',type=Path,required=True);q.add_argument('--config',type=Path);q.add_argument('--parallel-draws',type=int,default=5);q.add_argument('--global-limit',type=int,default=8)
    q=sub.add_parser('prepare-local');q.add_argument('--root',type=Path,required=True);q.add_argument('--out',type=Path);q.add_argument('--skip-hashes',action='store_true')
    q=sub.add_parser('fetch-train');q.add_argument('--out',type=Path,required=True);q.add_argument('--repo',default='yzxjb/ppt-sft');q.add_argument('--revision');q.add_argument('--prefix',default='qualified/20261101-v1');q.add_argument('--ask-token',action='store_true')
    a=p.parse_args()
    if a.command=='infer':
        from .inference import run_inference,QUERY
        c=read_json(a.config) if a.config else {}
        r=run_inference(a.materials,a.out,model=c.get('model','qwen3.8-flash-next-unsloth-q8_0'),endpoint=c.get('endpoint','http://127.0.0.1:8099'),sampling=c.get('sampling'),query=a.query or c.get('query',QUERY),max_turns=c.get('max_turns',80),node=a.node,style_template=a.style_template,style_pdf=a.style_pdf,style_manifest=a.style_manifest,sandbox_image=a.sandbox_image or c.get('sandbox_image'),tool_policy=c.get('tool_policy'),sandbox_fonts=c.get('sandbox_fonts'),inline_content=a.inline_content or c.get('inline_content',False),reference_images=read_json(a.reference_images) if a.reference_images else c.get('reference_images'),style_source_pdf=a.style_source_pdf,inline_content_max_characters=c.get('inline_content_max_characters',60000))
        print(json.dumps({k:r.get(k) for k in ['success','agent_state','pages','pptx_path','elapsed_seconds','error']},ensure_ascii=False))
        if not r['success']:raise SystemExit(1)
    elif a.command=='extract':
        from .matching import extract_pptx
        write_json(a.out,extract_pptx(a.pptx))
    elif a.command=='align':
        from .matching import FlashPageMatcher,extract_pptx,normalize_pages
        def pages(path):return normalize_pages(extract_pptx(path)['pages'] if path.suffix.lower()=='.pptx' else read_json(path))
        result=FlashPageMatcher(endpoint=a.endpoint,model=a.model,timeout=1800,max_repairs=2).align(pages(a.a),pages(a.b),a.out)
        print(json.dumps({'groups':len(result['groups']),'unmatched_a':len(result['unmatched_a']),'unmatched_b':len(result['unmatched_b'])}))
    elif a.command in ['duel','rank']:
        from .tournament import execute,duel
        c=read_json(a.config) if a.config else {}
        r=duel(a.manifest,a.out,c) if a.command=='duel' else execute(a.manifest,a.out,c,slots=a.slots,draw_seed=None if a.slots else a.seed)
        print(json.dumps({'status':r['status'],'winner':r.get('winner'),'ranking':r.get('ranking')},ensure_ascii=False))
    elif a.command=='plan':
        from .plans import complete_plan
        fixed=read_json(a.fixed) if a.fixed else []
        slots=complete_plan(a.ids,fixed)
        write_json(a.out,{'ids':a.ids,'draws':[{'id':f'draw{i:02d}','draw_seed':None,'slots':x} for i,x in enumerate(slots,1)],'first_round_unique_pairs':28,'scope':'One complete disjoint first-round coverage, not all 105 partitions or all bracket placements.'})
    elif a.command=='compare':
        from .metrics import compare_rankings
        write_json(a.out,compare_rankings(read_json(a.a),read_json(a.b)))
    elif a.command=='run-plan':
        from .batch import run_plan
        r=run_plan(a.manifest,a.plan,a.out,read_json(a.config) if a.config else {},a.parallel_draws,a.global_limit)
        print(json.dumps({'status':r['status'],'completed_draws':r['completed_draws']}))
    elif a.command=='prepare-local':
        from .data import prepare_local
        print(json.dumps(prepare_local(a.root,a.out,not a.skip_hashes)))
    else:
        from .data import fetch_qualified
        token=getpass.getpass('HF token (hidden): ') if a.ask_token else os.environ.get('HF_TOKEN')
        print(json.dumps(fetch_qualified(a.out,a.repo,a.revision,a.prefix,token)))

if __name__=='__main__':main()
