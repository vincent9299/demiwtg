"""Apply explicit scoped reviewer decisions, preserving machine judgments."""
import copy
from curation.v4.final_results import image_selection
from .quality_policy import image_followups

class ApplySupportReview:
    def __init__(self,review):self.review=review
    def __call__(self,row):
        out=copy.deepcopy(row);knowledge=out['knowledge']
        pairs=knowledge['image_evidence']['result']['support']
        known={(p['fact_id'],p['image_id']) for p in pairs}
        overrides={(r['fact_id'],r['image_id']):r for r in self.review['decisions']}
        if not set(overrides)<=known:raise ValueError('Reviewer refers to unknown support')
        for p in pairs:
            r=overrides.get((p['fact_id'],p['image_id']))
            if r:
                if r['status'] not in {'none','unobservable'} or not r.get('reason'):raise ValueError('Review only withdraws uncertain/invalid support')
                p['machine_judgment']=copy.deepcopy(p)
                p.update(status=r['status'],supports='',limitations=r['reason'],reviewer=self.review['reviewer'])
        for f in knowledge['facts']:
            disputed=[e for e in f.get('image_evidence',[]) if (f['fact_id'],e['image_id']) in overrides]
            if not disputed:continue
            f['disputed_image_evidence']=disputed
            f['image_evidence']=[e for e in f['image_evidence'] if e not in disputed]
            if f.get('evidence'):f['basis']='multimodal' if f['image_evidence'] else 'text'
            else:raise ValueError('Visual-only knowledge requires an explicit knowledge deferral review')
        out['audit']['scoped_support_review']=self.review
        knowledge['image_followups']=image_followups(knowledge['facts'],knowledge['image_evidence'])
        out['image_selection']=image_selection(out['images'],knowledge)
        return out
