from ppt_rl.matching import graph_views,locate_evidence,normalize_pages
from ppt_rl.aesthetics import make_groups,FlashAestheticJudge
from pathlib import Path
import hashlib,json

def test_sparse_many_to_many_becomes_one_disjoint_component():
    raw={'matches':[{'a_page':1,'b_page':1},{'a_page':2,'b_page':2},{'a_page':2,'b_page':3},{'a_page':3,'b_page':3}],
         'unmatched_a':[{'page':4,'reason':'Unique A material'}],'unmatched_b':[{'page':4,'reason':'Unique B material'}]}
    groups=make_groups(raw)
    assert len(groups)==2
    assert groups[1]['a_pages']==[2,3] and groups[1]['b_pages']==[2,3]
    assert len(groups[1]['source_links'])==3
    assert len([p for g in groups for p in g['a_pages']])==len({p for g in groups for p in g['a_pages']})

def test_evidence_is_contiguous_not_fuzzy():
    assert locate_evidence('work is heat','work is not heat') is None
    found=locate_evidence('“internal energy”','Some "internal energy" here')
    assert found['text']=='"internal energy"' and found['verification']=='typographic_quotes'

def test_flip_normalizes_winner_but_preserves_reason_labels(tmp_path,monkeypatch):
    path=tmp_path/'sample.png';path.write_bytes(b'PNG-test-input');h=hashlib.sha256(path.read_bytes()).hexdigest()
    images=[{'path':str(path),'page':1,'sha256':h,'width':1,'height':1}]
    raw={'content':[{'type':'text','text':json.dumps({'winner':'A','reason':'Group A looks better overall.'})}],'stop_reason':'end_turn','usage':{}}
    class Response:
        text=json.dumps(raw)
        def raise_for_status(self):pass
        def json(self):return raw
    judge=FlashAestheticJudge(thinking_budget=0);monkeypatch.setattr(judge.http,'post',lambda *args,**kwargs:Response())
    result=judge.compare(images,images,tmp_path/'judge','BA')
    assert result['displayed_winner']=='A' and result['winner']=='B'
    assert result['reason']=='Group A looks better overall.'
