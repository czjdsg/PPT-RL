#!/usr/bin/env python3
"""Judge one aligned slide group at a time using screenshots and a minimal prompt."""
from __future__ import annotations
import argparse
import base64
import collections
import csv
import datetime
import hashlib
import json
import os
import pathlib
import re
import subprocess
import time
import uuid

import fitz
from pptx import Presentation
import requests

from .matching import DEFAULT_ENDPOINT, DEFAULT_MODEL, graph_views, save, sha

PROMPT_VERSION='overall-aesthetics-v2-en'
AESTHETIC_PROMPT='''Compare the PPT page screenshots in Group A and Group B. Which group has the better overall aesthetic impression?
The number of pages in either group must not affect your judgment. Focus on overall visual appeal.
Return only a JSON object: winner must be "A", "B", or "tie", and reason must be one short sentence in English.'''

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()

def make_groups(alignment: dict, mode='components') -> list[dict]:
    """Transitively merge shared endpoints; never reuse a page across groups.

    Rebuild from explicit links instead of trusting cached component metadata.
    Sparse many-to-many groups retain their real links, without filling in a
    complete bipartite graph. Unmatched pages never enter a comparison group.
    """
    if mode!='components':
        raise ValueError('Only disjoint connected-component grouping is supported; overlapping stars/edges are obsolete')
    edges=[(e['a_page'],e['b_page']) for e in alignment['matches']]
    if len(edges)!=len(set(edges)):raise ValueError('Duplicate semantic page pair')
    pages={side:[{'page':p} for p in sorted({e[i] for e in edges}|{x['page'] for x in alignment.get('unmatched_'+side,[])})]
           for i,side in enumerate(['a','b'])}
    value={**alignment,'unmatched_a':alignment.get('unmatched_a',[]),'unmatched_b':alignment.get('unmatched_b',[])}
    return graph_views(value,pages['a'],pages['b'])['groups']

class SlideScreenshots:
    def __init__(self,pptx: str|pathlib.Path,out_dir: str|pathlib.Path,pdf: str|pathlib.Path|None=None,width=1600):
        self.pptx=pathlib.Path(pptx).resolve();self.root=pathlib.Path(out_dir);self.root.mkdir(parents=True,exist_ok=True)
        self.width=width;self.pptx_sha256=sha(self.pptx);self.pages=len(Presentation(self.pptx).slides)
        self.pdf=pathlib.Path(pdf).resolve() if pdf else self._convert()
        with fitz.open(self.pdf) as d:
            if len(d)!=self.pages:raise ValueError('PDF/PPTX page count mismatch')
        self.pdf_sha256=sha(self.pdf)
        self.images=self.root/'screenshots';self.images.mkdir(exist_ok=True)
        self.meta={'pptx':str(self.pptx),'pptx_sha256':self.pptx_sha256,'pdf':str(self.pdf),
            'pdf_sha256':self.pdf_sha256,'page_count':self.pages,'screenshot_width':width,
            'render_policy':'Full original PDF page; same width and aspect ratio; no crop, annotations, collage or aesthetic edits.'}
        old=self.root/'source.json'
        if old.exists() and json.load(open(old))!=self.meta:raise ValueError('Screenshot cache belongs to another source or resolution')
        save(old,self.meta)

    def _convert(self):
        render=self.root/'render';render.mkdir(exist_ok=True);pdf=render/'deck.pdf';manifest=render/'conversion.json'
        if pdf.exists() and manifest.exists() and json.load(open(manifest)).get('pptx_sha256')==self.pptx_sha256:return pdf
        name='ppt-aesthetic-'+uuid.uuid4().hex[:12]
        command=['docker','run','--rm','--name',name,'--network','none','--read-only','--user',f'{os.getuid()}:{os.getgid()}',
            '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','256','--cpus','2','--memory','2g',
            '--tmpfs','/tmp:rw,size=512m','-v',f'{self.pptx}:/input/deck.pptx:ro','-v',f'{render.resolve()}:/output',
            '-v','/usr/share/fonts:/usr/share/fonts/host:ro',os.environ.get('PPT_RL_RENDER_IMAGE','ppt-rl-render:local'),
            '-env:UserInstallation=file:///tmp/lo-profile','--headless','--convert-to','pdf','--outdir','/output','/input/deck.pptx']
        try:result=subprocess.run(command,capture_output=True,text=True,timeout=180)
        except subprocess.TimeoutExpired:
            subprocess.run(['docker','rm','-f',name],capture_output=True);raise
        (render/'conversion.log').write_text(result.stdout+result.stderr)
        if result.returncode or not pdf.exists():raise RuntimeError('PPTX conversion failed; see conversion.log')
        save(manifest,{'pptx_sha256':self.pptx_sha256,'pdf_sha256':sha(pdf),'renderer':os.environ.get('PPT_RL_RENDER_IMAGE','ppt-rl-render:local')})
        return pdf

    def page(self,page:int):
        if type(page) is not int or not 1<=page<=self.pages:raise ValueError('Page out of range')
        target=self.images/f'page-{page:03d}.png'
        if not target.exists():
            with fitz.open(self.pdf) as d:
                p=d[page-1];p.get_pixmap(matrix=fitz.Matrix(self.width/p.rect.width,self.width/p.rect.width),alpha=False).save(target)
        pix=fitz.Pixmap(str(target))
        return {'page':page,'path':str(target),'sha256':sha(target),'width':pix.width,'height':pix.height}

