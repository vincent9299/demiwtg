"""qid→桶 全量映射(独立于 rollup,复用其白名单上卷逻辑)

输入(sgx ~/):p31_all.tsv / p279_all.tsv / haveimg_final.txt /
             p31_buckets.tsv / p31_human_set.tsv / p31_labels.tsv
输出:p31_qid_bucket_map.tsv (qid 主类 主类标签 桶qid 桶名)
     p31_class_bucket.tsv   (类 类标签 桶qid 桶名)
口径:同一 qid 多行时取首行(超大实体碎片行会被丢弃);主类=P31 首值
"""
import collections, os, time

t0 = time.time()
HOME = os.environ.get("DEMIWTG_P31_HOME", "/home/ubuntu")
MAX_LEVEL, MAX_VISIT = 15, 3000

BUCKETS = []
for line in open(f"{HOME}/p31_buckets.tsv", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#"):
        qid, name = line.split("\t")[:2]
        BUCKETS.append((qid, name))
BUCKET_NAME = dict(BUCKETS)
BUCKET_ORDER = {q: i for i, (q, _) in enumerate(BUCKETS)}

have = set()
for l in open(f"{HOME}/haveimg_final.txt"):
    l = l.strip()
    if l.startswith("Q") and l[1:].isdigit():
        have.add(l)

labels = {}
try:
    for l in open(f"{HOME}/p31_labels.tsv", encoding="utf-8"):
        p = l.rstrip("\n").split("\t")
        if len(p) >= 3:
            labels[p[0]] = (p[1], p[2])
except FileNotFoundError:
    pass
def lab(q):
    en, zh = labels.get(q, ("", ""))
    return zh or en or q

HUMAN_SET = {"Q5"}
try:
    for l in open(f"{HOME}/p31_human_set.tsv", encoding="utf-8"):
        q = l.strip().split("\t")[0]
        if q.startswith("Q"):
            HUMAN_SET.add(q)
except FileNotFoundError:
    pass

# 1) 主类计数:同 qid 取首行(去碎片重复)
have_cls = collections.Counter()
first_of = {}                     # qid -> 主类(首行)
dup_rows = 0
for l in open(f"{HOME}/p31_all.tsv"):
    q, _, cls = l.rstrip("\n").partition("\t")
    if q not in have:
        continue
    main = "Q" + cls.split(",")[0]
    if q in first_of:
        dup_rows += 1
        continue
    first_of[q] = main
    have_cls[main] += 1
print(f"[1] 有图且有P31 {len(first_of):,} qid,重复行丢弃 {dup_rows:,},"
      f"主类 {len(have_cls):,}  {time.time()-t0:.0f}s", flush=True)

# 2) 闭包:used 类及其祖先的 P279 边
parents = {}
frontier = set(have_cls)
for it in range(12):
    want, new, found = frontier, set(), 0
    for l in open(f"{HOME}/p279_all.tsv"):
        q, _, rest = l.rstrip("\n").partition("\t")
        if q in want and q not in parents:
            parents[q] = ["Q" + x for x in rest.split("\t")[0].split(",")]
            found += 1
            new |= {p for p in parents[q] if p not in parents}
    frontier = new - set(parents)
    if not frontier:
        break
print(f"[2] 闭包 {it+1} 轮,{len(parents):,} 类边  {time.time()-t0:.0f}s", flush=True)

# 3) 分配(与 rollup 白名单版同逻辑)
def assign(c):
    if c in BUCKET_ORDER and c != "Q5":
        return c
    if c in HUMAN_SET:
        return "Q5"
    seen = {c, "Q5"}
    frontier = [c]
    visited = 0
    for _ in range(MAX_LEVEL):
        nxt = []
        for q in frontier:
            for p in parents.get(q, ()):
                if p != "Q5" and p not in seen:
                    seen.add(p)
                    nxt.append(p)
        visited += len(nxt)
        if not nxt or visited > MAX_VISIT:
            return None
        hits = [b for b in BUCKET_ORDER if b != "Q5" and b in nxt]
        if hits:
            return min(hits, key=lambda b: BUCKET_ORDER[b])
        frontier = nxt
    return None

cls_bucket = {}
for c in have_cls:
    cls_bucket[c] = assign(c)
print(f"[3] 分配完成  {time.time()-t0:.0f}s", flush=True)

# 4) 落盘
with open(f"{HOME}/p31_class_bucket.tsv", "w", encoding="utf-8") as f:
    f.write("class\tclass_label\tbucket_qid\tbucket_name\n")
    for c, n in have_cls.most_common():
        b = cls_bucket[c]
        f.write(f"{c}\t{lab(c)}\t{b or ''}\t{BUCKET_NAME.get(b, '')}\n")
mapped = 0
with open(f"{HOME}/p31_qid_bucket_map.tsv", "w", encoding="utf-8") as f:
    f.write("qid\tmain_class\tmain_class_label\tbucket_qid\tbucket_name\n")
    for q, c in first_of.items():
        b = cls_bucket[c]
        if b:
            mapped += 1
        f.write(f"{q}\t{c}\t{lab(c)}\t{b or ''}\t{BUCKET_NAME.get(b, '')}\n")
n_have31 = len(first_of)
print(f"[4] 映射 {mapped:,}/{n_have31:,} 有P31实体落桶"
      f"({mapped/len(have):.1%} of 有图全集) 用时 {time.time()-t0:.0f}s", flush=True)
