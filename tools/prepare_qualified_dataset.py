#!/usr/bin/env python3
"""Package the frozen qualified train set; change only attachment file paths."""
from __future__ import annotations
import sys
sys.dont_write_bytecode=True
from pathlib import Path
from collections import Counter
import argparse,gzip,hashlib,json,re,shutil,datetime

SECRET=re.compile(rb'(?<![A-Za-z0-9_-])(?:hf_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9._-]{24,})')
def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def dump(path,data):Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def gzopen(path):return gzip.GzipFile(filename='',mode='wb',fileobj=Path(path).open('wb'),mtime=0,compresslevel=6)

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--audit-dir',type=Path);a=p.parse_args()
    source=a.source.resolve();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True);(out/'images').mkdir(exist_ok=True);(out/'provenance').mkdir(exist_ok=True)
    frozen=json.loads((source/'dataset-manifest.json').read_text())
    assert frozen['retained_cases']==2497 and frozen['retained_windows']==4325 and frozen['heldout_cases']==0
    for path,record in frozen['files'].items():assert digest(path)==record['sha256'],'Frozen input changed: '+path
    cases=[json.loads(x) for x in (source/'cases.jsonl').read_text().splitlines()]
    assert len(cases)==len({x['task_id'] for x in cases})==2497
    case_ids={x['task_id'] for x in cases};teachers=Counter(x['teacher_model'] for x in cases);domains=Counter(x['domain'] for x in cases)
    paths={};ids=set();masked=images_n=windows=0;normalized_hash=hashlib.sha256()
    target=out/'train.jsonl.gz'
    with gzopen(target) as stream,(source/'train.jsonl').open('rb') as rows:
        for raw in rows:
            if SECRET.search(raw):raise ValueError('Credential-like text requires review in training window '+str(windows))
            original=json.loads(raw);row=dict(original);assert row['id'] not in ids;ids.add(row['id']);windows+=1
            normalized=[]
            for name in row.get('images',[]):
                if name not in paths:
                    file=Path(name).resolve();sha=digest(file);relative='images/'+sha[:2]+'/'+sha+file.suffix.lower();dest=out/relative;dest.parent.mkdir(parents=True,exist_ok=True)
                    if dest.exists():assert digest(dest)==sha
                    else:shutil.copyfile(file,dest)
                    paths[name]={'source_path':name,'path':relative,'sha256':sha,'bytes':file.stat().st_size}
                normalized.append(paths[name]['path']);images_n+=1
            row['images']=normalized
            assert row['messages']==original['messages'] and row['tools']==original['tools']
            masked+=sum(m.get('loss')==0 for m in row['messages'])
            encoded=(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n').encode();stream.write(encoded);normalized_hash.update(encoded)
    assert windows==4325 and len(paths)==3373 and masked==2027
    source_ids=set()
    with gzopen(out/'sources.jsonl.gz') as stream:
        for raw in (source/'sources.jsonl').open('rb'):
            row=json.loads(raw);assert row['task_id'] in case_ids;source_ids.add(row['id'])
            # Source metadata remains original; the explicit image-map records portability.
            stream.write(raw)
    assert source_ids==ids
    for name in ['cases.jsonl','token-lengths.jsonl']:
        with gzopen(out/(name+'.gz')) as stream:stream.write((source/name).read_bytes())
    with (out/'image-map.jsonl').open('w') as stream:
        for _,row in sorted(paths.items()):stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    for name in ['dataset-manifest.json']:
        shutil.copyfile(source/name,out/'provenance'/('original-'+name))
    if a.audit_dir:
        for name in ['audit-report.json','export-validation.json','export-stage.json','domain-distribution.json','excluded-cases.json','reply-masks.json']:
            file=a.audit_dir/name
            if file.is_file():shutil.copyfile(file,out/'provenance'/name)
    checks={'cases':len(cases),'windows':windows,'image_occurrences':images_n,'unique_images':len(paths),'masked_message_occurrences':masked,
        'messages_and_tools_unchanged':True,'only_training_images_field_rewritten':True,'all_retained_samples_in_train':True,'heldout_cases':0,
        'normalized_uncompressed_train_sha256':normalized_hash.hexdigest(),'source_train_sha256':digest(source/'train.jsonl'),'teacher_counts':dict(teachers),'domain_counts':dict(domains)}
    dump(out/'validation.json',checks)
    files=[{'path':f.relative_to(out).as_posix(),'bytes':f.stat().st_size,'sha256':digest(f)} for f in sorted(out.rglob('*')) if f.is_file() and f.name!='manifest.json']
    manifest={'format':'ppt-rl-qualified-sft-v1','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source_snapshot_date':'2026-11-01',
        'counts':checks,'image_path_policy':'paths in train.images are relative to this release root; prepare-local rewrites them to a local snapshot directory.',
        'loss_policy':'Every original message loss flag, message text, tool schema and sample ID is retained. No additional filtering or train/validation split.',
        'selection_policy':'The previously reviewed 2,497 cases after 108 whole-case exclusions and two reply-level masks; not new rollout or evaluation data.',
        'files':files}
    dump(out/'manifest.json',manifest)
    print(json.dumps({'status':'ready',**checks,'release_files':len(files)+1,'release_bytes':sum(x['bytes'] for x in files)},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
