from __future__ import annotations
from pathlib import Path
import gzip,hashlib,json,os,uuid

def read_json(path):
    path=Path(path);raw=path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix=='.gz' else raw)

def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temp.replace(path)

def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda:stream.read(1024*1024),b''):h.update(part)
    return h.hexdigest()

def workspace_environment(work):
    """Route caches outside HOME without changing the user's HOME variable."""
    work=Path(work).resolve();cache=work/'scratch/cache';cache.mkdir(parents=True,exist_ok=True)
    env={k:v for k,v in os.environ.items() if not k.startswith(('HF_TOKEN','HUGGING_FACE_HUB_TOKEN','GITHUB_TOKEN','GH_TOKEN','OAI_SITES_','PB_COORD_','ANTHROPIC_','OPENAI_','CLAUDE_','PI_')) and k not in {'SSH_AUTH_SOCK','SSH_AGENT_PID','SSH_ASKPASS','GIT_ASKPASS','GIT_SSH_COMMAND'}}
    env.update(TMPDIR=str(work/'scratch'),XDG_CACHE_HOME=str(cache),MPLCONFIGDIR=str(cache/'matplotlib'),PYTHONPYCACHEPREFIX=str(cache/'pycache'),PYTHONDONTWRITEBYTECODE='1')
    return env
