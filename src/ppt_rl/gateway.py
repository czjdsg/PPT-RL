#!/usr/bin/env python3
"""Local native-Anthropic pass-through with explicit thinking and SSE keepalive."""
import argparse
import asyncio
from contextlib import asynccontextmanager,suppress
from datetime import datetime,timezone
import json
from pathlib import Path
import time
import httpx
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse,Response,StreamingResponse
import uvicorn


def make_app(upstream,log_dir,model_id):
    log_dir.mkdir(parents=True,exist_ok=True)
    def audit(data):
        with (log_dir/'gateway-audit.jsonl').open('a') as f:f.write(json.dumps({'at_utc':datetime.now(timezone.utc).isoformat(),**data})+'\n')
    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(trust_env=False,timeout=httpx.Timeout(connect=10,read=None,write=120,pool=30)) as client:
            app.state.client=client;yield
    app=FastAPI(lifespan=lifespan)
    @app.get('/health')
    async def health():return {'gateway':'ppt-pi-native-model','upstream':upstream,'model':model_id,'thinking_mode':'explicit_per_request','sse_keepalive_seconds':20}
    @app.api_route('/{path:path}',methods=['GET','POST'])
    async def forward(path:str,request:Request):
        if path not in ['v1/models','v1/messages','v1/messages/count_tokens']:
            return JSONResponse({'error':'Unsupported route'},status_code=404)
        payload=None
        if request.method=='POST':
            payload=await request.json()
            if payload.get('model')!=model_id:return JSONResponse({'error':'Unexpected model; no fallback'},status_code=400)
            enabled=(payload.get('thinking')or{}).get('type')!='disabled'
            payload['chat_template_kwargs']={**(payload.get('chat_template_kwargs')or{}),'enable_thinking':enabled}
            audit({'event':'request','route':path,'upstream':upstream,'effective_enable_thinking':enabled,
                'requested_thinking':payload.get('thinking'),'max_tokens':payload.get('max_tokens'),
                'output_config':payload.get('output_config'),'stream':payload.get('stream',False)})
        headers={k:v for k,v in request.headers.items() if k.lower() in ['content-type','anthropic-version','anthropic-beta','x-api-key','authorization']}
        target=upstream+'/'+path+('?'+request.url.query if request.url.query else '')
        try:response=await app.state.client.send(app.state.client.build_request(request.method,target,headers=headers,json=payload),stream=True)
        except httpx.HTTPError as error:return JSONResponse({'error':{'type':'upstream_connection_error','message':str(error)}},status_code=502)
        content_type=response.headers.get('content-type','application/json')
        if 'text/event-stream' in content_type:
            async def chunks():
                iterator=response.aiter_bytes().__aiter__();pending=asyncio.create_task(anext(iterator));pings=0;started=time.monotonic()
                try:
                    while True:
                        done,_=await asyncio.wait([pending],timeout=20)
                        if not done:
                            pings+=1;yield b'event: ping\ndata: {"type":"ping"}\n\n';continue
                        try:chunk=pending.result()
                        except StopAsyncIteration:break
                        yield chunk;pending=asyncio.create_task(anext(iterator))
                finally:
                    pending.cancel()
                    with suppress(asyncio.CancelledError,StopAsyncIteration):await pending
                    await response.aclose()
                    audit({'event':'stream_closed','upstream':upstream,'keepalive_pings':pings,'elapsed_seconds':round(time.monotonic()-started,3)})
            return StreamingResponse(chunks(),status_code=response.status_code,media_type='text/event-stream')
        body=await response.aread();await response.aclose()
        return Response(body,status_code=response.status_code,media_type=content_type)
    return app


def main():
    p=argparse.ArgumentParser();p.add_argument('--upstream',required=True);p.add_argument('--port',type=int,required=True);p.add_argument('--log-dir',type=Path,required=True);p.add_argument('--model',required=True)
    a=p.parse_args()
    from urllib.parse import urlsplit
    u=urlsplit(a.upstream)
    if u.scheme not in ['http','https'] or u.username or u.password:p.error('Use a plain HTTP(S) upstream URL without embedded credentials')
    uvicorn.run(make_app(a.upstream.rstrip('/'),a.log_dir,a.model),host='127.0.0.1',port=a.port,log_level='warning')

if __name__=='__main__':main()
