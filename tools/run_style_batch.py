#!/usr/bin/env python3
"""Run a frozen style-only task batch with isolated Pi sessions and durable archives."""
from __future__ import annotations
import argparse,base64,gzip,hashlib,json,queue,shutil,subprocess,sys,threading
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
from ppt_rl.inference import run_inference
from ppt_rl.io import read_json,write_json,sha256

def utc():return datetime.now(timezone.utc).isoformat()
def rooted(root,name):
    p=(root/name).resolve()
    if not p.is_relative_to(root):raise ValueError('Batch path escapes root')
    return p

def verify_task(root,task,refs):
    materials=rooted(root,task['materials_dir'])
    actual={p.relative_to(materials).as_posix():sha256(p) for p in materials.rglob('*') if p.is_file()}
    expected={p['file']:p['sha256'] for p in task['content_files']}
    if actual!=expected:raise ValueError('Material hash mismatch: '+task['sample_id'])
    text=(materials/'material.md').read_text(encoding='utf-8')
    if not 800<=len(text)<=60000:raise ValueError('Material outside declared full-text bounds')
    ref=refs[task['style_id']]
    for name in ['pptx','overview_pdf','original_pdf','manifest']:
        if ref.get(name) and sha256(rooted(root,ref[name]['file']))!=ref[name]['sha256']:raise ValueError('Reference hash mismatch: '+name)
    if len(ref['images'])!=4:raise ValueError('Each reference requires four frozen whole-page images')
    for image in ref['images']:
        if sha256(rooted(root,image['file']))!=image['sha256']:raise ValueError('Reference page hash mismatch')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--plan',type=Path,required=True);p.add_argument('--node',required=True);p.add_argument('--workers',type=int,default=10);p.add_argument('--resume',action='store_true');a=p.parse_args()
    root=a.root.resolve();plan_path=a.plan.resolve();plan=read_json(plan_path);digest=sha256(plan_path);tasks=plan['tasks'];refs=plan['references']
    if not 1<=a.workers<=16:raise ValueError('workers must be between 1 and 16')
    if len({t['sample_id'] for t in tasks})!=len(tasks):raise ValueError('Duplicate sample IDs')
    checkout=Path(__file__).resolve().parents[1];commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=checkout,text=True).strip()
    if subprocess.check_output(['git','status','--porcelain'],cwd=checkout,text=True).strip():raise ValueError('Commit the batch runtime before collection')
    if plan['code_commit']!=commit:raise ValueError('Frozen code commit mismatch')
    for task in tasks:verify_task(root,task,refs)
    root.mkdir(parents=True,exist_ok=True);active=root/'active'
    if active.exists() and any(active.iterdir()):raise ValueError('Existing active attempts require explicit inspection; none will be overwritten')
    state={'started_utc':utc(),'plan_sha256':digest,'code_commit':commit,'planned_attempts':len(tasks),'workers':a.workers,'qualified_dataset_upload':False,'attempts':{t['sample_id']+'/style':{'status':'pending','source_task_id':t['source_task_id'],'style_id':t['style_id']} for t in tasks}}
    if a.resume and (root/'status.json').is_file():
        prior=read_json(root/'status.json')
        if prior['plan_sha256']!=digest or prior['code_commit']!=commit:raise ValueError('Resume requires the same code and plan')
        state=prior
    elif (root/'status.json').exists():raise ValueError('Existing status requires --resume')
    lock=threading.Lock();work=queue.Queue();failures=[]
    for task in tasks:
        target=root/'runs'/task['sample_id']/'style'
        if target.exists():
            if not a.resume or not (target/'result.json').is_file():raise ValueError('Refuse to overwrite archived attempt')
            continue
        work.put(task)
    def checkpoint(kind,**fields):
        with lock:
            e={'at_utc':utc(),'event':kind,**fields};state['updated_utc']=e['at_utc']
            state['completed_attempts']=sum(x['status'] in ['completed','failed'] for x in state['attempts'].values());state['successful_attempts']=sum(x.get('success',False) for x in state['attempts'].values())
            with (root/'experiment-events.jsonl').open('a') as f:f.write(json.dumps(e,ensure_ascii=False)+'\n')
            write_json(root/'status.json',state);print(json.dumps(e,ensure_ascii=False),flush=True)
    def worker(index):
        slot=f'pair{index:02d}';folder=active/slot
        while True:
            if (root/'STOP').exists():return
            try:task=work.get_nowait()
            except queue.Empty:return
            sid=task['sample_id'];key=sid+'/style';target=root/'runs'/sid/'style';ref=refs[task['style_id']]
            if sha256(plan_path)!=digest:raise ValueError('Frozen plan changed')
            verify_task(root,task,refs)
            with lock:state['attempts'][key].update(status='running',started_utc=utc(),worker_slot=slot,active_dir=str(folder))
            checkpoint('attempt_started',sample_id=sid,source_task_id=task['source_task_id'],style_id=task['style_id'],worker_slot=slot)
            result=run_inference(rooted(root,task['materials_dir']),folder,model=plan['model'],endpoint=f"http://{plan['proxy_host']}:{plan['proxy_base_port']+index}",node=a.node,
                query=task['query'],sampling=dict(plan['sampling'],base_seed=task['base_seed']),max_turns=plan['max_turns'],sandbox_image=plan['sandbox_image'],sandbox_fonts=rooted(root,plan['sandbox_fonts']),tool_policy=plan['tool_policy'],
                style_template=rooted(root,ref['pptx']['file']) if ref.get('pptx') else None,style_pdf=rooted(root,ref['overview_pdf']['file']),style_manifest=rooted(root,ref['manifest']['file']),style_source_pdf=rooted(root,ref['original_pdf']['file']) if ref.get('original_pdf') else None,
                inline_content=True,inline_content_max_characters=60000,reference_images=[dict(path=str(rooted(root,v['file'])),page=v['page'],sha256=v['sha256'],mime_type='image/png') for v in ref['images']])
            # Native logs retain the historical slot path. Never resolve that path after reuse.
            target.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(folder),str(target))
            artifact=target/'work/outputs/presentation.pptx'
            if artifact.is_file() and sha256(artifact)!=result.get('pptx_sha256'):raise ValueError('Archived artifact hash mismatch')
            write_json(target/'archive-location.json',{'original_working_directory':str(folder/'work'),'archived_directory':str(target),'pptx_relative_path':'work/outputs/presentation.pptx' if artifact.is_file() else None,'note':'Original logs retain historical slot paths. Use archived relative files and recorded SHA-256.'})
            capture=target/'logs/http-audit/001.json.gz';delivery={'status':'request_body_missing'}
            if capture.is_file():
                body=json.loads(gzip.decompress(capture.read_bytes()));messages=body.get('messages',[]);text='\n'.join(b.get('text','') for m in messages for b in (m.get('content',[]) if isinstance(m.get('content'),list) else []) if b.get('type')=='text')
                original=(rooted(root,task['materials_dir'])/'material.md').read_text()
                image_blocks=[b for m in messages for b in (m.get('content',[]) if isinstance(m.get('content'),list) else []) if b.get('type')=='image']
                image_shas=[hashlib.sha256(base64.b64decode(b['source']['data'])).hexdigest() for b in image_blocks]
                images_verified=image_shas==[v['sha256'] for v in ref['images']]
                delivery={'status':'verified' if original in text and images_verified else 'delivery_mismatch','complete_factual_markdown_present':original in text,'frozen_content_sha256':sha256(rooted(root,task['materials_dir'])/'material.md'),'reference_image_blocks':len(image_blocks),'reference_image_sha256':image_shas,'reference_images_byte_verified':images_verified,'first_http_body_gzip_sha256':sha256(capture),'meaning_limit':'Visible in actual request; this does not prove semantic understanding or correct output.'}
            write_json(target/'initial-input-http-verification.json',delivery)
            with lock:state['attempts'][key].update(status='completed' if result['success'] else 'failed',archive_dir=str(target),success=result['success'],agent_state=result.get('agent_state'),pages=result.get('pages'),elapsed_seconds=result['elapsed_seconds'],tool_calls=result.get('tool_calls'),automatic_protocol_continuations=result.get('automatic_protocol_continuations',0),initial_input_http_status=delivery['status'],finished_utc=result['finished_utc'])
            checkpoint('attempt_finished',sample_id=sid,success=result['success'],agent_state=result.get('agent_state'),pages=result.get('pages'),input_delivery=delivery['status'])
            work.task_done()
    checkpoint('batch_started')
    proxy=subprocess.Popen([sys.executable,str(checkout/'tools/pi_recording_proxy.py'),'--root',str(root),'--host',plan['proxy_host'],'--base-port',str(plan['proxy_base_port']),'--pairs',str(a.workers),'--upstream',plan['upstream']],stdout=subprocess.PIPE,text=True)
    try:
        ready=proxy.stdout.readline()
        if not ready or not json.loads(ready).get('ready'):raise RuntimeError('Recording proxy did not start')
        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            futures={pool.submit(worker,i):i for i in range(1,a.workers+1)}
            for f in as_completed(futures):
                try:f.result()
                except Exception as error:failures.append({'worker':futures[f],'error':type(error).__name__+': '+str(error)});checkpoint('controller_error',**failures[-1])
    finally:proxy.terminate();proxy.wait(timeout=30)
    state['finished_utc']=utc();state['controller_errors']=failures;checkpoint('batch_stopped' if (root/'STOP').exists() else 'batch_finished')
    return int(bool(failures) or state['completed_attempts']!=len(tasks))

if __name__=='__main__':raise SystemExit(main())
