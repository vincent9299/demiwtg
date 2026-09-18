import gzip, subprocess, urllib.parse, collections, json
ledger = set()
p = subprocess.Popen(["zcat","/lhcos-data/demiwtg-data/datasets/demiwtg/kb/qid_images.jsonl.gz"], stdout=subprocess.PIPE, bufsize=1<<22)
for line in p.stdout:
    try: ledger.add(json.loads(line)["commons_file"])
    except Exception: pass
p.wait()
print(f"ledger unique commons_file: {len(ledger):,}", flush=True)
def norm(url):
    try:
        path = urllib.parse.unquote(url.split("?")[0])
        b = path.split("/")[-1]
        if "/thumb/" in path:
            pre = b.split("-",1)
            if len(pre)==2 and pre[0].endswith("px"): b = pre[1]
        return b
    except Exception: return None
wit_files=set(); rows=0; by_lang=collections.Counter(); cov_lang=collections.Counter(); ex=[]
with gzip.open("/home/ubuntu/demi/raw/wit_sample.tsv.gz","rt",encoding="utf-8",errors="replace") as f:
    hdr=f.readline().rstrip("\n").split("\t"); iu=hdr.index("image_url"); lg=hdr.index("language")
    for line in f:
        c=line.rstrip("\n").split("\t"); rows+=1
        if len(c)<=iu or not c[iu]: continue
        by_lang[c[lg]]+=1; n=norm(c[iu])
        if n:
            wit_files.add(n)
            if n in ledger: cov_lang[c[lg]]+=1
            elif len(ex)<5: ex.append((c[iu],n))
uniq_cov=sum(1 for n in wit_files if n in ledger)
rl=sum(by_lang.values()); rc=sum(cov_lang.values())
res={"ledger":len(ledger),"sample_rows":rows,"wit_unique_files":len(wit_files),
     "unique_cov":uniq_cov,"unique_pct":round(uniq_cov/len(wit_files)*100,1),
     "row_cov":rc,"row_pct":round(rc/max(rl,1)*100,1)}
print(json.dumps(res,ensure_ascii=False), flush=True)
print("top_langs:",[(l,f"{by_lang[l]:,}",f"{cov_lang[l]/by_lang[l]*100:.0f}%") for l,_ in by_lang.most_common(6)], flush=True)
print("uncovered examples:",ex, flush=True)
