import unittest
from .ops.token_routing import RouteByTokenBudget
from .ops.cross_batch import PlanCrossBatchReview

class TokenRoutingTests(unittest.TestCase):
    def row(self):
        return {'case_id':'c','concept':'c','passages':[{'source_id':str(i),'text':'abc'} for i in range(3)],'images':[{'image_id':'i'}],'native_links':[{'source_id':'0','image_id':'i'}]}
    def test_whole_concept(self):
        r=RouteByTokenBudget('',100,counter=lambda r:10*len(r['passages'])+5*len(r['images']))(self.row())
        self.assertEqual(len(r['requests']),1)
    def test_split_preserves_material_and_native_image(self):
        r=RouteByTokenBudget('',25,counter=lambda r:10*len(r['passages'])+5*len(r['images']))(self.row())
        self.assertEqual(sorted(p['source_id'] for g in r['requests'] for p in g['passages']),['0','1','2'])
        g=next(g for g in r['requests'] if any(p['source_id']=='0' for p in g['passages']))
        self.assertEqual(g['images'],[{'image_id':'i'}])
        self.assertTrue(all(g['input_token_budget']<=25 for g in r['requests']))
    def test_oversize_not_silently_truncated(self):
        with self.assertRaises(ValueError):RouteByTokenBudget('',1,counter=lambda r:10)(self.row())
    def test_single_group_bypasses(self):
        r=PlanCrossBatchReview(skip_single_batch=True)({'concept':'c','items':[{'concept':'c','paragraph_id':'p','batch_id':'b'}]})
        self.assertEqual(r['review_requests'],[])

if __name__=='__main__':unittest.main()
