#!/usr/bin/env python3
"""Local Pi workers using the owner-private HTTPS queue. No inbound ports."""
import argparse
import concurrent.futures
from datetime import datetime, timezone
import getpass
import hashlib
import importlib.metadata
import io
import zipfile
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import uuid
import requests

HERE=Path(__file__).resolve().parent
SITE=os.environ.get('PPT_COORDINATOR_URL','http://127.0.0.1:8787')
STOP=threading.Event()


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)


def read(path,default=None):
    try:return json.loads(Path(path).read_text())
    except (OSError,ValueError):return default


class LeaseLost(Exception):pass


class Client:
    def __init__(self,url,token):
        if not url.startswith('https://') and not url.startswith('http://127.0.0.1:'):
            raise ValueError('Use HTTPS or local test loopback')
        self.url=url.rstrip('/')+'/api/sync/';self.token=token

    def call(self,path,body=None,retries=2):
        for attempt in range(retries+1):
            try:
                r=requests.request('GET' if body is None else 'POST',self.url+path,
                    headers={'OAI-Sites-Authorization':'Bearer '+self.token},json=body,
                    timeout=(15,35),allow_redirects=False)
                if r.status_code==409:raise LeaseLost(r.json().get('error','Conflict'))
                r.raise_for_status();return r.json()
            except LeaseLost:raise
            except (requests.RequestException,ValueError):
                if attempt==retries:raise
                STOP.wait(min(2**attempt,4))

    def download(self,key,sha,cache):
        cache.mkdir(parents=True,exist_ok=True);target=cache/(sha+'.tar.gz')
        if target.is_file() and digest(target)==sha:return target
        tmp=target.with_name(target.name+'.'+uuid.uuid4().hex+'.part')
        r=requests.get(self.url+'objects/'+key,headers={'OAI-Sites-Authorization':'Bearer '+self.token},timeout=(15,120),stream=True,allow_redirects=False)
        r.raise_for_status()
        with tmp.open('wb') as f:
            for block in r.iter_content(1024*1024):f.write(block)
        if digest(tmp)!=sha:
            tmp.unlink(missing_ok=True);raise ValueError('Downloaded bundle SHA256 mismatch')
        tmp.replace(target);return target


def validate_code():
    files=read(HERE/'CODE_FILES.json')
    if not files:raise RuntimeError('Missing immutable code file manifest')
    for name,expected in files.items():
        if digest(HERE/name)!=expected:raise RuntimeError('Code differs from pinned version: '+name)
    return hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def doctor(options):
    validate_code()
    node=shutil.which(options.node)
    if not node:raise RuntimeError('Node.js not found')
    for package in ['requests','pypdf','PyMuPDF','python-pptx']:
        importlib.metadata.version(package)
    if not (HERE/'node_modules/@earendil-works/pi-coding-agent').is_dir():
        raise RuntimeError('Run npm ci in the code directory first')
    endpoint=options.model_url.rstrip('/')
    if not re.match(r'^https?://',endpoint):raise ValueError('Invalid local model gateway URL')
    # A transport probe only; this does not invoke the model.
    r=requests.get(endpoint+'/health',timeout=(5,15),allow_redirects=False)
    if r.status_code>=500:raise RuntimeError('Gateway health failed')
    models=requests.get(endpoint+'/v1/models',timeout=(5,15),allow_redirects=False)
    models.raise_for_status()
    expected_model=os.environ.get('PB_MODEL_ID','Qwen3.8-27B')
    if expected_model not in [m.get('id') for m in models.json().get('data',[])]:
        raise RuntimeError('Expected Qwen3.8-27B is not served by this gateway')
    from pptx import Presentation
    sample=Presentation();sample.slides.add_slide(sample.slide_layouts[6])
    buffer=io.BytesIO();sample.save(buffer);buffer.seek(0)
    if len(Presentation(buffer).slides)!=1:raise RuntimeError('PPTX roundtrip failed')
    return {'node':subprocess.check_output([node,'--version'],text=True).strip(),
        'python':platform.python_version(),'model':expected_model,'pi_version':'1.4.0',
        'gateway_transport_status':r.status_code,'model_tool_roundtrip_verified':False,
        'code_files_verified':True,'pptx_library_roundtrip_verified':True,'gateway_model_id_verified':True,
        'gpus_reported_by_operator':options.gpus,'source_image_reading':True,'source_pdf_rendering':True,'source_pdf_image_extraction':True,'source_pdf_region_crop':True}


