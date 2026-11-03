#!/usr/bin/env python3
"""Local SQLite leases for the new 1K campaign; unchanged native Pi task loop."""
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime,timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import sqlite3
import threading
import time
import uuid

import requests
import worker_core as core

LEASE=180
def now():return time.time()
def stamp():return datetime.now(timezone.utc).isoformat()
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def log(event,**kw):print(json.dumps({'at_utc':stamp(),'event':event,**kw},ensure_ascii=False),flush=True)
def connect(path):
    c=sqlite3.connect(str(path),timeout=30,isolation_level=None);c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=30000');return c

def initialize(path,tasks,release,target=None,campaign='ppt-rl'):
    target=len(tasks) if target is None else target
    assert len(tasks)==target and len({t['id'] for t in tasks})==target
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,ordinal INTEGER NOT NULL,material_id TEXT NOT NULL,
            title TEXT NOT NULL,language TEXT NOT NULL,domain TEXT NOT NULL,input_key TEXT NOT NULL,input_sha TEXT NOT NULL,
            plan_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ready',attempts INTEGER NOT NULL DEFAULT 0,
            worker_id TEXT,lease_token TEXT,lease_until REAL,started_at REAL,finished_at REAL,
            progress_json TEXT NOT NULL DEFAULT '{}',result_json TEXT,updated_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS task_status_order ON tasks(status,ordinal);
        CREATE TABLE IF NOT EXISTS attempts(task_id TEXT NOT NULL,attempt INTEGER NOT NULL,worker_id TEXT NOT NULL,
            lease_token TEXT NOT NULL,status TEXT NOT NULL,started_at REAL NOT NULL,finished_at REAL,result_json TEXT,
            PRIMARY KEY(task_id,attempt));
        CREATE TABLE IF NOT EXISTS workers(id TEXT PRIMARY KEY,model_id TEXT NOT NULL,pool TEXT NOT NULL,
            state TEXT NOT NULL,last_seen REAL NOT NULL,metadata_json TEXT NOT NULL);
        ''')
        old=db.execute("SELECT value FROM metadata WHERE key='release'").fetchone()
        if old and old[0]!=release:raise RuntimeError('Existing queue pins another code release')
        db.execute("INSERT OR IGNORE INTO metadata VALUES('release',?)",(release,))
        db.execute("INSERT OR IGNORE INTO metadata VALUES('target',?)",(str(target),))
        db.execute("INSERT OR IGNORE INTO metadata VALUES('campaign',?)",(campaign,))
        for t in tasks:
            db.execute('INSERT OR IGNORE INTO tasks(id,ordinal,material_id,title,language,domain,input_key,input_sha,plan_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (t['id'],t['ordinal'],t['material_id'],t['title'],t['language'],t['domain'],t['input_key'],t['input_sha'],canonical(t['plan']),now()))
            existing=db.execute('SELECT input_sha,plan_json FROM tasks WHERE id=?',(t['id'],)).fetchone()
            if existing['input_sha']!=t['input_sha'] or existing['plan_json']!=canonical(t['plan']):raise RuntimeError('Existing task input differs; do not rebuild queue')
        assert db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]==target

def register(path,worker,pool,model,metadata):
    with connect(path) as db:
        db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET model_id=excluded.model_id,pool=excluded.pool,last_seen=excluded.last_seen,metadata_json=excluded.metadata_json',
                   (worker,model,pool,'idle',now(),canonical(metadata)))

def claim(path,worker,claim_id=None):
    token=claim_id or uuid.uuid4().hex
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        expired=db.execute("SELECT id,attempts FROM tasks WHERE status='running' AND lease_until<?",(now(),)).fetchall()
        for r in expired:
            db.execute("UPDATE attempts SET status='lease_expired',finished_at=? WHERE task_id=? AND attempt=? AND status='running'",(now(),r['id'],r['attempts']))
            db.execute("UPDATE tasks SET status='ready',worker_id=NULL,lease_token=NULL,lease_until=NULL,progress_json='{}',updated_at=? WHERE id=?",(now(),r['id']))
        existing=db.execute("SELECT * FROM tasks WHERE status='running' AND worker_id=?",(worker,)).fetchone()
        if existing:
            db.commit()
            if existing['lease_token']!=token:raise core.LeaseLost('Worker slot already owns another lease')
            return {**dict(existing),'campaign_id':db.execute("SELECT value FROM metadata WHERE key='campaign'").fetchone()[0]}
        row=db.execute("SELECT * FROM tasks WHERE status='ready' ORDER BY ordinal LIMIT 1").fetchone()
        if row is None:
            db.execute("UPDATE workers SET state='idle',last_seen=? WHERE id=?",(now(),worker));db.commit();return None
        t=dict(row);t.update(worker_id=worker,lease_token=token,lease_until=now()+LEASE,attempts=t['attempts']+1,started_at=now())
        db.execute("UPDATE tasks SET status='running',worker_id=?,lease_token=?,lease_until=?,attempts=?,started_at=?,finished_at=NULL,result_json=NULL,progress_json='{}',updated_at=? WHERE id=?",
            (worker,token,t['lease_until'],t['attempts'],t['started_at'],now(),t['id']))
        db.execute('INSERT INTO attempts(task_id,attempt,worker_id,lease_token,status,started_at) VALUES(?,?,?,?,?,?)',
                   (t['id'],t['attempts'],worker,token,'running',t['started_at']))
        db.execute("UPDATE workers SET state='running',last_seen=? WHERE id=?",(now(),worker));db.commit()
        t['status']='running';t['campaign_id']='ppt-materials-1k-20261031';return t

class LocalClient:
    def __init__(self,path,packages,worker):self.path=path;self.packages=Path(packages);self.worker=worker
    def download(self,key,sha,cache):
        if key!='inputs/'+sha+'.tar.gz':raise ValueError('Invalid local material key')
        p=self.packages/(sha+'.tar.gz')
        if not p.is_file() or core.digest(p)!=sha:raise ValueError('Local material bundle SHA256 mismatch')
        return p
    def call(self,path,body=None,retries=2):
        with connect(self.path) as db:
            if path=='heartbeat':
                if body.get('task_id'):
                    changed=db.execute("UPDATE tasks SET lease_until=?,progress_json=?,updated_at=? WHERE id=? AND status='running' AND worker_id=? AND lease_token=? AND lease_until>?",
                        (now()+LEASE,canonical(body.get('progress',{})),now(),body['task_id'],body['worker_id'],body['lease_token'],now())).rowcount
                    if changed!=1:raise core.LeaseLost('Local lease is no longer owned')
                db.execute('UPDATE workers SET state=?,last_seen=? WHERE id=?',(body.get('worker_state') or ('running' if body.get('task_id') else 'idle'),now(),self.worker))
                return {'ok':True}
            if path!='complete':raise ValueError('Unsupported local protocol call')
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM tasks WHERE id=?',(body['task_id'],)).fetchone();result=body['result'];serialized=canonical(result)
            if row is None or row['worker_id']!=body['worker_id'] or row['lease_token']!=body['lease_token']:
                db.rollback();raise core.LeaseLost('Stale local completion rejected')
            if row['status'] in ['succeeded','failed']:
                db.commit()
                if row['result_json']!=serialized:raise core.LeaseLost('Idempotent completion payload differs')
                return {'ok':True,'idempotent':True}
            if row['status']!='running' or row['lease_until']<=now():db.rollback();raise core.LeaseLost('Lease expired before local completion')
            if result['status']=='succeeded' and not (result.get('agent_state')=='completed' and result.get('submission_ready') and result.get('pptx_sha256') and result.get('trace_manifest_sha256')):
                db.rollback();raise ValueError('Success lacks PPTX and trace evidence')
            db.execute('UPDATE tasks SET status=?,result_json=?,finished_at=?,updated_at=?,lease_until=NULL WHERE id=?',
                       (result['status'],serialized,now(),now(),row['id']))
            db.execute('UPDATE attempts SET status=?,result_json=?,finished_at=? WHERE task_id=? AND attempt=?',
                       (result['status'],serialized,now(),row['id'],row['attempts']))
            db.execute("UPDATE workers SET state='idle',last_seen=? WHERE id=?",(now(),self.worker));db.commit()
            return {'ok':True,'idempotent':False}

def summary(path):
    with connect(path) as db:
        counts=dict(db.execute('SELECT status,COUNT(*) FROM tasks GROUP BY status').fetchall())
        workers=[dict(r) for r in db.execute('SELECT id,model_id,pool,state,last_seen FROM workers ORDER BY id')]
        running=[dict(r) for r in db.execute("SELECT id,worker_id,attempts,progress_json,started_at FROM tasks WHERE status='running'")]
        per_model=[dict(r) for r in db.execute("SELECT workers.model_id,tasks.status,COUNT(*) n FROM tasks JOIN workers ON tasks.worker_id=workers.id GROUP BY workers.model_id,tasks.status")]
    return {'at_utc':stamp(),'counts':{'ready':0,'running':0,'failed':0,'succeeded':0,**counts},'workers':workers,'running':running,'per_model':per_model}

def run(config):
    core.validate_code()
    probe=json.loads((Path(config['operator_probe_directory'])/'verification.json').read_text())
    if not probe['passed'] or probe['teacher_model']!=config['model_id']:raise RuntimeError('Expected model/tool/vision probe has not passed')
    os.environ.update(PB_MODEL_ID=config['model_id'],PPT_CAMPAIGN_ID=config['campaign'],PPT_TEACHER_DEPLOYMENT=canonical({k:config[k] for k in ['teacher_model','backend','precision','quantization','gpus','backend_context','pi_context_window']}))
    options=argparse.Namespace(work_dir=Path(config['outputs_root']),machine=config['pool'],release=config['release_id'],
        model_url=config['local_model_gateway'],node=config.get('node') or shutil.which('node'))
    options.work_dir.mkdir(parents=True,exist_ok=True)
    gate=threading.Lock();paused=False;fast_failures=0;resume=Path(config['runtime_directory'])/'resume-claims'
    def health():
        session=requests.Session();session.trust_env=False
        try:
            url=config['model_upstream']+('/v1/status' if 'flash' in config['model_id'] else '/health')
            r=session.get(url,timeout=(3,8));r.raise_for_status()
            if 'flash' in config['model_id'] and not r.json().get('loaded'):return False
            m=session.get(config['local_model_gateway']+'/v1/models',timeout=(3,8));m.raise_for_status()
            return config['model_id'] in [x['id'] for x in m.json()['data']]
        except (requests.RequestException,ValueError):return False
        finally:session.close()
    if not health():raise RuntimeError('Actual model endpoint is not healthy or model ID differs')
    for signum in [signal.SIGTERM,signal.SIGINT]:signal.signal(signum,lambda *_:core.STOP.set())
    def slot(index):
        nonlocal paused,fast_failures
        worker=config['pool']+':'+str(index);register(config['queue_db'],worker,config['pool'],config['model_id'],config)
        client=LocalClient(config['queue_db'],config['packages_directory'],worker)
        for p in (options.work_dir/config['pool']/('slot-'+str(index))).glob('*/attempt-*/pending-completion.json'):
            try:client.call('complete',core.read(p));p.rename(p.with_name('committed-completion.json'))
            except core.LeaseLost:p.rename(p.with_name('stale-completion.json'))
        while not core.STOP.is_set():
            with gate:
                if resume.exists():resume.unlink();paused=False;fast_failures=0
                waiting=paused
            if waiting or not health():
                client.call('heartbeat',{'worker_id':worker,'worker_state':'paused_model' if waiting else 'waiting_model'});core.STOP.wait(15);continue
            if __import__('shutil').disk_usage(options.work_dir).free<10*1024**3:
                log('waiting_for_disk',worker=worker,minimum_free_gib=10);core.STOP.wait(30);continue
            try:task=claim(config['queue_db'],worker)
            except core.LeaseLost:
                log('occupied_slot_wait',worker=worker);core.STOP.wait(15);continue
            if task is None:
                s=summary(config['queue_db'])
                if not s['counts']['ready'] and not s['counts']['running']:break
                core.STOP.wait(5);continue
            log('claimed',worker=worker,task_id=task['id'],attempt=task['attempts'],teacher_model=config['model_id'])
            try:core.run_task(client,options,worker,task)
            except Exception as e:log('worker_error',worker=worker,error_type=type(e).__name__);core.STOP.wait(10);continue
            attempt=options.work_dir/config['pool']/('slot-'+str(index))/task['material_id']/('attempt-%04d'%task['attempts'])
            result=core.read(attempt/'result.json',{})
            with gate:
                fast=result.get('status')=='failed' and result.get('tool_calls',0)==0 and result.get('elapsed_seconds',999)<60
                fast_failures=fast_failures+1 if fast else 0
                if fast_failures>=2:
                    paused=True;log('claims_paused_after_fast_failures',pool=config['pool'],resume_file=str(resume))
        with connect(config['queue_db']) as db:db.execute("UPDATE workers SET state='stopped',last_seen=? WHERE id=?",(now(),worker))
    with ThreadPoolExecutor(max_workers=config['slots']) as executor:
        for f in [executor.submit(slot,i) for i in range(config['slots'])]:f.result()

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('init');a.add_argument('--tasks',type=Path,required=True);a.add_argument('--db',type=Path,required=True);a.add_argument('--release',required=True);a.add_argument('--campaign',default='ppt-rl')
    a=sub.add_parser('run');a.add_argument('--config',type=Path,required=True)
    a=sub.add_parser('status');a.add_argument('--db',type=Path,required=True)
    a=sub.add_parser('retry');a.add_argument('--db',type=Path,required=True);a.add_argument('--task-id',action='append',required=True)
    args=p.parse_args()
    if args.command=='init':initialize(args.db,json.loads(args.tasks.read_text())['tasks'],args.release,campaign=args.campaign);log('queue_initialized',**summary(args.db)['counts'])
    elif args.command=='run':run(json.loads(args.config.read_text()))
    elif args.command=='status':print(json.dumps(summary(args.db),ensure_ascii=False))
    else:
        with connect(args.db) as db:
            for task in args.task_id:
                n=db.execute("UPDATE tasks SET status='ready',worker_id=NULL,lease_token=NULL,lease_until=NULL,progress_json='{}',updated_at=? WHERE id=? AND status='failed'",(now(),task)).rowcount
                if n!=1:raise RuntimeError('Only a currently failed task may be retried: '+task)
                log('retry_requested',task_id=task)

if __name__=='__main__':main()
