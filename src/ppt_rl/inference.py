"""Fresh native Pi session, original inputs, exact logs, structural PPTX checks."""
from __future__ import annotations
from pathlib import Path
from datetime import datetime,timezone
import json,os,shutil,signal,subprocess,sys,time,zipfile,uuid
from .io import read_json,write_json,sha256,workspace_environment

QUERY='Create a PPTX presentation in English based on the provided materials.'

def run_inference(materials,out,model='qwen3.8-flash-next-unsloth-q8_0',endpoint='http://127.0.0.1:8099',query=QUERY,
                  sampling=None,max_turns=80,node=None,style_template=None,style_pdf=None,
                  sandbox_image=None,tool_policy=None,style_manifest=None,sandbox_fonts=None):
    materials=Path(materials).resolve();out=Path(out).resolve();sampling=dict(sampling or {})
    runner=Path(os.environ.get('PPT_RL_PI_RUNNER',Path(__file__).resolve().parents[2]/'inference/runner'))
    node=node or shutil.which('node')
    if not node and not sandbox_image:raise RuntimeError('Node.js >=22.19 is required')
    version=subprocess.check_output(['docker','run','--rm','--network','none',sandbox_image,'node','--version'] if sandbox_image else [node,'--version'],text=True).strip().lstrip('v').split('.')
    if tuple(map(int,version[:2]))<(22,19):raise RuntimeError('Node.js >=22.19 is required; select it with --node')
    if not (runner/'node_modules/@earendil-works/pi-coding-agent').is_dir():raise RuntimeError('Run npm ci --prefix inference/runner in the source checkout')
    if (out/'logs/runner-config.json').exists():raise ValueError('Refuse to overwrite a started rollout; choose a new output directory')
    if not materials.is_dir():raise ValueError('materials must be a directory')
    work=out/'work';logs=out/'logs'
    for path in [work/'materials',work/'outputs',work/'scratch',logs]:path.mkdir(parents=True,exist_ok=True)
    inputs=[]
    for source in sorted(materials.rglob('*')):
        if source.is_symlink():raise ValueError('Input symlinks are not accepted: '+str(source))
        if not source.is_file():continue
        relative=source.relative_to(materials);target=work/'materials'/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        inputs.append({'file':target.relative_to(work).as_posix(),'sha256':sha256(target),'role':'content_source'})
    if not inputs:raise ValueError('No material files were supplied')
    if style_template:
        source=Path(style_template).resolve();target=work/'materials/style_reference/template.pptx';target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        inputs.append({'file':target.relative_to(work).as_posix(),'sha256':sha256(target),'role':'style_reference_only'})
        if style_pdf:
            target=work/'materials/style_reference/reference.pdf';shutil.copyfile(style_pdf,target)
            inputs.append({'file':target.relative_to(work).as_posix(),'sha256':sha256(target),'role':'style_reference_only'})
        if style_manifest:
            target=work/'materials/style_reference/reference-manifest.json';shutil.copyfile(style_manifest,target)
            inputs.append({'file':target.relative_to(work).as_posix(),'sha256':sha256(target),'role':'style_reference_only'})
        query+=' Use materials/style_reference/template.pptx as the target visual style. Use it for design only; derive factual content from the original content materials, not the template examples.'
        if style_manifest:query+=' Preserve the supplied reference attribution in the final presentation credits. The reference PDF shows selected original reference pages; their mapping is in the reference manifest.'
    (work/'instructions.md').write_text(query+'\n');write_json(work/'materials.json',inputs)
    for name in ['read_text.py','pdf_tools.py']:shutil.copyfile(runner/name,work/name)
    (logs/'prompt.txt').write_text(query+'\n')
    config={'cwd':'/workspace/work' if sandbox_image else str(work),'log_dir':'/workspace/logs' if sandbox_image else str(logs),
            'python':'/usr/local/bin/python' if sandbox_image else sys.executable,'max_turns':max_turns,'query':query,'sampling':sampling,'tool_policy':dict(tool_policy or {})}
    write_json(logs/'runner-config.json',config)
    env=workspace_environment(work)
    env.update(PATH=os.pathsep.join([str(Path(sys.executable).parent),str(Path(node).resolve().parent) if node else '',env.get('PATH',os.defpath)]),PB_MODEL_ID=model,PB_MODEL_BASE_URL=endpoint,PI_CODING_AGENT_DIR=str(logs/'agent-config'),PI_OFFLINE='1',PI_SKIP_VERSION_CHECK='1')
    container_name=None
    if sandbox_image:
        container_name='ppt-rl-pi-'+uuid.uuid4().hex[:16]
        command=['docker','run','--rm','--init','--name',container_name,'--label','ppt-rl.role=inference',
                 '--network','bridge','--read-only','--user',f'{os.getuid()}:{os.getgid()}',
                 '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','256','--cpus','4','--memory','6g',
                 '--tmpfs','/tmp:rw,size=512m','-v',f'{out}:/workspace:rw','-w','/workspace/work']
        for filename in ['harness.mjs','pi_runner.mjs','read_text.py','pdf_tools.py']:
            command+=['-v',f'{runner/filename}:/opt/ppt-runner/{filename}:ro']
        command+=['-v',f'{(runner/"node_modules").resolve()}:/opt/ppt-runner/node_modules:ro']
        if sandbox_fonts:
            fonts=Path(sandbox_fonts).resolve()
            if not fonts.is_dir():raise ValueError('sandbox_fonts must be a directory')
            command+=['-v',f'{fonts}:/usr/share/fonts/ppt-rl-reference:ro']
        for key,value in {'PB_MODEL_ID':model,'PB_MODEL_BASE_URL':endpoint,'PI_CODING_AGENT_DIR':'/workspace/logs/agent-config','PI_OFFLINE':'1','PI_SKIP_VERSION_CHECK':'1','NO_PROXY':'*'}.items():
            command+=['-e',key+'='+value]
        command += [sandbox_image,'node','/opt/ppt-runner/pi_runner.mjs','/workspace/logs/runner-config.json']
    else:command=[node,str(runner/'pi_runner.mjs'),str(logs/'runner-config.json')]
    started=time.monotonic();result={'started_utc':datetime.now(timezone.utc).isoformat(),'model_id':model,'endpoint':endpoint,'pi_version':'1.4.0','query':query,'sampling':sampling,'input_files':inputs,'generated_preview_feedback':False,'style_template_supplied':bool(style_template),'success':False,
        'sandbox_image':sandbox_image,'container_name':container_name,'tool_policy':dict(tool_policy or {}),
        'filesystem_scope':'One condition workspace plus read-only runner/dependency mounts; private container /tmp' if sandbox_image else 'Host process; not a full filesystem sandbox'}
    process=None
    try:
        with (logs/'stdout.log').open('w') as stdout,(logs/'stderr.log').open('w') as stderr:
            process=subprocess.Popen(command,cwd=work,env=env,stdout=stdout,stderr=stderr,start_new_session=os.name=='posix')
            process.wait()
        native=read_json(logs/'runner-result.json') if (logs/'runner-result.json').exists() else {}
        result.update(agent_state=native.get('status','failed'),exit_code=process.returncode,tool_calls=native.get('tool_calls',0),assistant_responses=native.get('assistant_responses',0))
        pptx=work/'outputs/presentation.pptx'
        if pptx.is_file():
            if pptx.is_symlink() or not pptx.resolve().is_relative_to(work.resolve()) or pptx.stat().st_nlink!=1:raise ValueError('Final PPTX must be an independent regular file within the workspace')
            with zipfile.ZipFile(pptx) as archive:
                if archive.testzip() is not None:raise ValueError('Corrupt PPTX archive')
            from pptx import Presentation
            slides=len(Presentation(str(pptx)).slides)
            result.update(submission_ready=slides>0,pages=slides,pptx_path=str(pptx),pptx_sha256=sha256(pptx),artifact_validation='Structure only; not visual or factual quality')
            if slides:pptx.chmod(0o444)
        result['success']=result.get('agent_state')=='completed' and result.get('submission_ready',False) and result.get('tool_calls',0)>0
    except BaseException as error:
        result['error']=type(error).__name__+': '+str(error)
        if isinstance(error,(KeyboardInterrupt,SystemExit)):result['agent_state']='interrupted'
    finally:
        if container_name:
            try:
                subprocess.run(['docker','stop','--time','10',container_name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
                subprocess.run(['docker','rm','-f',container_name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
            except subprocess.TimeoutExpired:result['container_cleanup_error']='Docker cleanup timed out; inspect the recorded container name'
        if process and os.name=='posix':
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait()
            for _ in range(20):
                try:os.killpg(process.pid,0)
                except ProcessLookupError:break
                time.sleep(.05)
            else:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
        result['changed_input_files']=[x['file'] for x in inputs if not (work/x['file']).is_file() or sha256(work/x['file'])!=x['sha256']]
        result['elapsed_seconds']=time.monotonic()-started;result['finished_utc']=datetime.now(timezone.utc).isoformat()
        write_json(out/'result.json',result)
        files={p.relative_to(out).as_posix():{'sha256':sha256(p),'bytes':p.stat().st_size} for p in sorted(out.rglob('*')) if p.is_file() and not p.is_symlink() and p.name!='trace-manifest.json'}
        write_json(out/'trace-manifest.json',{'files':files,'all_attempts_retained':True})
    return result