def extract_task(archive,material,dest):
    prefix=material+'/'
    dest.mkdir(parents=True,exist_ok=True)
    with tarfile.open(archive,'r:gz') as tar:
        chosen=[m for m in tar.getmembers() if m.name.startswith(prefix)]
        if not chosen:raise ValueError('Task absent from input bundle')
        for member in chosen:
            relative=Path(member.name).relative_to(material)
            if not member.isfile() or '..' in relative.parts or relative.is_absolute():
                raise ValueError('Unexpected archive entry')
            data=tar.extractfile(member).read();target=dest/relative
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    metadata=read(dest/'metadata.json')
    if metadata['id']!=material:raise ValueError('Input material ID mismatch')
    source=next(p for p in dest.glob('source.*') if p.is_file())
    if digest(source)!=metadata['sha256']:raise ValueError('Source file SHA256 mismatch')
    if digest(dest/'cleaned.txt')!=metadata['cleaned_text_sha256']:
        # cleaned.txt has one serialization newline, excluded by the source audit.
        body=(dest/'cleaned.txt').read_text().rstrip('\n')
        if hashlib.sha256(body.encode()).hexdigest()!=metadata['cleaned_text_sha256']:
            raise ValueError('Cleaned material SHA256 mismatch')
    return metadata


def terminate(process):
    if process.poll() is not None:return
    if os.name=='posix':os.killpg(process.pid,signal.SIGTERM)
    else:process.terminate()
    try:process.wait(timeout=12)
    except subprocess.TimeoutExpired:
        if os.name=='posix':os.killpg(process.pid,signal.SIGKILL)
        else:process.kill()
        process.wait()


