import pytest
from .ops.paragraph_similarity import pair_candidates,bounded_groups


def row(i,v,tokens=10,concept='C'):
    return {'paragraph_id':str(i),'concept':concept,'embedding_title_body':v,'citations':[],'merge_content_tokens':tokens}


def test_candidate_search_stays_in_concept_and_does_not_force_neighbors():
    rows=[row(1,[1,0]),row(2,[.99,.1]),row(3,[0,1])]
    pairs,scores=pair_candidates(rows,threshold=.9,top_k=1)
    assert [(p['left'],p['right']) for p in pairs if p['candidate']]==[('1','2')]
    groups,residual=bounded_groups(rows,pairs,scores,threshold=.9)
    assert sorted(len(g['paragraph_ids']) for g in groups)==[1,2] and not residual
    with pytest.raises(ValueError,match='one concept'):pair_candidates(rows+[row(4,[1,0],concept='D')])


def test_no_transitive_giant_group_and_capacity_keeps_every_paragraph():
    import math
    rows=[row(i,[math.cos(a),math.sin(a)]) for i,a in enumerate([0,.5,1.])]
    pairs,scores=pair_candidates(rows,threshold=.8,top_k=2)
    groups,residual=bounded_groups(rows,pairs,scores,threshold=.8)
    assert sorted(len(g['paragraph_ids']) for g in groups)==[1,2] and len(residual)==1
    assert sorted(p for g in groups for p in g['paragraph_ids'])==['0','1','2']
    groups,_=bounded_groups(rows,pairs,scores,threshold=.8,max_tokens=15)
    assert len(groups)==3


def test_native_batch_and_reduce_wiring():
    from demiflow.standalone import local_data
    from .ops.paragraph_similarity import FindParagraphNeighbors
    data=local_data().from_iter(lambda:iter([row(1,[1,0]),row(2,[1,0])]))
    grouped=data.reduce_by_key('concept',lambda acc,r:{'concept':r['concept'],'items':acc['items']+[r]},initial={'items':[]})
    result=grouped.map(FindParagraphNeighbors()).take_all()
    assert result[0]['groups'][0]['paragraph_ids']==['1','2']
