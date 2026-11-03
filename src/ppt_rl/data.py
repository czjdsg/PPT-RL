"""Materialize the qualified SFT release without changing messages or loss masks."""
from pathlib import Path
import gzip,json,os
from .io import read_json,sha256,write_json

def prepare_local(root,out=None,verify=True):
    root=Path(root).resolve();manifest=read_json(root/'manifest.json')
    if verify:
        for entry in manifest['files']:
            path=(root/entry['path']).resolve()
            if not path.is_relative_to(root) or not path.is_file() or sha256(path)!=entry['sha256']:raise ValueError('Release integrity failure: '+entry['path'])
    out=Path(out or root/'train-local.jsonl').resolve();count=0
    source=root/'train.jsonl.gz'
    with gzip.open(source,'rt',encoding='utf-8') as rows,out.open('w',encoding='utf-8') as stream:
        for line in rows:
            row=json.loads(line)
            images=[]
            for name in row.get('images',[]):
                path=(root/name).resolve()
                if not path.is_relative_to(root) or not path.is_file():raise ValueError('Missing local image: '+name)
                images.append(str(path))
            row['images']=images;stream.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n');count+=1
    if count!=manifest['counts']['windows']:raise ValueError('Window count differs from the frozen release')
    return {'train_file':str(out),'windows':count,'message_text_tools_and_loss_flags_modified':False}

def fetch_qualified(out,repo='yzxjb/ppt-sft',revision=None,prefix='qualified/20261101-v1',token=None):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    cache=out/'cache';cache.mkdir(exist_ok=True)
    os.environ.setdefault('HF_HOME',str(cache/'hf'));os.environ.setdefault('HF_HUB_CACHE',str(cache/'hf/hub'))
    os.environ.setdefault('HF_XET_CACHE',str(cache/'hf/xet'));os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY','1')
    from huggingface_hub import snapshot_download
    snapshot=snapshot_download(repo_id=repo,repo_type='dataset',revision=revision,token=token,allow_patterns=[prefix+'/**'],cache_dir=str(cache/'hub'),local_dir=str(out/'snapshot'))
    return prepare_local(Path(snapshot)/prefix)
