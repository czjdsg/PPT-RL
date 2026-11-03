#!/usr/bin/env python3
"""Exercise the real Pi SDK and bash loop using a deterministic local mock API."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import argparse,json,tempfile,threading,subprocess
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from ppt_rl.inference import run_inference

def main():
    p=argparse.ArgumentParser();p.add_argument('--node',required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--sandbox-image');p.add_argument('--bind',default='127.0.0.1');p.add_argument('--exercise-timeout',action='store_true');p.add_argument('--through-proxy',action='store_true');a=p.parse_args()
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(payload);index=len(requests)
            def event(kind,value):return 'event: '+kind+'\ndata: '+json.dumps({'type':kind,**value})+'\n\n'
            body=event('message_start',{'message':{'id':f'mock-{index}','type':'message','role':'assistant','model':'offline-pi-fixture','content':[],'stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':10,'output_tokens':0}}})
            if a.exercise_timeout and index==1:
                command='python -c "import time; time.sleep(30)"'
                body+=event('content_block_start',{'index':0,'content_block':{'type':'tool_use','id':'fixture-timeout','name':'bash','input':{}}})
                body+=event('content_block_delta',{'index':0,'delta':{'type':'input_json_delta','partial_json':json.dumps({'command':command,'timeout':9999})}});stop='tool_use'
            elif index==(2 if a.exercise_timeout else 1):
                command="python -c \"from pptx import Presentation; p=Presentation(); s=p.slides.add_slide(p.slide_layouts[6]); s.shapes.add_textbox(0,0,2000000,500000).text='Synthetic offline fixture'; p.save('outputs/presentation.pptx')\""
                if a.sandbox_image:command="python -c \"import os; assert not os.path.exists('/data3/wancong'); assert not os.path.exists('/data4/wancong'); assert not os.path.exists('/home/wancong')\" && "+command
                body+=event('content_block_start',{'index':0,'content_block':{'type':'tool_use','id':'fixture-bash','name':'bash','input':{}}})
                body+=event('content_block_delta',{'index':0,'delta':{'type':'input_json_delta','partial_json':json.dumps({'command':command})}});stop='tool_use'
            else:
                body+=event('content_block_start',{'index':0,'content_block':{'type':'text','text':''}})
                body+=event('content_block_delta',{'index':0,'delta':{'type':'text_delta','text':'The synthetic fixture PowerPoint is saved.'}});stop='end_turn'
            body+=event('content_block_stop',{'index':0})+event('message_delta',{'delta':{'stop_reason':stop,'stop_sequence':None},'usage':{'output_tokens':20}})+event('message_stop',{})
            encoded=body.encode();self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
    a.out.mkdir(parents=True,exist_ok=True);materials=a.out/'materials';materials.mkdir(exist_ok=True);(materials/'material.md').write_text('A synthetic fixture for offline SDK testing, not an experiment sample.\n')
    server=ThreadingHTTPServer((a.bind,0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    proxy=None;rollout=a.out/'rollout';endpoint=f'http://{a.bind}:{server.server_port}'
    if a.through_proxy:
        rollout=a.out/'active/pair01'
        proxy=subprocess.Popen([sys.executable,str(Path(__file__).with_name('pi_recording_proxy.py')),'--root',str(a.out),'--host',a.bind,'--base-port','18980','--pairs','1','--upstream',endpoint],stdout=subprocess.PIPE,text=True)
        assert json.loads(proxy.stdout.readline())['ready'];endpoint=f'http://{a.bind}:18981'
    try:r=run_inference(materials,rollout,model='offline-pi-fixture',endpoint=endpoint,sampling={'temperature':1.0,'top_p':.95,'top_k':20,'base_seed':900000},node=a.node,sandbox_image=a.sandbox_image,tool_policy={'bash_timeout_seconds':2,'finish_after_structural_export':True} if a.exercise_timeout else {})
    finally:
        server.shutdown();server.server_close();thread.join()
        if proxy:proxy.terminate();proxy.wait(timeout=10)
    count=3 if a.exercise_timeout else 2
    assert r['success'] and r['tool_calls']==(2 if a.exercise_timeout else 1) and r['pages']==1,r
    assert len(requests)==count and [x['seed'] for x in requests]==list(range(900000,900000+count))
    assert all(x['temperature']==1 and x['max_tokens']==32768 and x['thinking']['budget_tokens']==8192 for x in requests)
    if a.exercise_timeout:
        audit=[json.loads(line) for line in (rollout/'logs/tool-policy-audit.jsonl').read_text().splitlines()]
        assert audit[0]['requested_timeout_seconds']==9999 and audit[0]['effective_timeout_seconds']==2
        assert any(x['event']=='bash_error' and 'timed out' in x['error'] for x in audit)
    if a.through_proxy:
        captured=[json.loads(line) for line in (rollout/'logs/http-audit/requests.jsonl').read_text().splitlines()]
        assert len(captured)==count and all(x['path'].startswith('/v1/messages?beta=') for x in captured)
    report={'status':'passed','synthetic_fixture':True,'real_model_calls':0,'pi_sdk':'1.4.0','provider_requests':count,'tool_calls':r['tool_calls'],'pptx_pages':1,'regular_payload_seeds_verified':True,'sandbox_image':a.sandbox_image,'timeout_clamp_exercised':a.exercise_timeout,'native_beta_route_through_proxy':a.through_proxy,'logs':str(rollout/'logs')}
    (a.out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))

if __name__=='__main__':main()
