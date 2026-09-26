import csv, os, re, subprocess, sys
sys.path.insert(0, "/home/ubuntu/demiflow_collect")

# v2 修复(相对 sgx_gbif_parse.py):
#  ① produce bid 偏移坑:每次 flush 新队列传 start_index=0+skip_existing 会把后续 flush
#    全部静默跳过——改为累计 bid_off;
#  ② join 结构:occurrence 命中行可达数千万,全量 gid2tk dict 会 OOM——pass1 就按物种
#    截前 3 个 gid(capped),反向表 ≤1000 万条(~2GB),30G 内存稳。

ZIP = sys.argv[1]
PAT = re.compile(r'<http://www\.wikidata\.org/entity/(Q\d+)> '
                 r'<http://www\.wikidata\.org/prop/direct/P846> "(.*)"')
key2qid = {}
with open("/home/ubuntu/wd_b4_bridge.nt", encoding="utf-8", errors="replace") as f:
    for line in f:
        m = PAT.search(line)
        if m:
            key2qid.setdefault(m.group(2), m.group(1))
print(f"P846 桥: {len(key2qid):,}", flush=True)

# pass1: occurrence → 每物种截前 3 gid → 反向表 gid→tk
tk_gids = {}
n = hit = 0
with subprocess.Popen(["unzip", "-p", ZIP, "occurrence.txt"],
                      stdout=subprocess.PIPE, text=True) as p:
    rd = csv.DictReader(p.stdout, delimiter="\t", quoting=csv.QUOTE_NONE)
    for r in rd:
        n += 1
        tk = r.get("taxonKey")
        if tk in key2qid:
            lst = tk_gids.get(tk)
            if lst is None:
                tk_gids[tk] = [r["gbifID"]]
                hit += 1
            elif len(lst) < 3:
                lst.append(r["gbifID"])
                hit += 1
        if n % 20_000_000 == 0:
            print(f"  occ {n:,} pairs {hit:,}", flush=True)
print(f"pass1: {n:,} 行, 物种 {len(tk_gids):,}, pairs {hit:,}", flush=True)

gid2tk = {}
for tk, gids in tk_gids.items():
    for g in gids:
        gid2tk[g] = tk
del tk_gids
print(f"join 表: {len(gid2tk):,}", flush=True)

# pass2: multimedia → 任务行 → 队列(累计 bid 偏移)
from cosio import COSCreds, COSIO, build_host
from cosqueue import COSQueue
io = COSIO(COSCreds.discover(paths=("/home/ubuntu/.cos_creds",)),
           build_host("lhcos-368f6-1256345599", "ap-singapore"))
NC_RE = re.compile(r'nc|non[- ]?commercial|by-nc', re.I)
rows, per, n = [], {}, 0
buf, bid_off = [], 0


def flush_rows():
    global buf, bid_off
    if buf:
        bids = COSQueue(io, "lhcos-data/demiwtg-data/queue-b4-gbifdl").produce(
            buf, 2000, start_index=bid_off, skip_existing=True)
        bid_off += len(bids)
        buf = []


with subprocess.Popen(["unzip", "-p", ZIP, "multimedia.txt"],
                      stdout=subprocess.PIPE, text=True) as p:
    rd = csv.DictReader(p.stdout, delimiter="\t", quoting=csv.QUOTE_NONE)
    for r in rd:
        n += 1
        tk = gid2tk.get(r.get("gbifID") or r.get("coreid") or "")
        if tk is not None:
            c = per.get(tk, 0)
            if c < 3:
                url = r.get("identifier") or ""
                if url.startswith("http"):
                    lic = r.get("license") or ""
                    per[tk] = c + 1
                    buf.append({"src": "gbifdl", "extid": f"taxon:{tk}#{c}",
                                "qid": key2qid[tk], "url": url, "license": lic,
                                "nc": 1 if (lic and NC_RE.search(lic)) else ""})
                    if len(buf) >= 50000:
                        flush_rows()
        if n % 20_000_000 == 0:
            print(f"  mm {n:,} 已选 {sum(per.values()):,} bid_off {bid_off}", flush=True)
flush_rows()
print(f"pass2: {n:,} 行, 选出 {sum(per.values()):,} 图 / {len(per):,} 物种, 批 {bid_off}", flush=True)
print("PARSE_DONE", flush=True)
