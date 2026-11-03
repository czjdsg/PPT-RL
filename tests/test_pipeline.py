from pathlib import Path
import json
import fitz
from pptx import Presentation
from ppt_rl import engine
from ppt_rl.tournament import execute
from ppt_rl.matching import graph_views,restore_evidence

def test_real_pptx_and_pdf_to_fourteen_match_rewards_without_model(tmp_path,monkeypatch):
    decks=[]
    for i in range(1,9):
        pid=f'r{i:02d}';pptx=tmp_path/(pid+'.pptx');pdf=tmp_path/(pid+'.pdf')
        p=Presentation()
        for title in ['Shared introduction','Shared main result']:
            slide=p.slides.add_slide(p.slide_layouts[6]);slide.shapes.add_textbox(0,0,1000000,1000000).text=title
        p.save(pptx)
        doc=fitz.open()
        for title in ['Shared introduction','Shared main result']:
            page=doc.new_page(width=160,height=90);page.insert_text((10,20),title)
        doc.save(pdf);doc.close();decks.append({'id':pid,'pptx':str(pptx),'pdf':str(pdf)})
    manifest=tmp_path/'decks.json';manifest.write_text(json.dumps({'decks':decks}))
    calls=[]
    def align(self,a,b,out):
        matches=[{'a_page':i,'b_page':i,'coverage':'equivalent','confidence':.9,'reason':'Same stage.',
                  'evidence_a':a[i-1]['text'],'evidence_b':b[i-1]['text']} for i in [1,2]]
        result=restore_evidence({'matches':matches,'unmatched_a':[],'unmatched_b':[],'notes':[]},a,b)
        result.update(graph_views(result,a,b));engine.atomic(out/'result.json',result);return result
    def judge(self,a,b,out,orientation='AB'):
        calls.append(str(out));winner='A' if a[0]['source_deck']>b[0]['source_deck'] else 'B'
        result={'winner':winner,'reason':'Synthetic comparator; not a recorded model judgment.'}
        engine.atomic(out/'result.json',result);return result
    monkeypatch.setattr(engine.FlashPageMatcher,'align',align);monkeypatch.setattr(engine.FlashAestheticJudge,'compare',judge)
    result=execute(manifest,tmp_path/'rank',{'concurrency':3},draw_seed=20261103)
    assert result['status']=='complete' and result['matches_completed']==14
    assert result['ranking'][0]['participant']=='r08'
    assert len(calls)==len(set(calls))==28
    assert all(Path(p).is_relative_to(tmp_path/'rank') for p in calls)
