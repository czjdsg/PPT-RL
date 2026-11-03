"""Pure eight-player double elimination: fourteen matches, single grand final."""
from __future__ import annotations

BRACKET = [
    {'id':'W1a','stage':'winners_round_1','left':'S1','right':'S2'},
    {'id':'W1b','stage':'winners_round_1','left':'S3','right':'S4'},
    {'id':'W1c','stage':'winners_round_1','left':'S5','right':'S6'},
    {'id':'W1d','stage':'winners_round_1','left':'S7','right':'S8'},
    {'id':'W2a','stage':'winners_round_2','left':'W:W1a','right':'W:W1b'},
    {'id':'W2b','stage':'winners_round_2','left':'W:W1c','right':'W:W1d'},
    {'id':'W3','stage':'winners_final','left':'W:W2a','right':'W:W2b'},
    {'id':'L1a','stage':'losers_round_1','left':'L:W1a','right':'L:W1b'},
    {'id':'L1b','stage':'losers_round_1','left':'L:W1c','right':'L:W1d'},
    {'id':'L2a','stage':'losers_round_2','left':'W:L1a','right':'L:W2b'},
    {'id':'L2b','stage':'losers_round_2','left':'W:L1b','right':'L:W2a'},
    {'id':'L3','stage':'losers_round_3','left':'W:L2a','right':'W:L2b'},
    {'id':'L4','stage':'losers_final','left':'W:L3','right':'L:W3'},
    {'id':'GF','stage':'grand_final','left':'W:W3','right':'W:L4'},
]
ELIMINATION = {'L1a':(7,8,0.0),'L1b':(7,8,0.0),
               'L2a':(5,6,.2),'L2b':(5,6,.2),'L3':(4,4,.4),
               'L4':(3,3,.6),'GF':(2,2,.8)}


def participant(reference, slots, completed):
    if reference.startswith('S'):return slots[int(reference[1:])-1]
    side,match_id=reference.split(':',1)
    result=completed.get(match_id)
    return result.get('winner' if side=='W' else 'loser') if result else None


def ready_matches(slots,completed,scheduled=()):
    result=[]
    for match in BRACKET:
        if match['id'] in completed or match['id'] in scheduled:continue
        left,right=participant(match['left'],slots,completed),participant(match['right'],slots,completed)
        if left and right:
            if left==right:raise ValueError('A participant cannot play itself')
            result.append({**match,'left_player':left,'right_player':right})
    return result


def group_vote(evaluations):
    """Every paired group is one vote, including multi-page groups."""
    counts={w:sum(e['winner']==w for e in evaluations) for w in ['A','B','tie']}
    winner='A' if counts['A']>counts['B'] else 'B' if counts['B']>counts['A'] else None
    return {'wins_a':counts['A'],'wins_b':counts['B'],'ties':counts['tie'],'winner':winner,'group_count':len(evaluations)}


def standings(slots,completed):
    if len(completed)!=14 or 'GF' not in completed:raise ValueError('A completed bracket requires exactly fourteen matches')
    records={p:{'participant':p,'wins':0,'losses':0} for p in slots}
    placements={}
    for match in BRACKET:
        result=completed[match['id']]
        expected={participant(match['left'],slots,completed),participant(match['right'],slots,completed)}
        assert {result['winner'],result['loser']}==expected
        records[result['winner']]['wins']+=1;records[result['loser']]['losses']+=1
        if match['id'] in ELIMINATION:
            lo,hi,reward=ELIMINATION[match['id']]
            assert result['loser'] not in placements
            placements[result['loser']]={'rank_min':lo,'rank_max':hi,'reward':reward,'elimination_match':match['id'],'elimination_stage':match['stage']}
    placements[completed['GF']['winner']]={'rank_min':1,'rank_max':1,'reward':1.0,'elimination_match':None,'elimination_stage':'champion'}
    assert set(placements)==set(slots)
    rows=[{**records[p],**placements[p]} for p in slots]
    assert sum(x['losses'] for x in rows)==14
    # The upper finalist is allowed to finish second with only one loss.
    assert all(x['losses']==2 for x in rows if x['rank_min']>=3)
    return sorted(rows,key=lambda x:(x['rank_min'],x['participant']))


def reward_conflicts(completed,ranking):
    rewards={x['participant']:x['reward'] for x in ranking}
    return [{'match_id':mid,'winner':m['winner'],'loser':m['loser'],
             'winner_reward':rewards[m['winner']],'loser_reward':rewards[m['loser']]}
            for mid,m in completed.items() if m.get('aesthetic_winner',m['winner']) is not None and rewards[m['winner']]<rewards[m['loser']]]
