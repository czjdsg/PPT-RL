from itertools import product
import pytest
from ppt_rl.bracket import BRACKET,ready_matches,standings,group_vote

def test_all_binary_outcomes_are_valid_no_reset_brackets():
    slots=[f'r{x:02d}' for x in range(1,9)]
    for choices in product([False,True],repeat=14):
        completed={}
        for bit in choices:
            match=ready_matches(slots,completed)[0]
            a,b=match['left_player'],match['right_player'];winner,loser=(b,a) if bit else (a,b)
            completed[match['id']]={**match,'winner':winner,'loser':loser}
        rows=standings(slots,completed)
        assert len(rows)==8 and sum(r['losses'] for r in rows)==14
        assert sorted(r['rank_min'] for r in rows)==[1,2,3,4,5,5,7,7]
        assert all(r['losses']==2 for r in rows if r['rank_min']>=3)

def test_group_ties_do_not_add_half_wins():
    vote=group_vote([{'winner':v} for v in ['A','A','B','tie']])
    assert (vote['wins_a'],vote['wins_b'],vote['ties'],vote['winner'])==(2,1,1,'A')

def test_incomplete_ranking_rejected():
    with pytest.raises(ValueError):standings(list('abcdefgh'),{})
