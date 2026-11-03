from ppt_rl.batch import aggregate_runs
from ppt_rl.tournament import pin_selection
from ppt_rl.gate import FlashGate
import pytest

def test_partial_aggregate_counts_only_complete_rankings():
    ranking=[{'participant':f'r{i:02d}','rank_min':i,'rank_max':i,'reward':(8-i)/7} for i in range(1,9)]
    result=aggregate_runs([{'id':'done','status':'complete','ranking':ranking},{'id':'future','status':'in_progress','ranking':[]}])
    assert result['completed_draws']==1 and result['total_draws']==2 and not result['pairwise']
    assert all(x['observations']==1 and x['reward_population_std']==0 for x in result['participants'])

def test_frozen_selection_not_overwritten_on_mismatch(tmp_path):
    pin_selection(tmp_path,{'participants':['original']})
    before=(tmp_path/'selection.json').read_bytes()
    with pytest.raises(ValueError):pin_selection(tmp_path,{'participants':['other']})
    assert (tmp_path/'selection.json').read_bytes()==before

def test_zero_lease_limit_is_rejected(tmp_path):
    with pytest.raises(ValueError):FlashGate(tmp_path,0)
