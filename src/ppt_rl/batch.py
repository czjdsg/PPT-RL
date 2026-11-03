"""Reproducible multi-draw CLI driver with shared text alignment only."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from collections import Counter
import math,os,re,subprocess,sys,time
from .io import read_json,write_json,sha256
from .tournament import configure,prepare_decks
from . import engine as E
from .metrics import compare_rankings

def aggregate_runs(records):
    complete=[r for r in records if r['status']=='complete'];ids=sorted(x['participant'] for x in complete[0]['ranking']) if complete else []
    rows=[]
    for pid in ids:
        obs=[next(x for x in r['ranking'] if x['participant']==pid) for r in complete];v=[x['reward'] for x in obs];mu=sum(v)/len(v)
        rows.append({'participant':pid,'observations':len(v),'reward_mean':mu,'reward_population_std':math.sqrt(sum((x-mu)**2 for x in v)/len(v)),
            'reward_min':min(v),'reward_max':max(v),'championships':sum(x['rank_min']==1 for x in obs),'top_two_finishes':sum(x['rank_min']<=2 for x in obs),
            'tier_counts':dict(Counter(str(x['rank_min']) if x['rank_min']==x['rank_max'] else f"{x['rank_min']}-{x['rank_max']}" for x in obs))})
    pairs=[{'a':a['id'],'b':b['id'],**compare_rankings(a['ranking'],b['ranking'])} for a,b in combinations(complete,2)]
    return {'status':'complete' if len(complete)==len(records) else 'in_progress','completed_draws':len(complete),'total_draws':len(records),'participants':rows,'pairwise':pairs,'runs':records}

def run_plan(manifest,plan_file,out,settings=None,parallel_draws=5,global_limit=8):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True);settings=dict(settings or {});plan=read_json(plan_file)
    draws=plan.get('draws')
    if not draws:raise ValueError('Plan needs a draws list with id and eight slots')
    if len({d['id'] for d in draws})!=len(draws) or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}',d['id']) for d in draws):raise ValueError('Use unique simple draw IDs')
    shared=out/'shared';configure(shared,settings);selection=prepare_decks(manifest,shared);players={p['id']:p for p in selection['participants']}
    for d in draws:
        if sorted(d['slots'])!=sorted(players):raise ValueError('Plan and deck IDs differ')
    cache=E.PairCache(players)
    with ThreadPoolExecutor(max_workers=settings.get('concurrency',3)) as pool:
        pairs=list(pool.map(lambda pair:cache.get(*pair),combinations(sorted(players),2)))
    entries=[]
    for pair in pairs:
        a,b=pair['canonical_a'],pair['canonical_b'];source=pair['alignment_source']
        entries.append({'a':a,'b':b,'directory':source['directory'],'result_file':source['result_file'],'result_sha256':source['result_sha256'],
            'pptx_sha256_a':players[a]['pptx_sha256'],'pptx_sha256_b':players[b]['pptx_sha256']})
    def one(draw):
        root=out/'runs'/draw['id'];root.mkdir(parents=True,exist_ok=True)
        write_json(root/'alignment-cache-index.json',{'entries':entries})
        config={**settings,'global_gate':str(out/'leases'),'global_limit':global_limit};cfg=root/'judge.json';write_json(cfg,config)
        command=[sys.executable,'-m','ppt_rl','rank','--manifest',str(shared/'selection.json'),'--out',str(root),'--config',str(cfg),'--slots',*draw['slots']]
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
        with (root/'controller.log').open('a') as log:
            completed=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT)
        state=read_json(root/'status.json') if (root/'status.json').exists() else {'status':'failed','ranking':[]}
        return {'id':draw['id'],'slots':draw['slots'],'status':state['status'],'ranking':state.get('ranking',[]),'exit_code':completed.returncode,'root':str(root)}
    with ThreadPoolExecutor(max_workers=parallel_draws) as pool:records=list(pool.map(one,draws))
    # All workers have exited before the final snapshot, avoiding a poll/snapshot race.
    result=aggregate_runs(records);write_json(out/'summary.json',result)
    if result['status']!='complete':raise RuntimeError('Some draws are incomplete; inspect their preserved controller.log and failures')
    return result
