"""Glass-only cleaning experiment. Native demiflow, no model calls or relevance filter."""
import argparse
import hashlib
import html
import json
from importlib.metadata import version as package_version
from pathlib import Path
from demiflow.standalone import local_data
from .contracts import immutable, digest
from .ops.dataset_operators import CleanDocument
from .ops.cleaning_trials import QualityBranches, CompareDuplicates


def render_report(run, result, html_results):
    names={'baseline':'现有清洗','gopher_english_thresholds':'英文阈值直接套用（反例）','gopher_multilingual_signals':'重复与符号规则整页过滤（实验）','c4_terminal_punctuation':'C4式逐行标点过滤（反例）','block_quality':'结构块质量过滤','paragraph_dedup':'再做段落精确去重','document_dedup':'保守文档去重','minhash_drop_document':'MinHash整篇删重（对照）'}
    esc=html.escape
    def pre(t):return '<pre>'+esc(t)+'</pre>'
    rows=result['documents'];head='<meta charset="utf-8"><title>玻璃棒清洗对照</title><style>body{font:16px system-ui;max-width:1400px;margin:30px auto}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px;vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:650px;overflow:auto}summary{cursor:pointer;padding:8px;background:#f4f5f7}details{margin:10px 0}.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}</style>'
    out=[head,'<h1>玻璃棒：只看清洗、过滤与去重</h1><p>14份冻结采集文本；无概念相关性判断、无模型提炼。所有原文与删除原因保留。英文阈值和C4标点分支是反例对照，不作为推荐输出。HTML来自另一次抓取，不能与历史文本直接归因比较。</p>']
    out.append('<table><tr><th>文档</th><th>原文字符</th>'+''.join('<th>'+esc(v)+'</th>' for v in names.values())+'</tr>')
    for r in rows:out.append('<tr><td><a href="#'+r['doc_id']+'">'+esc(r['title'])+'</a></td><td>'+str(len(r['raw_text']))+'</td>'+''.join('<td>'+str(len(r['variants'][k]))+'</td>' for k in names)+'</tr>')
    out.append('</table><p>字符少不等于质量高。下方可以查看每个被删片段及其原文位置。</p>')
    for r in rows:
        out.append('<h2 id="'+r['doc_id']+'">'+esc(r['title'])+'</h2><p>'+esc(r['url'])+'</p><details><summary>采集原文</summary>'+pre(r['raw_text'])+'</details>')
        out.append('<div class="cols"><div><h3>原有清洗</h3>'+pre(r['clean_text'])+'</div><div><h3>本轮结构块过滤＋去重</h3>'+pre(r['variants']['document_dedup'] or '（没有保留正文）')+'</div></div>')
        for k in ['gopher_english_thresholds','gopher_multilingual_signals','c4_terminal_punctuation']:out.append('<details><summary>'+esc(names[k])+'</summary>'+pre(r['variants'][k] or '（整页排除／无保留行）')+'</details>')
        out.append('<details><summary>过滤依据与指标</summary>'+pre(json.dumps(r['filter_diagnostics'],ensure_ascii=False,indent=2))+'</details>')
        out.append('<details><summary>逐块决定：保留、排除及原文（完整）</summary><table><tr><th>决定／原因／原文位置</th><th>清洗后的块</th><th>原始片段</th></tr>')
        for b in r['dedup_blocks']:
            if not b['raw_text'].strip():continue
            out.append('<tr><td>'+esc(b['decision']+' · '+b['reason'])+'<br>'+str(b['raw_start'])+':'+str(b['raw_end'])+'</td><td>'+pre(b['text'])+'</td><td>'+pre(b['raw_text'])+'</td></tr>')
        out.append('</table></details>')
    out.append('<h2>重复文档候选与跨文档重复段落</h2>'+pre(json.dumps({k:v for k,v in result.items() if k!='documents'},ensure_ascii=False,indent=2)))
    out.append('<h2>重新抓取HTML：抽取器对照</h2><p>仅比较同一次HTML上的两种抽取器；抓取失败与反爬页分别列出，不能算成功正文。</p>')
    for r in html_results:
        out.append('<details><summary>'+esc(r.get('url',''))+' · HTTP '+str(r.get('http_status'))+'</summary>')
        out.append('<div class="cols"><div><h3>Trafilatura</h3>'+pre(r.get('trafilatura','') or r.get('error','空输出'))+'</div><div><h3>Resiliparse</h3>'+pre(r.get('resiliparse','') or r.get('error','空输出'))+'</div></div></details>')
    (run/'preview.html').write_text(''.join(out))


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);a=p.parse_args();run=a.run.resolve()
    source=run/'raw_documents.jsonl';code_root=Path(__file__).parent
    code={str(f.relative_to(code_root)):digest(f.read_bytes()) for f in code_root.rglob('*.py') if '__pycache__' not in f.parts and not f.name.startswith('test_')}
    manifest={'input_sha':digest(source.read_bytes()),'code':code,'packages':{k:package_version(k) for k in ['demiflow','trafilatura','resiliparse','datasketch','jieba']},'scope':'glass only; no relevance or model; independent filter ablations'}
    immutable(run/'experiment_manifest.json',manifest);v=digest(manifest);data=local_data();tables=run/'datasets'
    raw=data.read_records(source).map(lambda r:r['value'])
    baseline=raw.map_cached(CleanDocument(),cache_dir=run/'cache/clean',version=v).checkpoint(tables/'baseline.jsonl',version=v)
    branches=baseline.map(QualityBranches()).checkpoint(tables/'quality_branches.jsonl',version=v)
    comparison=branches.map(lambda r:{**r,'case':'glass'}).group_batches('case',max_rows=100,output='documents').map(CompareDuplicates()).checkpoint(tables/'comparison.jsonl',version=v)
    result=comparison.take(1)[0]
    html_results=[]
    fetch_path=run/'html_fetch_proxy.json'
    if fetch_path.exists():
        from trafilatura import extract
        from trafilatura.utils import decode_file
        from resiliparse.extract.html2text import extract_plain_text
        for fetched in json.loads(fetch_path.read_text()):
            r=dict(fetched)
            if r.get('http_status')==200 and 'html' in (r.get('content_type') or '').lower():
                raw=Path(r['path']).read_bytes();assert hashlib.sha256(raw).hexdigest()==r['sha256'];text=decode_file(raw)
                for key,fn in [('trafilatura',lambda:extract(text,favor_precision=True,include_comments=False,include_tables=True) or ''),('resiliparse',lambda:extract_plain_text(text,main_content=True,comments=False))]:
                    try:r[key]=fn()
                    except Exception as e:r[key]='';r[key+'_error']=str(e)
            html_results.append(r)
        immutable(run/'html_extraction.json',html_results)
    summary={'documents':len(result['documents']),'branches':{name:{'retained_documents':sum(bool(r['variants'][name].strip()) for r in result['documents']),'characters':sum(len(r['variants'][name]) for r in result['documents'])} for name in result['documents'][0]['variants']},'near_duplicate_pairs':result['near_duplicate_pairs'],'repeated_paragraphs':len(result['repeated_paragraphs']),'paragraph_near_pairs':len(result.get('paragraph_near_pairs',[])), 'shared_passages':len(result['shared_passages']), 'shared_duplicate_groups':sum(len(x['sources'])>1 for x in result['shared_passages']), 'shared_extra_occurrences':sum(len(x['sources'])-1 for x in result['shared_passages']),'model_calls':0}
    immutable(run/'summary.json',summary);render_report(run,result,html_results);print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
