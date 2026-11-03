#!/usr/bin/env python3
"""Per-pair native Messages proxies, recording regular and compaction bodies.

Bind on the Docker bridge gateway. Each pair keeps the same URL in both
conditions; its active log directory is recreated by the paired controller.
"""
from __future__ import annotations
import argparse,gzip,hashlib,json,threading,time
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import requests

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--host',required=True)
    p.add_argument('--base-port',type=int,default=18900);p.add_argument('--pairs',type=int,default=10)
    p.add_argument('--upstream',default='http://127.0.0.1:8099')
    a=p.parse_args();root=a.root.resolve();servers=[]
    def handler_for(pair_id):
        lock=threading.Lock()
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.0'
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path!='/health':self.send_error(404);return
                b=json.dumps({'ready':True,'pair_id':pair_id}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
            def do_POST(self):
                if self.path not in ['/v1/messages','/messages']:self.send_error(404);return
                active=root/'active'/pair_id
                if not (active/'logs/runner-config.json').is_file():self.send_error(503,'No active attempt');return
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=128*1024*1024:self.send_error(413);return
                body=self.rfile.read(n);dest=active/'logs/http-audit'
                with lock:
                    dest.mkdir(parents=True,exist_ok=True)
                    ids=[int(q.stem.split('.')[0]) for q in dest.glob('*.json.gz')]
                    index=max(ids,default=0)+1;name=f'{index:03d}.json.gz'
                    (dest/name).write_bytes(gzip.compress(body,mtime=0))
                    request={'at_utc':datetime.now(timezone.utc).isoformat(),'index':index,'body_file':name,'body_sha256':hashlib.sha256(body).hexdigest(),'body_bytes':len(body),'path':self.path}
                    with (dest/'requests.jsonl').open('a') as stream:stream.write(json.dumps(request)+'\n')
                started=time.monotonic();session=requests.Session();session.trust_env=False;status=None;error=None;headers_sent=False
                try:
                    with session.post(a.upstream.rstrip('/')+'/v1/messages',data=body,headers={'Content-Type':'application/json','x-api-key':'local-vllm-no-secret','anthropic-version':self.headers.get('anthropic-version','2023-06-01')},stream=True,timeout=(15,None)) as response:
                        status=response.status_code;self.send_response(status)
                        self.send_header('Content-Type',response.headers.get('Content-Type','text/event-stream'))
                        self.send_header('Cache-Control','no-cache');self.end_headers();headers_sent=True
                        # Small raw chunks preserve SSE latency and complete event bytes.
                        for chunk in response.iter_content(chunk_size=1024):
                            if chunk:self.wfile.write(chunk);self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError):error='client_disconnected'
                except Exception as exc:
                    error=type(exc).__name__+': '+str(exc)
                    if not headers_sent:self.send_error(502,'Upstream failed')
                finally:
                    session.close();self.close_connection=True
                    with lock:
                        with (dest/'responses.jsonl').open('a') as stream:stream.write(json.dumps({'index':index,'finished_utc':datetime.now(timezone.utc).isoformat(),'http_status':status,'elapsed_seconds':time.monotonic()-started,'error':error})+'\n')
        return Handler
    for i in range(1,a.pairs+1):
        pair=f'pair{i:02d}';server=ThreadingHTTPServer((a.host,a.base_port+i),handler_for(pair));servers.append(server)
        threading.Thread(target=server.serve_forever,daemon=True).start()
    print(json.dumps({'ready':True,'host':a.host,'ports':[a.base_port+i for i in range(1,a.pairs+1)],'upstream':a.upstream}),flush=True)
    try:threading.Event().wait()
    except KeyboardInterrupt:pass
    finally:
        for server in servers:server.shutdown();server.server_close()

if __name__=='__main__':main()
