"""Plan conditional cross-batch review from saved paragraph embeddings; no LLM calls."""
import argparse
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import immutable,digest,source_code
from .ops.cross_batch import PlanCrossBatchReview


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--threshold',type=float,default=.8);a=p.parse_args()
    m={'source':str(a.source.resolve()),'sha256':digest(a.source.read_bytes()),'code':source_code(),'threshold':a.threshold,'scope':'Planning only; no generation or integration'}
    immutable(a.out/'manifest.json',m)
    (local_data().read_json(str(a.source))
     .reduce_by_key('concept',lambda acc,r:{'concept':r['concept'],'items':acc['items']+[r]},initial={'items':[]})
     .map(PlanCrossBatchReview(threshold=a.threshold)).checkpoint(a.out/'review_plan.jsonl',version=digest(m)))
    print(a.out/'review_plan.jsonl')

if __name__=='__main__':main()
