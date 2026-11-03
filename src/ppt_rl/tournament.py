"""Portable entry points around the recorded eight-candidate duel engine."""
from __future__ import annotations
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
import fcntl,os,random,re,shutil,threading
from . import engine as E
from .matching import extract_pptx,DEFAULT_MODEL,SYSTEM_PROMPT
from .aesthetics import SlideScreenshots,AESTHETIC_PROMPT
from .bracket import BRACKET,ready_matches,standings,reward_conflicts
from .io import read_json,write_json,sha256

EXECUTION_LOCK=threading.Lock()

def pin_selection(out,data):
    path=Path(out)/'selection.json'
    if path.exists():
        if read_json(path)!=data:raise ValueError('Existing run has a different frozen deck selection; use a new directory')
    else:write_json(path,data)

def prepare_decks(manifest,out):
    manifest=Path(manifest).resolve();out=Path(out).resolve();data=read_json(manifest)
    if 'participants' in data and all('images' in p and 'extracted_file' in p for p in data['participants']):
        for p in data['participants']:
            assert sha256(p['pptx'])==p['pptx_sha256']
            for image in p['images']:assert sha256(image['path'])==image['sha256']
        pin_selection(out,data);return data
    decks=data.get('decks',[])
    if not decks:raise ValueError('The manifest needs a nonempty decks list')
    ids=[p['id'] for p in decks]
    if len(ids)!=len(set(ids)) or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}',x) or '__' in x for x in ids):raise ValueError('Use unique simple deck IDs without double underscores')
    result=[]
    for d in decks:
        src=Path(d['pptx']);src=src if src.is_absolute() else manifest.parent/src
        root=out/'sources'/d['id'];root.mkdir(parents=True,exist_ok=True);pptx=root/'deck.pptx'
        if pptx.exists() and sha256(pptx)!=sha256(src):raise ValueError('A previous source differs: '+d['id'])
        if not pptx.exists():shutil.copyfile(src,pptx)
        pdf=None
        if d.get('pdf'):
            p=Path(d['pdf']);p=p if p.is_absolute() else manifest.parent/p;pdf=root/'deck.pdf'
            if pdf.exists() and sha256(pdf)!=sha256(p):raise ValueError('A previous source PDF differs')
            if not pdf.exists():shutil.copyfile(p,pdf)
        images=SlideScreenshots(pptx,root,pdf=pdf);extracted=root/'extracted.json'
        current_text=extract_pptx(pptx)
        if extracted.exists():
            cached=read_json(extracted)
            if cached['source_sha256']!=current_text['source_sha256'] or cached['pages']!=current_text['pages']:raise ValueError('Existing native text extraction differs')
        else:write_json(extracted,current_text)
        pages=[images.page(i) for i in range(1,images.pages+1)]
        result.append({'id':d['id'],'rep':d['id'],'name':d.get('name',d['id']),'pptx':str(pptx),'pdf':str(images.pdf),'pptx_sha256':images.pptx_sha256,
            'pdf_sha256':images.pdf_sha256,'page_count':images.pages,'extracted_file':str(extracted),'source_manifest':str(root/'source.json'),'images':pages})
    selection={'task':data.get('task',{'id':'user-task'}),'participants':result,'participant_count':len(result),'page_count':sum(p['page_count'] for p in result),'png_count':sum(p['page_count'] for p in result)}
    pin_selection(out,selection);return selection

def configure(out,settings):
    settings=dict(settings or {})
    for key in ['api_key','token','password','secret']:
        if key in settings:raise ValueError('Use an environment variable name, not an inline credential: '+key)
    E.ROOT=Path(out).resolve();E.ROOT.mkdir(parents=True,exist_ok=True);E.SETTINGS=settings
    E.SEED=settings.get('orientation_seed',20261103)
    if settings.get('global_gate'):
        from .gate import FlashGate
        E.FLASH_LIMIT=FlashGate(settings['global_gate'],settings.get('global_limit',8),settings.get('concurrency',3))
    else:E.FLASH_LIMIT=threading.BoundedSemaphore(settings.get('concurrency',3))
    return settings.get('model',DEFAULT_MODEL)

def credential(settings):
    if settings.get('protocol','anthropic')=='openai':
        key=os.environ.get(settings.get('api_key_env','PPT_RL_JUDGE_API_KEY'))
        if not key:raise ValueError('Set the configured API key environment variable')
        return key
    return 'LOCAL_FLASH_NO_REMOTE_CREDENTIAL'

