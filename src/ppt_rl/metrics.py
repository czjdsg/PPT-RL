"""Independent rank comparison helpers; no model or controller access.

Kendall tau-b uses total pairs minus each ranking's tied-pair count, rather
than copying the runner's denominator in terms of one-sided tie categories.
"""
from collections import Counter
from itertools import combinations
import math


def midrank(row):
    return (row['rank_min'] + row['rank_max']) / 2


def kendall_tau_b(old, new):
    aa={row['participant']:midrank(row) for row in old}
    bb={row['participant']:midrank(row) for row in new}
    if set(aa)!=set(bb) or len(aa)!=len(old) or len(bb)!=len(new):
        raise ValueError('Rankings must contain exactly the same unique participants')
    counts=dict.fromkeys(['concordant','discordant','old_tie_only','new_tie_only','both_tied'],0)
    sign=lambda value:(value>0)-(value<0)
    for a,b in combinations(sorted(aa),2):
        x,y=sign(aa[a]-aa[b]),sign(bb[a]-bb[b])
        key='both_tied' if x==y==0 else 'old_tie_only' if x==0 else 'new_tie_only' if y==0 else 'concordant' if x==y else 'discordant'
        counts[key]+=1
    n=len(aa);all_pairs=n*(n-1)//2
    old_ties=sum(k*(k-1)//2 for k in Counter(aa.values()).values())
    new_ties=sum(k*(k-1)//2 for k in Counter(bb.values()).values())
    denominator=math.sqrt((all_pairs-old_ties)*(all_pairs-new_ties))
    tau=(counts['concordant']-counts['discordant'])/denominator if denominator else None
    return {'kendall_tau_b':tau,'pair_order_counts':counts,'total_participant_pairs':all_pairs,
            'old_tied_pairs':old_ties,'new_tied_pairs':new_ties,'denominator':denominator}


def compare_rankings(old,new):
    old_by={r['participant']:r for r in old};new_by={r['participant']:r for r in new}
    if set(old_by)!=set(new_by):raise ValueError('Participant sets differ')
    rows=[]
    for participant in sorted(old_by):
        before,after=old_by[participant],new_by[participant]
        rows.append({'participant':participant,'old_rank':[before['rank_min'],before['rank_max']],
            'new_rank':[after['rank_min'],after['rank_max']],'old_reward':before['reward'],
            'new_reward':after['reward'],'reward_delta':round(after['reward']-before['reward'],8),
            'midrank_shift':midrank(before)-midrank(after),'tier_changed':before['reward']!=after['reward']})
    old_champion=next(r['participant'] for r in old if r['rank_min']==r['rank_max']==1)
    new_champion=next(r['participant'] for r in new if r['rank_min']==r['rank_max']==1)
    top_old={r['participant'] for r in old if r['rank_max']<=2}
    top_new={r['participant'] for r in new if r['rank_max']<=2}
    return {'rows':rows,'same_champion':old_champion==new_champion,'top_two_overlap':len(top_old&top_new),
        'tier_changes':sum(r['tier_changed'] for r in rows),
        'mean_absolute_reward_change':sum(abs(r['reward_delta']) for r in rows)/len(rows),
        **kendall_tau_b(old,new)}


def mathematical_fixtures():
    def ranking(values):return [{'participant':str(i),'rank_min':v,'rank_max':v} for i,v in enumerate(values)]
    base=[1,2,3,4,5.5,5.5,7.5,7.5]
    fixtures=[('same_tied_ranking',base,base,1.),
              ('reverse_ordinal_ranking',base,[-x for x in base],-1.),
              ('swap_top_two_with_bottom_ties',base,[2,1,3,4,5.5,5.5,7.5,7.5],24/26),
              ('all_tied_is_undefined',[1]*8,[1]*8,None)]
    rows=[]
    for name,a,b,expected in fixtures:
        actual=kendall_tau_b(ranking(a),ranking(b))
        assert actual['kendall_tau_b'] is None if expected is None else math.isclose(actual['kendall_tau_b'],expected,abs_tol=1e-12)
        assert sum(actual['pair_order_counts'].values())==28
        rows.append({'name':name,'synthetic_math_fixture':True,'expected_tau_b':expected,'actual':actual})
    return rows
