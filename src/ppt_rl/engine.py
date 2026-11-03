#!/usr/bin/env python3
"""Two real judges, the same eight-player draw, fourteen matches without reset."""
from concurrent.futures import Future,ThreadPoolExecutor,wait,FIRST_COMPLETED
from pathlib import Path
import getpass,hashlib,json,random,shutil,threading,time,os
import requests

from .matching import DEFAULT_MODEL,SYSTEM_PROMPT,FlashPageMatcher,normalize_pages,validate_alignment,sha,now
from .aesthetics import FlashAestheticJudge,AESTHETIC_PROMPT
from .remote_judge import Judge,BASE
from .bracket import BRACKET,ready_matches,group_vote,standings,reward_conflicts

ROOT=Path('runs/tournament').resolve()
MODELS=[DEFAULT_MODEL];SEED=20261103
SETTINGS={}
FLASH_LIMIT=threading.BoundedSemaphore(3)
EVENT_LOCK=threading.Lock()


def load(p):return json.loads(Path(p).read_text())
def atomic(p,value):
    p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name('.'+p.name+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');tmp.replace(p)
def event(kind,**details):
    with EVENT_LOCK:
        with (ROOT/'events.jsonl').open('a') as stream:stream.write(json.dumps({'created_utc':now(),'event':kind,**details},ensure_ascii=False)+'\n')
    if kind in ['alignment_ready','match_complete','tie_waiting','tournament_complete']:
        print(json.dumps({'event':kind,**details},ensure_ascii=False),flush=True)
def key_for(a,b):return '__'.join(sorted([a,b]))
def seeded(label):return random.Random(int(hashlib.sha256((str(SEED)+'/'+label).encode()).hexdigest(),16))


class PairCache:
    def __init__(self,participants):
        self.players=participants;self.lock=threading.Lock();self.futures={}
        self.old={key_for(e['a'],e['b']):e for e in load(ROOT/'alignment-cache-index.json')['entries']} if (ROOT/'alignment-cache-index.json').exists() else {}
    def get(self,left,right):
        key=key_for(left,right)
        with self.lock:
            owner=key not in self.futures
            if owner:self.futures[key]=Future()
            future=self.futures[key]
        if owner:
            try:future.set_result(self.prepare(key))
            except Exception as exc:future.set_exception(exc)
        return future.result()
    def prepare(self,key):
        a,b=key.split('__');directory=ROOT/'pairs'/key;directory.mkdir(parents=True,exist_ok=True)
        pages={s:normalize_pages(load(self.players[p]['extracted_file'])['pages']) for s,p in [('a',a),('b',b)]}
        pointer=directory/'alignment-source.json'
        if pointer.exists():
            source=load(pointer);result=load(Path(source['result_file']))
        elif key in self.old:
            old=self.old[key];assert old['a']==a and old['b']==b
            assert old['pptx_sha256_a']==self.players[a]['pptx_sha256'] and old['pptx_sha256_b']==self.players[b]['pptx_sha256']
            result=load(Path(old['result_file']))
            source={'status':'complete','kind':'historical','pair_key':key,'canonical_a':a,'canonical_b':b,
                    'directory':old['directory'],'result_file':old['result_file'],'result_sha256':sha(Path(old['result_file'])),
                    'cache_note':'Only the verified Flash text alignment is reused. No historical aesthetic outcome is reused.','created_utc':now()}
        else:
            history=[];result=None
            for number in range(1,3):
                run=directory/'alignment-runs'/f'run-{number:02d}';run.mkdir(parents=True,exist_ok=True)
                for side,pid in [('a',a),('b',b)]:shutil.copyfile(self.players[pid]['extracted_file'],run/f'extracted-{side}.json')
                if (run/'result.json').exists():result=load(run/'result.json')
                elif list(run.glob('attempt-*.request.json')):history.append({'directory':str(run),'status':'preserved_partial'});continue
                else:
                    try:
                        event('alignment_job_started',pair=key,run=number)
                        with FLASH_LIMIT:result=FlashPageMatcher(endpoint=SETTINGS.get('match_endpoint',SETTINGS.get('endpoint','http://127.0.0.1:8099')),model=SETTINGS.get('match_model',DEFAULT_MODEL),timeout=1800,max_repairs=2).align(pages['a'],pages['b'],run)
                    except Exception as exc:
                        failure={'directory':str(run),'status':'failed','error':str(exc),'created_utc':now()}
                        atomic(run/'run-failure.json',failure);history.append(failure);continue
                history.append({'directory':str(run),'status':'complete'});break
            if result is None:raise RuntimeError('Flash alignment failed: '+key)
            source={'status':'complete','kind':'fresh','pair_key':key,'canonical_a':a,'canonical_b':b,
                    'directory':str(run),'result_file':str(run/'result.json'),'result_sha256':sha(run/'result.json'),'runs':history,'created_utc':now()}
        assert not validate_alignment(result,pages['a'],pages['b'])
        assert result['groups'],'There are no comparable page groups'
        for side,pid in [('a',a),('b',b)]:
            used=[p for g in result['groups'] for p in g[side+'_pages']];unmatched=[p['page'] for p in result['unmatched_'+side]]
            assert len(used)==len(set(used)) and not set(used)&set(unmatched)
            assert set(used)|set(unmatched)==set(range(1,self.players[pid]['page_count']+1))
        for request in Path(source['directory']).glob('attempt-*.request.json'):
            body=load(request);assert body['system']==SYSTEM_PROMPT
            assert all(isinstance(m['content'],str) for m in body['messages'])
        atomic(pointer,source);atomic(directory/'groups.json',result['groups'])
        event('alignment_ready',pair=key,cache_kind=source['kind'],groups=len(result['groups']))
        return {'pair_key':key,'canonical_a':a,'canonical_b':b,'alignment_source':source,'result':result}


def image_records(players,pair,group,side):
    pid=pair['canonical_'+side];lookup={p['page']:p for p in players[pid]['images']}
    return [{**lookup[n],'page_key':side.upper()+'-'+str(n),'source_side':side.upper(),'source_page':n,
             'source_deck':pid,'participant_id':pid,'physical_key':pid+':'+str(n)} for n in group[side+'_pages']]


def evaluate_group(players,pair,group,model,key,out,orientation):
    a,b=image_records(players,pair,group,'a'),image_records(players,pair,group,'b');history=[]
    for number in range(1,3):
        run=out/'runs'/f'run-{number:02d}'
        if list(run.glob('attempt-*.request.json')) and not (run/'result.json').exists():history.append({'directory':str(run),'status':'preserved_partial'});continue
        try:
            event('evaluation_job_started',model=model,job=str(out.relative_to(ROOT)),orientation=orientation)
            if SETTINGS.get('protocol','anthropic')=='anthropic':
                with FLASH_LIMIT:result=FlashAestheticJudge(endpoint=SETTINGS.get('endpoint','http://127.0.0.1:8099'),model=model,temperature=SETTINGS.get('temperature',.2),seed=SETTINGS.get('generation_seed',20261102),thinking_budget=1024,max_tokens=4096,timeout=1800).compare(a,b,run,orientation)
            else:result=Judge(model,key,base_url=SETTINGS.get('endpoint')).compare(a,b,run,orientation)
        except Exception as exc:
            failure={'directory':str(run),'status':'failed','error':str(exc).replace(key,'[REDACTED]'),'created_utc':now()}
            atomic(run/'run-failure.json',failure);history.append(failure);continue
        history.append({'directory':str(run),'status':'complete'})
        atomic(out/'active-run.json',{'status':'complete','directory':str(run),'result_file':str(run/'result.json'),'runs':history})
        shutil.copyfile(run/'result.json',out/'result.json')
        event('evaluation_job_complete',model=model,job=str(out.relative_to(ROOT)),winner=result['winner'])
        return {'group_id':group['id'],'artifact_dir':str(run),'result_file':str(run/'result.json'),'winner':result['winner'],'orientation':orientation}
    raise RuntimeError('Both evaluation runs failed: '+str(out))


def manual_decision(model,match,record):
    path=ROOT/'manual-decisions.json'
    record['status']='awaiting_manual_decision';atomic(match/'match.json',record)
    event('tie_waiting',model=model,match=record['id'],reason='manual_decision_required')
    while True:
        if path.exists():
            value=load(path).get(model+'::'+record['id'])
            if value in [record['canonical_a'],record['canonical_b']]:return value
        time.sleep(3)


def decide_tie(players,pair,model,key,record,directory,pool):
    policy_file=ROOT/'tie-policy.json'
    record['status']='awaiting_tie_policy';atomic(directory/'match.json',record)
    event('tie_waiting',model=model,match=record['id'],wins_a=record['primary_wins_a'],wins_b=record['primary_wins_b'])
    while not policy_file.exists():time.sleep(3)
    policy=load(policy_file)['policy'];record['tie_policy']=policy
    groups=pair['result']['groups'];opposite='BA' if record['orientation']=='AB' else 'AB'
    if policy=='random_group_swap':
        order=groups[:];seeded(pair['pair_key']+'/overtime').shuffle(order)
        for number,group in enumerate(order[:3],1):
            item=pool.submit(evaluate_group,players,pair,group,model,key,directory/'tiebreaks'/f'group-{number:02d}',opposite).result()
            item['policy']=policy;record['tiebreaks'].append(item)
            if item['winner']!='tie':return pair['canonical_'+item['winner'].lower()],'random_group_overtime'
    elif policy=='full_round_swap':
        wa,wb=record['primary_wins_a'],record['primary_wins_b']
        for number in range(1,3):
            jobs=[pool.submit(evaluate_group,players,pair,g,model,key,directory/'tiebreaks'/f'round-{number:02d}'/g['id'],opposite if number==1 else record['orientation']) for g in groups]
            results=[job.result() for job in jobs]
            for item in results:item['policy']=policy;record['tiebreaks'].append(item)
            vote=group_vote([{'winner':r['winner']} for r in results]);wa+=vote['wins_a'];wb+=vote['wins_b']
            if wa!=wb:return pair['canonical_a'] if wa>wb else pair['canonical_b'],'replayed_round_group_wins'
    elif policy!='manual':raise ValueError('Unknown tie policy: '+policy)
    return manual_decision(model,directory,record),'manual_advancement'


def run_match(players,cache,model,key,match,occurrence,pool):
    directory=ROOT/'tournaments'/model/'matches'/match['id'];path=directory/'match.json'
    if path.exists() and load(path).get('status')=='complete':return load(path)
    pair=cache.get(match['left_player'],match['right_player']);orientation='AB' if seeded(pair['pair_key']+'/orientation').randrange(2)==0 else 'BA'
    if occurrence%2:orientation='BA' if orientation=='AB' else 'AB'
    record={**match,'status':'judging','model':model,'pair_key':pair['pair_key'],'canonical_a':pair['canonical_a'],'canonical_b':pair['canonical_b'],
            'orientation':orientation,'pair_occurrence':occurrence+1,'started_utc':now(),'group_results':[],'tiebreaks':[]}
    atomic(path,record)
    jobs=[pool.submit(evaluate_group,players,pair,g,model,key,directory/'groups'/g['id'],orientation) for g in pair['result']['groups']]
    record['group_results']=[j.result() for j in jobs]
    vote=group_vote([{'winner':x['winner']} for x in record['group_results']])
    record.update(primary_wins_a=vote['wins_a'],primary_wins_b=vote['wins_b'],primary_ties=vote['ties'],group_count=vote['group_count'])
    if vote['winner']:
        winner=pair['canonical_'+vote['winner'].lower()];method='paired_group_win_count'
    else:winner,method=decide_tie(players,pair,model,key,record,directory,pool)
    loser=record['canonical_b'] if winner==record['canonical_a'] else record['canonical_a']
    record.update(status='complete',winner=winner,loser=loser,aesthetic_winner=winner if method!='manual_advancement' else None,
                  decision_method=method,completed_utc=now(),score_by_player={record['canonical_a']:vote['wins_a'],record['canonical_b']:vote['wins_b']})
    atomic(path,record);event('match_complete',model=model,match=record['id'],winner=winner,loser=loser,score=record['score_by_player'],decision=method)
    return record