def run_task(client,options,worker,task):
    attempt=options.work_dir/options.machine/('slot-'+worker.rsplit(':',1)[-1])/task['material_id']/('attempt-%04d'%task['attempts'])
    if attempt.exists():raise RuntimeError('Refusing to overwrite existing attempt: '+str(attempt))
    attempt.mkdir(parents=True)
    save(attempt/'claim.json',task)
    base={'worker_id':worker,'task_id':task['id'],'lease_token':task['lease_token']}
    log=attempt/'pi-agent';log.mkdir();work=log/'work'
    (work/'materials').mkdir(parents=True);(work/'outputs').mkdir();(work/'scratch').mkdir()
    process=None;started=time.monotonic();last_ok=time.monotonic();last_heartbeat=0
    result={'status':'failed','agent_state':'failed','submission_ready':False,'material_id':task['material_id'],
        'machine':options.machine,'worker_id':worker,'code_release':options.release,'attempt_directory':str(attempt),
        'trajectory_storage':'local_only','official_judge_run':False,
        'campaign_id':os.environ.get('PPT_CAMPAIGN_ID'),
        'teacher_model':os.environ.get('PB_MODEL_ID'),
        'model_id':os.environ.get('PB_MODEL_ID'),
        'teacher_deployment':json.loads(os.environ.get('PPT_TEACHER_DEPLOYMENT','{}'))}
    try:
        archive=client.download(task['input_key'],task['input_sha'],options.work_dir/'input-cache')
        metadata=extract_task(archive,task['material_id'],attempt/'original-input')
        material=attempt/'original-input/material.md';shutil.copy2(material,work/'materials/material.md')
        inputs=[{'file':'materials/material.md','sha256':digest(material),'source_sha256':metadata['sha256']}]
        if metadata['source_format']=='pdf' or (attempt/'original-input/original.pdf').is_file():
            source=attempt/('original-input/source.pdf' if metadata['source_format']=='pdf' else 'original-input/original.pdf');target=work/'materials/source.pdf';shutil.copy2(source,target)
            from read_text import pdf_text
            text=target.with_suffix('.extracted.txt');text.write_text(pdf_text(target))
            inputs.append({'file':'materials/source.pdf','sha256':digest(target),'extracted_text':'materials/source.extracted.txt','input_mode':'text_and_visual_original_pdf'})
        plan=json.loads(task['plan_json'])
        asset_dir=attempt/'original-input/assets'
        if asset_dir.is_dir():
            shutil.copytree(asset_dir,work/'materials/assets')
            for asset in plan.get('visual_assets',[]):
                target=work/asset['path']
                if not target.is_file() or digest(target)!=asset['sha256']:raise ValueError('Source image SHA256 mismatch: '+asset['path'])
                inputs.append({'file':asset['path'],'sha256':asset['sha256'],'kind':asset.get('kind'),'source_url':asset.get('source_url'),'caption':asset.get('caption','')})
            if (work/'materials/assets/index.md').is_file():inputs.append({'file':'materials/assets/index.md','kind':'source_figure_index'})
        result.update(task_revision=plan.get('revision_id','original-text-v1'),input_bundle_sha256=task['input_sha'],source_image_count=len(plan.get('visual_assets',[])),source_pdf_visual_tools=True)
        (work/'instructions.md').write_text(plan['instructions'])
        save(work/'materials.json',inputs)
        for helper in ['read_text.py','pdf_tools.py']:shutil.copy2(HERE/helper,work/helper)
        shutil.copy2(HERE/'task-prompt.txt',log/'prompt.txt')
        save(log/'runner-config.json',{'cwd':str(work),'log_dir':str(log),'python':sys.executable,'max_turns':80,'query':plan['query']})
        child_env={k:v for k,v in os.environ.items() if not k.startswith(('ANTHROPIC_','OPENAI_','CLAUDE_','PI_','OAI_SITES_','PB_COORD_','HF_TOKEN','HUGGING_FACE_HUB_TOKEN'))}
        child_env.update(PATH=os.pathsep.join([str(Path(sys.executable).parent),str(Path(options.node).resolve().parent),child_env.get('PATH',os.defpath)]),PB_MODEL_BASE_URL=options.model_url,PI_CODING_AGENT_DIR=str(log/'agent-config'),PI_OFFLINE='1',PI_SKIP_VERSION_CHECK='1',TMPDIR=str(work/'scratch'))
        client.call('heartbeat',{**base,'progress':{'phase':'inputs_prepared'}})
        last_ok=time.monotonic()
        with (log/'stdout.log').open('w') as out,(log/'stderr.log').open('w') as err:
            process=subprocess.Popen([options.node,str(HERE/'pi_runner.mjs'),str(log/'runner-config.json')],cwd=work,env=child_env,stdout=out,stderr=err,start_new_session=os.name=='posix')
            save(attempt/'process.json',{'pid':process.pid,'runner_config':str(log/'runner-config.json'),'started_at':datetime.now(timezone.utc).isoformat()})
            while process.poll() is None:
                if STOP.is_set():raise InterruptedError('Worker shutdown requested')
                if time.monotonic()-last_ok>60:raise LeaseLost('No confirmed lease renewal for 60s')
                if shutil.disk_usage(options.work_dir).free < 5*1024**3:
                    raise RuntimeError('Local output filesystem has <5GiB free; preserving this attempt and stopping computation')
                if time.monotonic()-last_heartbeat>=20:
                    try:
                        live=read(log/'live.json',{})
                        progress={k:live[k] for k in ['status','assistant_responses','tool_calls','provider_requests','thinking_blocks','elapsed_seconds','completed_turns'] if k in live}
                        progress.update(local_attempt_directory=str(attempt),runner_pid=process.pid)
                        client.call('heartbeat',{**base,'progress':progress},retries=0);last_ok=time.monotonic()
                    except LeaseLost:raise
                    except Exception:
                        if time.monotonic()-last_ok>60:raise LeaseLost('No confirmed renewal for 60s; stop computation before lease expiry')
                    last_heartbeat=time.monotonic()
                STOP.wait(1)
        cleanup={'runner_process_group':process.pid,'orphan_group_found':False,'forced':False}
        if os.name=='posix':
            try:
                os.killpg(process.pid,signal.SIGTERM);cleanup['orphan_group_found']=True
                for _ in range(20):
                    try:os.killpg(process.pid,0)
                    except ProcessLookupError:break
                    time.sleep(.05)
                else:
                    os.killpg(process.pid,signal.SIGKILL);cleanup['forced']=True
            except ProcessLookupError:pass
        save(log/'process-group-cleanup.json',cleanup)
        native=read(log/'runner-result.json',{})
        for key in ['status','assistant_responses','tool_calls','provider_requests','completed_turns','thinking_blocks']:
            if key in native and key!='status':result[key]=native[key]
        result['agent_state']=native.get('status','failed');result['exit_code']=process.returncode
        pptx=work/'outputs/presentation.pptx'
        if pptx.is_file():
            if pptx.is_symlink() or not pptx.resolve().is_relative_to(work.resolve()) or pptx.stat().st_nlink!=1:
                raise ValueError('Final PPTX must be an unshared regular file inside this task workspace')
            from pptx import Presentation
            with zipfile.ZipFile(pptx) as archive:
                if archive.testzip() is not None:raise ValueError('PPTX ZIP corruption')
                if '[Content_Types].xml' not in archive.namelist() or 'ppt/presentation.xml' not in archive.namelist():
                    raise ValueError('Not a PowerPoint Open XML document')
            presentation=Presentation(str(pptx))
            result.update(pages=len(presentation.slides),pptx_sha256=digest(pptx),pptx_path=str(pptx),
                slide_width_emu=presentation.slide_width,slide_height_emu=presentation.slide_height)
            result['submission_ready']=len(presentation.slides)>0
            result['artifact_validation']='zip_integrity_and_python_pptx_open_not_visual_quality'
        if result.get('submission_ready'):
            pptx.chmod(0o444)
            result['pptx_write_protected']=True
        if native.get('status')=='completed' and result['submission_ready'] and result.get('tool_calls',0)>0:
            result['status']='succeeded'
        else:result['error']=native.get('error') or 'Pi ended as '+result['agent_state']
    except Exception as error:
        if process:terminate(process)
        result.update(error=str(error),agent_state='lease_lost' if isinstance(error,LeaseLost) else 'interrupted' if isinstance(error,InterruptedError) else 'failed')
    finally:
        result['elapsed_seconds']=round(time.monotonic()-started,3)
        source_audit=[]
        for line in (log/'read-audit.jsonl').read_text().splitlines() if (log/'read-audit.jsonl').exists() else []:
            try:source_audit.append(json.loads(line))
            except ValueError:pass
        result['source_image_tool_returns']=sum(x.get('returned_image_blocks',0) for x in source_audit if x.get('event') in ['source_image_read','source_pdf_visual_read','source_pdf_images_extracted'])
        result['source_pdf_visual_operations']=sum(x.get('event') in ['source_pdf_visual_read','source_pdf_images_extracted'] for x in source_audit)
        trace={str(p.relative_to(attempt)):{'sha256':digest(p),'bytes':p.stat().st_size} for p in sorted(log.rglob('*')) if p.is_file()}
        save(attempt/'trace-manifest.json',trace);result['trace_manifest_sha256']=digest(attempt/'trace-manifest.json')
        result['trace_manifest_path']=str(attempt/'trace-manifest.json')
        save(attempt/'result.json',result)
        completion={**base,'result':result};save(attempt/'pending-completion.json',completion)
        try:
            client.call('complete',completion)
            (attempt/'pending-completion.json').rename(attempt/'committed-completion.json')
        except LeaseLost:
            (attempt/'pending-completion.json').rename(attempt/'stale-completion.json')
        except Exception:
            print(json.dumps({'worker':worker,'event':'completion_pending','path':str(attempt/'pending-completion.json')}),flush=True)
        print(json.dumps({'worker':worker,'task':task['material_id'],'status':result['status'],'agent_state':result['agent_state'],'path':str(attempt)},ensure_ascii=False),flush=True)


