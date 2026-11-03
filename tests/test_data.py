from pathlib import Path
import gzip,json,hashlib
from ppt_rl.data import prepare_local
from ppt_rl.io import workspace_environment
from ppt_rl.metrics import mathematical_fixtures

def test_materialization_changes_attachments_not_supervision(tmp_path):
    image=tmp_path/'images/a.png';image.parent.mkdir();image.write_bytes(b'example-image')
    original={'id':'one','images':['images/a.png'],'tools':[],'messages':[{'role':'assistant','content':'literal \ue000PPT_LITERAL_IMAGE_TAG\ue001','loss':0}]}
    with gzip.open(tmp_path/'train.jsonl.gz','wt') as f:f.write(json.dumps(original)+'\n')
    (tmp_path/'manifest.json').write_text(json.dumps({'counts':{'windows':1},'files':[{'path':'images/a.png','sha256':hashlib.sha256(image.read_bytes()).hexdigest()}]}))
    result=prepare_local(tmp_path);row=json.loads(Path(result['train_file']).read_text())
    assert row['messages']==original['messages'] and row['tools']==original['tools']
    assert row['images']==[str(image)]

def test_agent_environment_does_not_inherit_upload_credentials(tmp_path,monkeypatch):
    monkeypatch.setenv('HF_TOKEN','test-secret');monkeypatch.setenv('GITHUB_TOKEN','test-secret');monkeypatch.setenv('GIT_ASKPASS','private-auth-helper')
    env=workspace_environment(tmp_path)
    assert all(x not in env for x in ['HF_TOKEN','GITHUB_TOKEN','GIT_ASKPASS'])
    assert env['TMPDIR'].startswith(str(tmp_path))

def test_independent_tau_fixtures():assert len(mathematical_fixtures())==4
