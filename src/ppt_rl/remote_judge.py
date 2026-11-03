from __future__ import annotations
import base64,json,re,time,os
from pathlib import Path
import requests
from .matching import save,sha,now
from .io import read_json as load
from .aesthetics import AESTHETIC_PROMPT,PROMPT_VERSION
BASE=os.environ.get('PPT_RL_OPENAI_BASE_URL','http://127.0.0.1:8000/v1')

class Judge:
    def __init__(self,model,key,base_url=None):
        self.model=model;self.key=key;self.base_url=(base_url or BASE).rstrip('/');self.http=requests.Session();self.http.trust_env=False
        self.parameters={'temperature':.2,'max_tokens':4096};self.adjustments=[]

    def compare(self,a_images,b_images,out,orientation='AB'):
        out.mkdir(parents=True,exist_ok=True)
        manifest={'requested_model':self.model,'endpoint':self.base_url,'prompt':AESTHETIC_PROMPT,'prompt_version':PROMPT_VERSION,
            'orientation':orientation,'original_a_images':a_images,'original_b_images':b_images}
        if (out/'result.json').exists():
            assert load(out/'input-manifest.json')==manifest
            return load(out/'result.json')
        if list(out.glob('attempt-*.request.json')):raise ValueError('Partial comparison already exists; preserve it before retrying')
        save(out/'input-manifest.json',manifest);(out/'prompt.txt').write_text(AESTHETIC_PROMPT)
        shown=[('A',a_images),('B',b_images)] if orientation=='AB' else [('A',b_images),('B',a_images)]
        content=[];image_order=[]
        for label,images in shown:
            content.append({'type':'text','text':'Group '+label+' screenshots:'})
            for image in images:
                path=Path(image['path']);assert sha(path)==image['sha256']
                content.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode()}})
                image_order.append({'display_side':label,**image})
        messages=[{'role':'system','content':AESTHETIC_PROMPT},{'role':'user','content':content}]
        attempts=[];format_repairs=0
        for attempt in range(1,5):
            body={'model':self.model,'messages':messages,**self.parameters}
            prefix=out/f'attempt-{attempt:02d}';save(prefix.with_suffix('.request.json'),body);started=time.monotonic()
            try:
                response=self.http.post(self.base_url+'/chat/completions',headers={'Authorization':'Bearer '+self.key},json=body,timeout=(30,600))
                text=response.text.replace(self.key,'[REDACTED]');prefix.with_suffix('.response.txt').write_text(text)
            except requests.RequestException as exc:
                save(out/'failure.json',{'created_utc':now(),'transport_error':type(exc).__name__,'elapsed_seconds':round(time.monotonic()-started,3)})
                raise RuntimeError('Transport failed before a complete model response was recorded') from None
            elapsed=round(time.monotonic()-started,3)
            try:raw=json.loads(text);save(prefix.with_suffix('.response.json'),raw)
            except ValueError:raw={}
            record={'attempt':attempt,'elapsed_seconds':elapsed,'status_code':response.status_code,'usage':raw.get('usage'),
                    'parameters':dict(self.parameters),'validation_error':None}
            if response.status_code>=400:
                record['validation_error']='HTTP '+str(response.status_code);attempts.append(record);save(out/'attempts.json',attempts)
                error=str(raw.get('error',text)).lower()
                if response.status_code==400 and 'temperature' in error and 'temperature' in self.parameters:
                    self.parameters.pop('temperature');self.adjustments.append('temperature omitted after an explicit API rejection');continue
                if response.status_code==400 and 'max_tokens' in error and 'max_tokens' in self.parameters:
                    self.parameters['max_completion_tokens']=self.parameters.pop('max_tokens');self.adjustments.append('max_tokens renamed to max_completion_tokens after an explicit API rejection');continue
                if response.status_code in [429,500,502,503,504] and attempt<4:time.sleep(2);continue
                save(out/'failure.json',{'created_utc':now(),'status_code':response.status_code,'response_file':prefix.name+'.response.txt'})
                raise RuntimeError('API returned HTTP '+str(response.status_code)+'; response preserved')
            try:
                answer=raw['choices'][0]['message']['content']
                if isinstance(answer,list):answer='\n'.join(x.get('text','') for x in answer if isinstance(x,dict))
                prefix.with_suffix('.output.txt').write_text(answer)
                value=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',answer.strip()))
                if not isinstance(value,dict) or value.get('winner') not in ['A','B','tie'] or not isinstance(value.get('reason'),str) or not value['reason'].strip():
                    raise ValueError('Expected winner=A/B/tie and a short English reason')
            except (ValueError,TypeError,KeyError) as exc:
                record['validation_error']=str(exc);attempts.append(record);save(out/'attempts.json',attempts);format_repairs+=1
                if format_repairs>1:raise RuntimeError('Invalid JSON after repair; all attempts preserved')
                messages+=[{'role':'assistant','content':locals().get('answer','') or '(No JSON returned.)'},
                    {'role':'user','content':'Return only a JSON object. winner must be A, B, or tie; reason must be one short sentence in English.'}]
                continue
            record['finish_reason']=raw['choices'][0].get('finish_reason');attempts.append(record);save(out/'attempts.json',attempts)
            displayed=value['winner'];mapping={'A':'A','B':'B'} if orientation=='AB' else {'A':'B','B':'A'}
            result={'winner':'tie' if displayed=='tie' else mapping[displayed],'displayed_winner':displayed,'reason':value['reason'],
                'orientation':orientation,'display_to_original':mapping,'original_a_pages':[x['page'] for x in a_images],
                'original_b_pages':[x['page'] for x in b_images],'n_a':len(a_images),'n_b':len(b_images),
                'reason_labels':'A/B in the original model reason refer to displayed input sides.',
                '_meta':{'created_utc':now(),'model':self.model,'response_model':raw.get('model'),
                    'prompt_version':PROMPT_VERSION,'input_manifest_sha256':sha(out/'input-manifest.json'),
                    'image_order':image_order,'attempts':attempts,'parameter_adjustments':self.adjustments,
                    'input_policy':'Only current paired screenshots and A/B labels; no page text, pairing rationale, rubric, scores or earlier judgments.'}}
            save(out/'result.json',result);return result
        raise RuntimeError('API attempts exhausted; all attempts preserved')
