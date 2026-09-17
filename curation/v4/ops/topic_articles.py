"""Topic articles with joint image selection and one deduplicated reference list."""
from urllib.parse import urlsplit,urlunsplit,unquote
import numpy as np


def reference_key(url, fallback):
    if not url:return ('id',fallback)
    p=urlsplit(url)
    return ('url',urlunsplit((p.scheme.lower(),p.netloc.lower(),unquote(p.path).rstrip('/'),p.query,'')))


def choose_images(candidates, windows, vectors, max_images=5, relevance_weight=.7,
                  relevance_floor=.08, relevance_margin=.04, duplicate_threshold=.93):
    """Relevance gate followed by MMR: relatedness first, visual novelty second."""
    if not candidates or not windows:return [],[]
    def unit(v):
        a=np.asarray(v,dtype=float);n=np.linalg.norm(a)
        if not np.isfinite(a).all() or n==0:raise ValueError('Invalid embedding')
        return a/n
    text=np.asarray([unit(w['embedding']) for w in windows])
    ids=list(dict.fromkeys(b['image_id'] for b in candidates))
    ims={i:unit(vectors[i]) for i in ids}  # Missing embedding is an error, not zero relevance.
    relevance={i:float(np.max(text@ims[i])) for i in ids}
    cutoff=max(relevance_floor,max(relevance.values())-relevance_margin)
    remaining={i for i in ids if relevance[i]>=cutoff};selected=[];audit=[]
    while remaining and len(selected)<max_images:
        def redundancy(i):return max((float(ims[i]@ims[j]) for j in selected),default=0.)
        def score(i):return relevance_weight*relevance[i]-(1-relevance_weight)*max(0.,redundancy(i))
        pick=max(remaining,key=lambda i:(score(i),relevance[i],i));red=redundancy(pick);mmr=score(pick)
        remaining.remove(pick)
        if selected and red>=duplicate_threshold:
            audit.append({'image_id':pick,'selected':False,'reason':'near_duplicate','relevance':relevance[pick],'redundancy':red});continue
        selected.append(pick);audit.append({'image_id':pick,'selected':True,'reason':'relevance_and_visual_diversity','relevance':relevance[pick],'redundancy':red,'mmr_score':mmr})
    seen={r['image_id'] for r in audit}
    audit.extend({'image_id':i,'selected':False,'reason':'below_relevance_gate' if relevance[i]<cutoff else 'image_limit','relevance':relevance[i]} for i in ids if i not in seen)
    return selected,audit


class TopicRows:
    def __call__(self,row):
        return [{'concept':row['concept'],'case_id':row['concept'],'topic_id':f"{row['batch_id']}:{idx}",
                 'title':t['title'],'blocks':t['blocks'],'run':row['run'],'batch_id':row['batch_id']}
                for idx,t in enumerate(row['topics'])]


class TopicTextInputs:
    def __call__(self,row):
        passages=[{'source_id':r['topic_id'],'text':r['title']+'\n'+'\n\n'.join(b['text'] for b in r['blocks'] if b['type']=='text')} for r in row['items']]
        return {**row,'case_id':row['concept'],'passages':passages,'images':[]}


class BuildTopicArticle:
    def __init__(self, text_windows, image_vectors, sources, image_sources, selection_mode='model', **selection):
        self.windows=text_windows;self.vectors=image_vectors;self.sources=sources;self.image_sources=image_sources;self.selection=selection;self.selection_mode=selection_mode
    def __call__(self,row):
        candidates=[b for b in row['blocks'] if b['type']=='image']
        if self.selection_mode=='model':
            chosen=list(dict.fromkeys(b['image_id'] for b in candidates))
            audit=[{'image_id':iid,'selected':True,'reason':'Preserve model-selected, verified image; publisher does not rescore or cap'} for iid in chosen]
        elif self.selection_mode=='legacy_mmr':
            chosen,audit=choose_images(candidates,self.windows.get(row['topic_id'],[]),self.vectors,**self.selection)
        else:raise ValueError('Unknown image selection mode')
        lookup={b['image_id']:b for b in candidates};refs={}
        def add(sid,kind):
            src=(self.sources if kind=='text' else self.image_sources).get(sid,{'title':sid,'url':''})
            key=reference_key(src.get('url',''),sid)
            entry=refs.setdefault(key,{'title':src.get('title') or sid,'url':src.get('url',''),'source_ids':[],'kinds':[]})
            if sid not in entry['source_ids']:entry['source_ids'].append(sid)
            if kind not in entry['kinds']:entry['kinds'].append(kind)
        for b in row['blocks']:
            if b['type']=='text':
                for c in b.get('citations',[]):add(c['source_id'],'text')
                for ref in b.get('image_refs',[]):add(ref['image_id'],'image')
        for iid in chosen:add(iid,'image')
        article={'title':row['title'],'content':{'paragraphs':[b['text'] for b in row['blocks'] if b['type']=='text'],
                   'images':[{k:lookup[i][k] for k in ['image_id','caption','region','limitations']} for i in chosen]},
                 'references':list(refs.values())}
        return {'concept':row['concept'],'topic_id':row['topic_id'],'article':article,
                'audit':{'image_selection':audit,'original_blocks':row['blocks'],'input_run':row['run'],'input_batch':row['batch_id']}}
