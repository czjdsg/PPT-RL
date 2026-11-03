#!/usr/bin/env python3
"""Exercise the real Pi SDK and bash loop using a deterministic local mock API."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import argparse,json,tempfile,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from ppt_rl.inference import run_inference

def main():
    p=argparse.ArgumentParser();p.add_argument('--node',required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(payload);index=len(requests)
            def event(kind,value):return 'event: '+kind+'\ndata: '+json.dumps({'type':kind,**value})+'\n\n'
            body=event('message_start',{'message':{'id':f'mock-{index}','type':'message','role':'assistant','model':'offline-pi-fixture','content':[],'stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':10,'output_tokens':0}}})
            if index==1:
                command="python -c \"from pptx import Presentation; p=Presentation(); s=p.slides.add_slide(p.slide_layouts[6]); s.shapes.add_textbox(0,0,2000000,500000).text='Synthetic offline fixture'; p.save('outputs/presentation.pptx')\""
                body+=event('content_block_start',{'index':0,'content_block':{'type':'tool_use','id':'fixture-bash','name':'bash','input':{}}})
                body+=event('content_block_delta',{'index':0,'delta':{'type':'input_json_delta','partial_json':json.dumps({'command':command})}});stop='tool_use'
            else:
                body+=event('content_block_start',{'index':0,'content_block':{'type':'text','text':''}})
                body+=event('content_block_delta',{'index':0,'delta':{'type':'text_delta','text':'The synthetic fixture PowerPoint is saved.'}});stop='end_turn'
            body+=event('content_block_stop',{'index':0})+event('message_delta',{'delta':{'stop_reason':stop,'stop_sequence':None},'usage':{'output_tokens':20}})+event('message_stop',{})
            encoded=body.encode();self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
    a.out.mkdir(parents=True,exist_ok=True);materials=a.out/'materials';materials.mkdir(exist_ok=True);(materials/'material.md').write_text('A synthetic fixture for offline SDK testing, not an experiment sample.\n')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:r=run_inference(materials,a.out/'rollout',model='offline-pi-fixture',endpoint=f'http://127.0.0.1:{server.server_port}',sampling={'temperature':1.0,'top_p':.95,'top_k':20,'base_seed':900000},node=a.node)
    finally:server.shutdown();server.server_close();thread.join()
    assert r['success'] and r['tool_calls']==1 and r['pages']==1,r
    assert len(requests)==2 and [x['seed'] for x in requests]==[900000,900001]
    assert all(x['temperature']==1 and x['max_tokens']==32768 and x['thinking']['budget_tokens']==8192 for x in requests)
    report={'status':'passed','synthetic_fixture':True,'real_model_calls':0,'pi_sdk':'1.4.0','provider_requests':2,'tool_calls':1,'pptx_pages':1,'regular_payload_seeds_verified':True,'logs':str(a.out/'rollout/logs')}
    (a.out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))

if __name__=='__main__':main()
