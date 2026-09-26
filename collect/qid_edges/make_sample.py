"""1M 实体均衡抽样清单:5千档语义并类(≥200合格) × 水填配额281, seed=42"""
import collections, random, time

t0 = time.time()
HOME = "/home/ubuntu"
SEED, QUOTA = 42, 281

res = {}
for l in open(f"{HOME}/qid_res1024.tsv"):
    q, c = l.split("\t"); res[q] = int(c)

def semantic(cid):
    parts = cid.split("|")
    while parts and parts[-1].startswith("shard"):
        parts.pop()
    if len(parts) == 3 and parts[2] == "noaux":
        parts = parts[:2]
    if len(parts) == 1 and (cid.endswith("#self") or cid.endswith("#other")):
        return parts[0].split("#")[0]
    if parts and parts[-1].startswith("@"):
        parts.pop()
        if not parts: return "ORPH"
    return "|".join(parts)

frame = collections.defaultdict(list)      # 语义类 -> [qid,...]
with open(f"{HOME}/qid_cut_map_v4_5000.tsv") as f:
    next(f)
    for l in f:
        p = l.rstrip("\n").split("\t")
        if len(p) >= 3 and p[0] in res and p[2] != "UNMAPPED":
            frame[semantic(p[2])].append(p[0])
cats = {c: q for c, q in frame.items() if len(q) >= 200}
print(f"[1] 框架 {len(cats):,} 类 / {sum(len(v) for v in cats.values()):,} 实体 {time.time()-t0:.0f}s", flush=True)

rng = random.Random(SEED)
picked = 0
with open(f"{HOME}/sample_1m_entities.tsv", "w") as so, \
     open(f"{HOME}/sample_1m_classes.tsv", "w") as sc:
    so.write("qid\tcat_id\timgs_ge1024\n")
    sc.write("cat_id\tavailable\tquota_sampled\n")
    for c, qids in sorted(cats.items(), key=lambda x: -len(x[1])):
        k = min(len(qids), QUOTA)
        sel = rng.sample(qids, k)
        picked += k
        sc.write(f"{c}\t{len(qids)}\t{k}\n")
        for q in sel:
            so.write(f"{q}\t{c}\t{res[q]}\n")
print(f"[2] 完成:抽样 {picked:,} 实体,图(展开) ≈ {sum(0 for _ in ())}...", flush=True)
tot_img = 0
for l in open(f"{HOME}/sample_1m_entities.tsv"):
    if not l.startswith("qid"):
        tot_img += int(l.rstrip("\n").rsplit("\t", 1)[1])
print(f"[3] 展开图总量 {tot_img:,} | 用时 {time.time()-t0:.0f}s", flush=True)