def slot(options,token,index,metadata):
    worker=options.machine+':'+str(index);client=Client(options.site,token)
    client.call('register',{'worker_id':worker,'machine':options.machine,'release_id':options.release,'metadata':{**metadata,'test_only':options.heartbeat_only}})
    if options.heartbeat_only:
        client.call('heartbeat',{'worker_id':worker});print(json.dumps({'worker':worker,'heartbeat':'verified','model_invoked':False}),flush=True);return
    # Replay committed-result requests idempotently after network interruption.
    for pending in (options.work_dir/options.machine/('slot-'+str(index))).glob('*/attempt-*/pending-completion.json'):
        try:client.call('complete',read(pending));pending.rename(pending.with_name('committed-completion.json'))
        except LeaseLost:pending.rename(pending.with_name('stale-completion.json'))
    state=options.work_dir/'client-state'/(worker.replace(':','-')+'.json')
    while not STOP.is_set():
        if shutil.disk_usage(options.work_dir).free < 10*1024**3:
            client.call('heartbeat',{'worker_id':worker})
            print(json.dumps({'worker':worker,'state':'waiting_for_disk','minimum_free_gib':10,'work_dir':str(options.work_dir)}),flush=True)
            STOP.wait(60);continue
        claim_id=(read(state,{}) or {}).get('claim_id') or str(uuid.uuid4());save(state,{'claim_id':claim_id})
        try:
            response=client.call('claim',{'worker_id':worker,'claim_id':claim_id});task=response.get('task')
            if not task:
                state.unlink(missing_ok=True);client.call('heartbeat',{'worker_id':worker});STOP.wait(15);continue
            # A request UUID is reusable only while its response is unknown.
            # Once a lease is observed, never reuse that token for a later attempt.
            state.unlink(missing_ok=True)
            run_task(client,options,worker,task)
        except LeaseLost as error:
            print(json.dumps({'worker':worker,'conflict':str(error)}),flush=True);state.unlink(missing_ok=True);STOP.wait(20)
        except Exception as error:
            print(json.dumps({'worker':worker,'network_or_local_error':str(error)}),flush=True);STOP.wait(15)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--site',default=SITE);p.add_argument('--machine',required=True);p.add_argument('--slots',type=int,default=1)
    p.add_argument('--release',required=True);p.add_argument('--work-dir',type=Path,default=HERE/'runs')
    p.add_argument('--model-url',default='http://127.0.0.1:18767');p.add_argument('--node',default=shutil.which('node') or 'node')
    p.add_argument('--gpus',default='',help='Operator-reported physical GPU IDs for provenance')
    p.add_argument('--doctor',action='store_true');p.add_argument('--heartbeat-only',action='store_true')
    p.add_argument('--token-stdin',action='store_true',help='Read existing Sites service credential without echo')
    p.add_argument('--token-socket',type=Path,help='Receive the credential once over a private local Unix socket, for systemd')
    o=p.parse_args();o.work_dir=o.work_dir.resolve()
    o.work_dir.mkdir(parents=True,exist_ok=True)
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,70}',o.machine):p.error('Invalid machine ID')
    if not re.fullmatch(r'[a-f0-9]{64}',o.release):p.error('Use exact code archive SHA256')
    if not 1<=o.slots<=16:p.error('Use 1–16 worker slots')
    if o.heartbeat_only:
        validate_code();metadata={'model':expected_model,'pi_version':'1.4.0','model_tool_roundtrip_verified':False}
    else:metadata=doctor(o)
    if o.doctor:
        print(json.dumps(metadata,ensure_ascii=False));return
    if bool(o.token_stdin)==bool(o.token_socket):p.error('Use exactly one of --token-stdin or --token-socket')
    if o.token_socket:
        socket_path=o.token_socket.resolve()
        if socket_path.exists():raise RuntimeError('Credential socket already exists; do not overwrite it')
        listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            listener.bind(str(socket_path));os.chmod(socket_path,0o600);listener.listen(1);listener.settimeout(300)
            print('Ready for credential through private local socket',flush=True)
            connection,_=listener.accept()
            with connection:
                chunks=[];size=0
                while True:
                    part=connection.recv(4096)
                    if not part:break
                    size+=len(part)
                    if size>16000:raise ValueError('Credential input too large')
                    chunks.append(part)
                token=b''.join(chunks).decode().strip()
        finally:listener.close();socket_path.unlink(missing_ok=True)
    else:token=getpass.getpass('Existing Sites service token (hidden): ')
    if not token:raise ValueError('Missing service credential')
    for signum in [signal.SIGTERM,signal.SIGINT]:signal.signal(signum,lambda *_:STOP.set())
    with concurrent.futures.ThreadPoolExecutor(max_workers=o.slots) as pool:
        futures=[pool.submit(slot,o,token,i,metadata) for i in range(o.slots)]
        for f in concurrent.futures.as_completed(futures):f.result()


if __name__=='__main__':main()
