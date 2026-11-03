"""Complete eight-player first-round coverage without using model outcomes."""
from itertools import combinations

def pair_key(a,b):return tuple(sorted((a,b)))
def first_pairs(slots):
    if len(slots)!=8 or len(set(slots))!=8:raise ValueError('Exactly eight unique PPT IDs are required')
    return tuple(pair_key(*slots[i:i+2]) for i in range(0,8,2))

def perfect_matchings(items):
    items=tuple(sorted(items))
    if not items:
        yield ();return
    a=items[0]
    for i in range(1,len(items)):
        b=items[i]
        for rest in perfect_matchings(items[1:i]+items[i+1:]):yield ((a,b),)+rest

def complete_plan(ids,fixed_slots=()):
    ids=sorted(ids)
    if len(ids)!=8 or len(set(ids))!=8:raise ValueError('The current tournament implementation supports eight candidates')
    fixed=[list(x) for x in fixed_slots];used=set()
    for slots in fixed:
        if sorted(slots)!=ids:raise ValueError('Fixed draw uses a different candidate set')
        pairs=set(first_pairs(slots))
        if used & pairs:raise ValueError('A first-round opponent pair was reused')
        used |= pairs
    candidates=list(perfect_matchings(ids))
    def search(chosen,edges):
        if len(chosen)+len(fixed)==7:return chosen
        for matching in candidates:
            if not edges.intersection(matching):
                result=search(chosen+[matching],edges|set(matching))
                if result is not None:return result
        return None
    result=search([],used)
    if result is None:raise ValueError('The fixed draws cannot be completed to seven disjoint first rounds')
    output=fixed+[[x for pair in matching for x in pair] for matching in result]
    all_pairs=[p for slots in output for p in first_pairs(slots)]
    assert len(all_pairs)==len(set(all_pairs))==28 and set(all_pairs)==set(combinations(ids,2))
    return output
