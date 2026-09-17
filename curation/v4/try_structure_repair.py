"""Resume the cleaning-only trial; emit JSONL and notebook outputs, no HTML page."""
import json
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import digest,immutable
from .ops.repair_source_structure import RepairSourceBlocks,ReadDOMFields

ROOT=Path(__file__).resolve().parents[2]

def main():
    parent=ROOT/'state/curation/cleaning_glass_v4';run=ROOT/'state/curation/cleaning_glass_v6';run.mkdir(parents=True,exist_ok=True)
    inputs=[parent/'datasets/comparison.jsonl',parent/'html_fetch_proxy.json']
    manifest={'parent':str(parent),'inputs':{str(p):digest(p.read_bytes()) for p in inputs},'code':{p.name:p.read_text() for p in [Path(__file__),Path(__file__).parent/'ops/repair_source_structure.py']},'scope':'repair source layout only; no relevance filter or model; no new HTML fetch'}
    immutable(run/'manifest.json',manifest);v=digest(manifest);data=local_data()
    documents=data.read_records(inputs[0]).map(lambda x:x['value']).flat_map(lambda r:r['documents'])
    result=documents.map(RepairSourceBlocks()).checkpoint(run/'documents.jsonl',version=v)
    fetched=json.loads(inputs[1].read_text())
    dom=data.from_items(fetched).map(ReadDOMFields()).checkpoint(run/'dom_fields.jsonl',version=v)
    retained_dom=dom.join(result.filter(lambda r:bool(r['final_text'])).select_columns(['doc_id']),on='doc_id',how='semi').checkpoint(run/'retained_dom_fields.jsonl',version=v)
    rows=result.take(100)
    for row in rows:
        for b in row['repaired_blocks']:
            assert row['raw_text'][b['raw_start']:b['raw_end']]==b['raw_text']
    review=json.loads((parent/'review.json').read_text());by={r['doc_id']:r for r in rows}
    for c in review['content_anchors']:assert c['anchor'] in by[c['doc_id']]['final_text']
    summary={'documents':len(rows),'retained_documents':sum(bool(r['final_text']) for r in rows),'before_chars':sum(len(r['variants']['block_quality']) for r in rows),'after_chars':sum(len(r['final_text']) for r in rows),'repair_actions':sum(len(r['repairs']) for r in rows),'restored_blocks':sum(e['action']=='restore' for r in rows for e in r['repairs']),'unresolved_fields':sum(len(r['unresolved_structure']) for r in rows),'dom_pairs':sum(len(r['dom_fields']) for r in dom.take(100)),'retained_dom_pairs':sum(len(r['dom_fields']) for r in retained_dom.take(100)),'content_anchors_preserved':len(review['content_anchors']),'source_offsets_verified':True,'model_calls':0}
    immutable(run/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