def execute(manifest,out,settings=None,slots=None,draw_seed=20261103):
    """One tournament per process; parallel draws use separate CLI processes."""
    with EXECUTION_LOCK:
        model=configure(out,settings);settings=E.SETTINGS;root=E.ROOT
        lock=(root/'controller.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        selection=prepare_decks(manifest,root);players={p['id']:p for p in selection['participants']}
        if len(players)!=8:raise ValueError('rank currently requires exactly eight decks')
        if slots is None:slots=sorted(players);random.Random(draw_seed).shuffle(slots)
        if sorted(slots)!=sorted(players):raise ValueError('slots must contain the eight candidate IDs exactly once')
        config={'created_utc':E.now(),'task':selection['task'],'models':[model],'seed':draw_seed if draw_seed is not None else None,'slots':slots,'bracket':BRACKET,
            'orientation_seed':E.SEED,'aesthetic_generation_seed':settings.get('generation_seed',20261102),'settings':settings,'grand_final_reset':False,'matches_per_tournament':14,
            'selection_sha256':sha256(root/'selection.json'),'prompts':{'alignment':SYSTEM_PROMPT,'aesthetic':AESTHETIC_PROMPT},
            'scoring':'One vote per paired group; group ties give neither side a win. No page-count weighting.',
            'reward_policy':{'1':1,'2':.8,'3':.6,'4':.4,'5-6':.2,'7-8':0}}
        prior=root/'config.json'
        if prior.exists():
            old=read_json(prior)
            if any(old.get(k)!=config.get(k) for k in ['slots','settings','models','orientation_seed','selection_sha256']):raise ValueError('Existing run has different inputs/settings; use a new directory')
            config=old
        else:write_json(prior,config)
        if not (root/'tie-policy.json').exists():write_json(root/'tie-policy.json',{'policy':settings.get('tie_policy','random_group_swap'),'source':'configuration_or_declared_default','user_selected':'tie_policy' in settings})
        key=credential(settings);cache=E.PairCache(players);completed={};scheduled=set();occurrences={};failures=[]
        for p in (root/'tournaments'/model/'matches').glob('*/match.json'):
            r=read_json(p)
            if r['status']=='complete':completed[r['id']]=r
        for r in completed.values():occurrences[r['pair_key']]=max(occurrences.get(r['pair_key'],0),r['pair_occurrence'])
        pending={}
        with ThreadPoolExecutor(max_workers=8) as mp,ThreadPoolExecutor(max_workers=settings.get('concurrency',3)) as gp:
            def checkpoint():
                rows=[completed[b['id']] for b in BRACKET if b['id'] in completed]
                state={'model':model,'status':'complete' if len(rows)==14 else 'in_progress','matches_completed':len(rows),'matches':rows}
                if len(rows)==14:
                    state['ranking']=standings(slots,completed);state['reward_conflicts']=reward_conflicts(completed,state['ranking']);write_json(root/'ranking.json',state['ranking'])
                write_json(root/'status.json',state);write_json(root/'progress.json',{'updated_utc':E.now(),'matches_completed':len(rows),'pending_jobs':len(pending),'failures':failures})
            def schedule():
                for match in ready_matches(slots,completed,scheduled):
                    pk=E.key_for(match['left_player'],match['right_player']);n=occurrences.get(pk,0);occurrences[pk]=n+1
                    pending[mp.submit(E.run_match,players,cache,model,key,match,n,gp)]=match['id'];scheduled.add(match['id'])
            schedule();checkpoint()
            while pending:
                done,_=wait(pending,timeout=5,return_when=FIRST_COMPLETED)
                for future in done:
                    mid=pending.pop(future)
                    try:completed[mid]=future.result()
                    except Exception as exc:
                        f={'match':mid,'error':str(exc).replace(key,'[REDACTED]'),'at_utc':E.now()};failures.append(f);write_json(root/'tournaments'/model/'matches'/mid/'match-failure.json',f)
                schedule();checkpoint()
        result=read_json(root/'status.json');write_json(root/'completion-manifest.json',{'status':result['status'],'matches':len(completed),'failures':failures,'finished_utc':E.now()})
        lock.close()
        if len(completed)!=14:raise RuntimeError('Tournament incomplete; failures and raw calls preserved')
        return result

def duel(manifest,out,settings=None):
    with EXECUTION_LOCK:
        model=configure(out,settings);lock=(E.ROOT/'controller.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        selection=prepare_decks(manifest,E.ROOT);players={p['id']:p for p in selection['participants']}
        if len(players)!=2:raise ValueError('duel requires exactly two decks')
        identity={'model':model,'settings':E.SETTINGS,'selection_sha256':sha256(E.ROOT/'selection.json'),'orientation_seed':E.SEED}
        prior=E.ROOT/'duel-config.json'
        if prior.exists() and read_json(prior)!=identity:raise ValueError('Existing duel uses different inputs/settings; use a new directory')
        if not prior.exists():write_json(prior,identity)
        ids=list(players);write_json(E.ROOT/'tie-policy.json',{'policy':E.SETTINGS.get('tie_policy','random_group_swap'),'source':'configuration_or_declared_default'})
        with ThreadPoolExecutor(max_workers=E.SETTINGS.get('concurrency',3)) as pool:
            result=E.run_match(players,E.PairCache(players),model,credential(E.SETTINGS),{'id':'DUEL','stage':'single_duel','left_player':ids[0],'right_player':ids[1]},0,pool)
        write_json(E.ROOT/'duel-result.json',result);lock.close();return result
