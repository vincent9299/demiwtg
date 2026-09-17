"""Apply a signed, narrow review to a new output; no model or source rewriting."""
import argparse,json
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import digest,immutable,source_code,runtime_version,run_lock
from .ops.support_review import ApplySupportReview

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ['source','review','run']:p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();review=json.loads(a.review.read_text())
    with run_lock(a.run):
        manifest={'source':str(a.source),'source_sha256':digest(a.source.read_bytes()),'review':review,'code':source_code(),'runtime':runtime_version(),'model_calls':0}
        immutable(a.run/'manifest.json',manifest)
        local_data().read_json(str(a.source)).map(ApplySupportReview(review)).checkpoint(a.run/'knowledge_base.jsonl',version=digest(manifest))
if __name__=='__main__':main()
