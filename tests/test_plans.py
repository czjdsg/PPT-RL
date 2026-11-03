from itertools import combinations
from ppt_rl.plans import complete_plan,first_pairs,perfect_matchings
import pytest

IDS=[f'r{x:02d}' for x in range(1,9)]
FIXED=[['r04','r01','r03','r05','r06','r07','r08','r02'],['r06','r02','r05','r01','r08','r07','r03','r04']]

def test_completion_preserves_fixed_slots_and_covers_all_pairs():
    plan=complete_plan(IDS,FIXED)
    assert plan[:2]==FIXED and len(plan)==7
    pairs=[p for slots in plan for p in first_pairs(slots)]
    assert len(pairs)==len(set(pairs))==28 and set(pairs)==set(combinations(IDS,2))

def test_105_partitions_60_derangements_33_after_current_two():
    all_plans=list(perfect_matchings(IDS));a=set(first_pairs(FIXED[0]));b=set(first_pairs(FIXED[1]))
    assert len(all_plans)==105
    assert sum(not set(m)&a for m in all_plans)==60
    assert sum(not set(m)&(a|b) for m in all_plans)==33

def test_duplicate_first_round_pair_rejected():
    with pytest.raises(ValueError):complete_plan(IDS,[FIXED[0],FIXED[0]])