class FlashAestheticJudge:
    def __init__(self,endpoint=DEFAULT_ENDPOINT,model=DEFAULT_MODEL,temperature=.2,seed=20261102,thinking_budget=1024,max_tokens=4096,timeout=600):
        self.endpoint=endpoint.rstrip('/');self.model=model;self.temperature=temperature;self.seed=seed
        self.thinking_budget=thinking_budget;self.max_tokens=max_tokens;self.timeout=timeout
        self.http=requests.Session();self.http.trust_env=False

    def compare_pngs(self,a_paths,b_paths,out_dir,orientation='AB'):
        """Convenience API for already-rendered PNG screenshots, without PPTX input."""
        def records(paths):
            result=[]
            for i,path in enumerate(paths,1):
                path=pathlib.Path(path).resolve()
                if path.read_bytes()[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('compare_pngs requires PNG screenshots: '+str(path))
                pix=fitz.Pixmap(str(path));result.append({'page':i,'path':str(path),'sha256':sha(path),'width':pix.width,'height':pix.height})
            return result
        return self.compare(records(a_paths),records(b_paths),out_dir,orientation)

    def compare(self,a_images:list[dict],b_images:list[dict],out_dir:str|pathlib.Path,orientation='AB'):
        if not a_images or not b_images:raise ValueError('Both sides need at least one screenshot')
        if orientation not in ['AB','BA']:raise ValueError('orientation must be AB or BA')
        out=pathlib.Path(out_dir);out.mkdir(parents=True,exist_ok=True)
        inputs={'original_a_images':a_images,'original_b_images':b_images,'display_orientation':orientation,
            'prompt_version':PROMPT_VERSION,'prompt':AESTHETIC_PROMPT,'model':self.model,
            'temperature':self.temperature,'seed':self.seed,'thinking_budget':self.thinking_budget,'max_tokens':self.max_tokens}
        input_file=out/'input-manifest.json'
        if (out/'result.json').exists():
            if json.load(open(input_file))!=inputs:raise ValueError('Refuse reusing result with changed inputs/configuration')
            return json.load(open(out/'result.json'))
        if list(out.glob('attempt-*.request.json')):raise ValueError('Partial evaluation exists; preserve it and use a new output directory')
        save(input_file,inputs);(out/'prompt.txt').write_text(AESTHETIC_PROMPT)
        display=[('A',a_images),('B',b_images)] if orientation=='AB' else [('A',b_images),('B',a_images)]
        content=[];image_order=[]
        for label,images in display:
            content.append({'type':'text','text':'Group '+label+' screenshots:'})
            for image in images:
                path=pathlib.Path(image['path']);assert sha(path)==image['sha256']
                content.append({'type':'image','source':{'type':'base64','media_type':'image/png','data':base64.b64encode(path.read_bytes()).decode()}})
                image_order.append({'display_side':label,**image})
        messages=[{'role':'user','content':content}];attempts=[]
        for attempt in range(2):
            body={'model':self.model,'system':AESTHETIC_PROMPT,'messages':messages,
                'temperature':self.temperature,'top_p':.95,'top_k':20,'seed':self.seed,'max_tokens':self.max_tokens,
                'thinking':{'type':'enabled','budget_tokens':self.thinking_budget} if self.thinking_budget else {'type':'disabled'}}
            if self.thinking_budget:body['output_config']={'effort':'medium'}
            prefix=f'attempt-{attempt+1:02d}';save(out/(prefix+'.request.json'),body);started=time.monotonic()
            try:
                response=self.http.post(self.endpoint+'/v1/messages',json=body,timeout=(10,self.timeout))
            except requests.RequestException as exc:
                failure={'attempt':attempt+1,'elapsed_seconds':round(time.monotonic()-started,3),
                    'usage':None,'transport_error':type(exc).__name__,'validation_error':'Transport failed before a complete model response was recorded.'}
                attempts.append(failure);save(out/'attempts.json',attempts)
                save(out/'failure.json',{'created_utc':now(),**failure,'note':'No aesthetic winner was inferred or substituted.'})
                raise
            (out/(prefix+'.response.txt')).write_text(response.text);response.raise_for_status();raw=response.json();save(out/(prefix+'.response.json'),raw)
            text='\n'.join(b.get('text','') for b in raw.get('content',[]) if b.get('type')=='text');(out/(prefix+'.output.txt')).write_text(text)
            cleaned=text.strip()
            if cleaned.startswith('```'):cleaned=re.sub(r'^```(?:json)?\s*|\s*```$','',cleaned)
            value=None;error=None
            try:
                value=json.loads(cleaned)
                if not isinstance(value,dict) or value.get('winner') not in ['A','B','tie'] or not isinstance(value.get('reason'),str) or not value['reason'].strip():raise ValueError('Need winner=A/B/tie and a short reason')
            except (ValueError,TypeError) as exc:error=str(exc)
            attempts.append({'attempt':attempt+1,'elapsed_seconds':round(time.monotonic()-started,3),'usage':raw.get('usage'),
                'stop_reason':raw.get('stop_reason'),'validation_error':error});save(out/'attempts.json',attempts)
            if not error:
                original=value['winner'] if orientation=='AB' or value['winner']=='tie' else {'A':'B','B':'A'}[value['winner']]
                result={'winner':original,'displayed_winner':value['winner'],'reason':value['reason'],
                    'reason_labels':'A/B in the original model reason refer to displayed sides.',
                    'display_to_original':{'A':'A','B':'B'} if orientation=='AB' else {'A':'B','B':'A'},
                    'original_a_pages':[i['page'] for i in a_images],'original_b_pages':[i['page'] for i in b_images],
                    'n_a':len(a_images),'n_b':len(b_images),'orientation':orientation,
                    '_meta':{'created_utc':now(),'model':self.model,'prompt_version':PROMPT_VERSION,'input_manifest_sha256':sha(input_file),
                        'image_order':image_order,'attempts':attempts,'input_policy':'Only current paired screenshots and A/B labels; no page text, pairing rationale, rubric, scores or earlier judgments.'}}
                save(out/'result.json',result);return result
            messages=messages+[{'role':'assistant','content':text or '(No JSON returned.)'},
                {'role':'user','content':'Return only the required JSON object. winner must be A, B, or tie, and reason must be one short sentence in English.'}]
        raise ValueError('Invalid model output; both attempts preserved at '+str(out))

def resolve_source(alignment_file,side,explicit):
    file=alignment_file.parent/f'extracted-{side}.json'
    if not file.exists():
        if explicit:return pathlib.Path(explicit)
        raise ValueError('Provide --ppt-'+side+' when extracted source metadata is absent')
    source=json.load(open(file))
    native=pathlib.Path(source.get('source_file','')).suffix.lower()=='.pptx' and source.get('source_sha256')
    if not native and not explicit:raise ValueError('Text-only alignment has no PPTX source; provide --ppt-'+side)
    path=pathlib.Path(explicit) if explicit else pathlib.Path(source['source_file'])
    if native and sha(path)!=source['source_sha256']:raise ValueError('PPTX differs from the source used for semantic alignment')
    return path

def run_alignment(alignment_file,out_dir,ppt_a=None,ppt_b=None,pdf_a=None,pdf_b=None,grouping='components',prepare_only=False):
    alignment_file=pathlib.Path(alignment_file).resolve();out=pathlib.Path(out_dir).resolve();out.mkdir(parents=True,exist_ok=True)
    alignment=json.load(open(alignment_file));groups=make_groups(alignment,grouping)
    plan={'alignment_file':str(alignment_file),'alignment_sha256':sha(alignment_file),'grouping':grouping,'groups':groups,
        'unmatched_a_skipped':alignment.get('unmatched_a',[]),'unmatched_b_skipped':alignment.get('unmatched_b',[]),
        'group_note':'Disjoint connected components support one-to-one, one-to-many, many-to-one and many-to-many. Each page belongs to at most one group; unmatched pages are skipped. Only explicit semantic links are retained.'}
    if (out/'plan.json').exists() and json.load(open(out/'plan.json'))!=plan:raise ValueError('Existing output plan differs')
    save(out/'plan.json',plan);(out/'prompt.txt').write_text(AESTHETIC_PROMPT)
    sources={'a':SlideScreenshots(resolve_source(alignment_file,'a',ppt_a),out/'source-a',pdf_a),
             'b':SlideScreenshots(resolve_source(alignment_file,'b',ppt_b),out/'source-b',pdf_b)}
    assets={'a':{p:sources['a'].page(p) for p in sorted({p for g in groups for p in g['a_pages']})},
            'b':{p:sources['b'].page(p) for p in sorted({p for g in groups for p in g['b_pages']})}}
    save(out/'screenshots.json',{s:[v for _,v in sorted(m.items())] for s,m in assets.items()})
    if prepare_only:
        print(json.dumps({'prepared_groups':len(groups),'screenshots_a':len(assets['a']),'screenshots_b':len(assets['b'])}));return
    client=FlashAestheticJudge();model_list=client.http.get(client.endpoint+'/v1/models',timeout=15);model_list.raise_for_status()
    if client.model not in {m['id'] for m in model_list.json().get('data',[])}:raise ValueError('Expected Flash model is not served')
    rows=[]
    for g in groups:
        a=[assets['a'][p] for p in g['a_pages']];b=[assets['b'][p] for p in g['b_pages']]
        print(json.dumps({'event':'judge_start','group':g['id'],'a_pages':g['a_pages'],'b_pages':g['b_pages']}),flush=True)
        result=client.compare(a,b,out/'comparisons'/g['id']/'AB')
        row={**g,**result,'result_file':str(out/'comparisons'/g['id']/'AB/result.json')};rows.append(row);save(out/'results.json',rows)
        print(json.dumps({'event':'judge_done','group':g['id'],'winner':result['winner'],'reason':result['reason']},ensure_ascii=False),flush=True)
    with (out/'results.csv').open('w',newline='') as f:
        keys=['id','kind','a_pages','b_pages','n_a','n_b','winner','reason','result_file'];writer=csv.DictWriter(f,fieldnames=keys);writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(row[k]) if k in ['a_pages','b_pages'] else row.get(k) for k in keys})
    summary={'status':'complete','groups':len(rows),'winners':dict(collections.Counter(r['winner'] for r in rows)),
        'grouping':grouping,'prompt':AESTHETIC_PROMPT,'model':client.model,
        'note':'Groups do not share pages. Group winner counts are descriptive; no whole-deck aesthetic score is inferred.',
        'results':str(out/'results.json'),'csv':str(out/'results.csv')};save(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--alignment',required=True);p.add_argument('--out',required=True)
    p.add_argument('--ppt-a');p.add_argument('--ppt-b');p.add_argument('--pdf-a');p.add_argument('--pdf-b')
    p.add_argument('--grouping',choices=['components'],default='components');p.add_argument('--prepare-only',action='store_true')
    args=p.parse_args();run_alignment(args.alignment,args.out,args.ppt_a,args.ppt_b,args.pdf_a,args.pdf_b,args.grouping,args.prepare_only)

if __name__=='__main__':main()
