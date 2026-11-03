#!/usr/bin/env python3
"""Real Pi/container input and recovery loop against a local synthetic API."""
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import argparse,base64,hashlib,json,subprocess,threading
from ppt_rl.inference import run_inference

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--sandbox-image',required=True);p.add_argument('--reference-registry',type=Path,required=True);p.add_argument('--node',required=True);a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=True);materials=a.out/'materials';materials.mkdir()
    factual='# Synthetic source attachment probe\n\nThis offline fixture describes a moon garden with exactly seven blue benches. It is not a real experiment task.\n'
    (materials/'material.md').write_text(factual)
    ref=next(iter(json.loads(a.reference_registry.read_text()).values()));requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(body);n=len(requests)
            def event(kind,value):return 'event: '+kind+'\ndata: '+json.dumps({'type':kind,**value})+'\n\n'
            s=event('message_start',{'message':{'id':f'input-fixture-{n}','type':'message','role':'assistant','model':'offline-style-fixture','content':[],'stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':10,'output_tokens':0}}})
            if n==2:
                command='python -c "from pathlib import Path; from pptx import Presentation; assert len(list(Path(\'materials/style_reference/model-views\').glob(\'reference-*.png\')))==4; assert not Path(\'/data3/wancong\').exists(); p=Presentation(); p.slides.add_slide(p.slide_layouts[6]); p.save(\'outputs/presentation.pptx\')"'
                s+=event('content_block_start',{'index':0,'content_block':{'type':'tool_use','id':'fixture-export','name':'bash','input':{}}})
                s+=event('content_block_delta',{'index':0,'delta':{'type':'input_json_delta','partial_json':json.dumps({'command':command})}});stop='tool_use'
            else:
                text='<tool_call><function=Read>materials/material.md</function></tool_call>' if n==1 else 'The synthetic fixture is saved.'
                s+=event('content_block_start',{'index':0,'content_block':{'type':'text','text':''}})+event('content_block_delta',{'index':0,'delta':{'type':'text_delta','text':text}});stop='end_turn'
            s+=event('content_block_stop',{'index':0})+event('message_delta',{'delta':{'stop_reason':stop,'stop_sequence':None},'usage':{'output_tokens':20}})+event('message_stop',{})
            data=s.encode();self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('172.17.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        result=run_inference(materials,a.out/'rollout',model='offline-style-fixture',endpoint=f'http://172.17.0.1:{server.server_port}',node=a.node,sandbox_image=a.sandbox_image,style_pdf=ref['overview_pdf']['path'],style_manifest=ref['model_manifest'],reference_images=ref['previews'],inline_content=True,max_turns=8,tool_policy={'bash_timeout_seconds':2,'finish_after_structural_export':True,'continue_missing_submission':True})
    finally:server.shutdown();server.server_close();thread.join()
    assert result['success'] and result['automatic_protocol_continuations']==1 and result['pages']==1,result
    assert len(requests)==3
    first=requests[0]['messages'];assert len(first)==1 and first[0]['role']=='user'
    blocks=first[0]['content'];text='\n'.join(b.get('text','') for b in blocks if b['type']=='text');assert factual in text
    images=[b for b in blocks if b['type']=='image'];assert len(images)==4
    image_shas=[hashlib.sha256(base64.b64decode(b['source']['data'])).hexdigest() for b in images]
    assert image_shas==[b['sha256'] for b in ref['previews']]
    assert sum(b.get('role')=='user' for b in requests[1]['messages'])==2
    report={'status':'passed','synthetic_fixture':True,'real_model_calls':0,'actual_pi_sdk':'1.4.0','provider_requests':len(requests),'first_request_complete_factual_markdown_verified':True,'first_request_reference_images_byte_verified':image_shas,'automatic_protocol_continuations':1,'actual_tool_calls':result['tool_calls'],'pptx_pages':result['pages'],'sandbox_image':a.sandbox_image,'pdf_only_reference_supported':result['style_reference_kind']=='pdf'}
    (a.out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))

if __name__=='__main__':main()
