"""均衡切层 v5 = v3 树切 + 递归谓词瀑布(P106/P171/P140/P131/P136)
+ 辅助森林内质量递归切 + 无维度兜底分段。保证超限=0。
环境:QC_MAX(20000)/QC_MIN(200)/QC_SUFFIX/QC_AUX;需 qid_edges_ext.tsv
"""
import collections, os, time

t0 = time.time()
HOME = os.environ.get("DEMIWTG_P31_HOME", "/home/ubuntu")
SUF = os.environ.get("QC_SUFFIX", "")
MIN_MASS = int(os.environ.get("QC_MIN", "200"))
MAX_MASS = int(os.environ.get("QC_MAX", "20000"))
AUX_PREDS = os.environ.get("QC_AUX", "P106,P171,P140,P131,P136").split(",")
AUX_CHAIN = {"P106": "P279", "P140": "P279", "P136": "P279",
             "P131": "P131", "P171": "P171"}
PNAME = {"P106": "职业", "P171": "物种树", "P140": "宗派",
         "P131": "属地", "P136": "体裁"}

BUCKETS = []
for line in open(f"{HOME}/p31_buckets.tsv", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#"):
        q, name = line.split("\t")[:2]
        BUCKETS.append((q, name))
BNAME = dict(BUCKETS)
PRIO = [q for q, _ in BUCKETS]
PRIO_IDX = {q: i for i, q in enumerate(PRIO)}
SRC = [q for q in PRIO if q != "Q5"]

labels = {}
for l in open(f"{HOME}/qid_class_labels.tsv", encoding="utf-8"):
    p = l.rstrip("\n").split("\t")
    if len(p) >= 3:
        labels[p[0]] = p[2] or p[1]
def lab(q):
    return labels.get(q, q)

HUMAN_SET = {"Q5"}
for l in open(f"{HOME}/p31_human_set.tsv", encoding="utf-8"):
    q = l.strip().split("\t")[0]
    if q.startswith("Q"):
        HUMAN_SET.add(q)

have = set()
for l in open(f"{HOME}/haveimg_final.txt"):
    l = l.strip()
    if l.startswith("Q") and l[1:].isdigit():
        have.add(l)
qid_main = {}
direct = collections.Counter()
for l in open(f"{HOME}/p31_all.tsv"):
    q, _, cls = l.rstrip("\n").partition("\t")
    if q in have and q not in qid_main:
        qid_main[q] = "Q" + cls.split(",")[0]
        direct[qid_main[q]] += 1
print(f"[1] {len(direct):,} 类 {len(qid_main):,} 实体 {time.time()-t0:.0f}s", flush=True)

# 辅助值(先收集,供闭包 frontier)
auxvals = collections.defaultdict(dict)
ext_ok = os.path.exists(f"{HOME}/qid_edges_ext.tsv")
if ext_ok:
    for l in open(f"{HOME}/qid_edges_ext.tsv"):
        q, _, rest = l.rstrip("\n").partition("\t")
        pred, _, val = rest.partition("\t")
        if pred in AUX_PREDS:
            auxvals[pred].setdefault(q, val)
aux_value_qids = set()
for pred in AUX_PREDS:
    if AUX_CHAIN[pred] == "P279":
        aux_value_qids |= set(auxvals[pred].values())
print(f"[1b] 辅助值 {sum(len(v) for v in auxvals.values()):,} {time.time()-t0:.0f}s", flush=True)

parents = {}
frontier = set(direct) | aux_value_qids
for it in range(12):
    want, new = frontier, set()
    for l in open(f"{HOME}/p279_all.tsv"):
        q, _, rest = l.rstrip("\n").partition("\t")
        if q in want and q not in parents:
            parents[q] = ["Q" + x for x in rest.split("\t")[0].split(",")]
            new |= {p for p in parents[q] if p not in parents}
    frontier = new - set(parents)
    if not frontier:
        break
children = collections.defaultdict(list)
for c, ps in parents.items():
    for p in ps:
        children[p].append(c)

label = {}; tparent = {}
for q in SRC:
    label[q] = (0, PRIO_IDX[q])
for q in HUMAN_SET:
    if q != "Q5":
        label.setdefault(q, (0, PRIO_IDX["Q5"]))
        tparent.setdefault(q, "Q5")
dq = collections.deque(list(label))
while dq:
    u = dq.popleft()
    du, pu = label[u]
    for v in children.get(u, ()):
        if v == "Q5":
            continue
        cand = (du + 1, pu)
        if v not in label or cand < label[v]:
            label[v] = cand
            tparent[v] = u
            dq.append(v)
label.setdefault("Q5", (0, PRIO_IDX["Q5"]))

tch = collections.defaultdict(list)
for c, p in tparent.items():
    tch[p].append(c)
mass = collections.defaultdict(int, direct)
nchild = {c: len(tch.get(c, ())) for c in set(direct) | set(tparent) | set(tparent.values())}
stack = [c for c, k in nchild.items() if k == 0]
while stack:
    c = stack.pop()
    p = tparent.get(c)
    if p is not None:
        mass[p] += mass[c]
        nchild[p] -= 1
        if nchild[p] == 0:
            stack.append(p)
orphan = [c for c in direct if c not in label]
print(f"[2] 树+质量 {time.time()-t0:.0f}s", flush=True)

cat_of_node = {}
def cut(node):
    m = mass.get(node, 0)
    if m <= MAX_MASS:
        cat_of_node[node] = node
        return
    chs = [c for c in tch.get(node, ()) if mass.get(c, 0) > 0]
    if not chs:
        cat_of_node[node] = node
        return
    big = [c for c in chs if mass[c] >= MIN_MASS]
    small = [c for c in chs if mass[c] < MIN_MASS]
    if small:
        oid = f"{node}#other"
        for c in small:
            cat_of_node[c] = oid
    for c in big:
        cut(c)
    if m - sum(mass[c] for c in chs) > 0:
        cat_of_node.setdefault(node, f"{node}#self")

for q in PRIO:
    if q in label or q in mass:
        cut(q)

def category_of(c):
    sp = set()
    while True:
        if c in cat_of_node:
            return cat_of_node[c]
        if c in sp or c not in tparent:
            return None
        sp.add(c)
        c = tparent[c]

qid_cat = {}
for q, c in qid_main.items():
    qid_cat[q] = category_of(c) or "UNMAPPED"
print(f"[A] 基础映射 {time.time()-t0:.0f}s", flush=True)

# ---------- 谓词森林构建(每个谓词一次,惰性缓存) ----------
_chained = {}
def chain_of(pred):
    if pred in _chained:
        return _chained[pred]
    vals = set(auxvals[pred].values())
    cp = AUX_CHAIN[pred]
    pmap = {}
    if vals:
        if cp == "P279":
            from collections import deque as dq2
            def _par(v):
                seen, d = {v}, dq2([(v, 0)])
                while d:
                    cur, h = d.popleft()
                    if h > 12:
                        return None
                    for p in parents.get(cur, ()):
                        if p in vals and p != v:
                            return p
                        if p not in seen:
                            seen.add(p); d.append((p, h + 1))
                return None
            pmap = {v: _par(v) for v in vals}
        else:
            chain_self = {}
            for l in open(f"{HOME}/qid_edges_ext.tsv"):
                q, _, rest = l.rstrip("\n").partition("\t")
                p2, _, v2 = rest.partition("\t")
                if p2 == cp and q in vals and q not in chain_self:
                    chain_self[q] = v2
            pmap = {v: (chain_self[v] if chain_self.get(v) in vals
                        and chain_self[v] != v else None) for v in vals}
    _chained[pred] = pmap
    return pmap

# ---------- 递归瀑布 ----------
cats_out = []
def emit(cid, name, m, qids=None):
    cats_out.append((cid, name, m))
    if qids:
        for q in qids:
            qid_cat[q] = cid

def aux_groups(pred, qids):
    """返回 {组根值: [qids]}(辅助森林按质量递归切后的组)。"""
    av = auxvals[pred]
    pmap = chain_of(pred)
    vset = set(pmap)
    ach = collections.defaultdict(list)
    for v, p in pmap.items():
        if p:
            ach[p].append(v)
    amass = collections.Counter()
    for q in qids:
        v = av.get(q)
        if v in vset:
            amass[v] += 1
    nn = {v: len(ach.get(v, ())) for v in vset}
    st = [v for v in vset if nn[v] == 0]
    while st:
        v = st.pop()
        p = pmap.get(v)
        if p:
            amass[p] += amass[v]
            nn[p] -= 1
            if nn[p] == 0:
                st.append(p)
    acat = {}
    def cut_aux(v):
        m = amass.get(v, 0)
        if m <= MAX_MASS:
            acat[v] = v
            return
        chs = [c for c in ach.get(v, ()) if amass.get(c, 0) > 0]
        if not chs:
            acat[v] = v
            return
        big = [c for c in chs if amass[c] >= MIN_MASS]
        small = [c for c in chs if amass[c] < MIN_MASS]
        if small:
            for c in small:
                acat[c] = ("@other", v)
        for c in big:
            cut_aux(c)
        if m - sum(amass[c] for c in chs) > 0:
            acat.setdefault(v, ("@self", v))
    for rv in (v for v, p in pmap.items() if not p):
        cut_aux(rv)
    def acat_of(v):
        sp = {v}
        while v not in acat and pmap.get(v) and pmap[v] not in sp:
            v = pmap[v]; sp.add(v)
        return acat.get(v)
    grp = collections.defaultdict(list)
    for q in qids:
        v = av.get(q)
        if v in vset:
            a = acat_of(v)
            if a is not None:
                key = a if isinstance(a, str) else f"{a[1]}{a[0]}"
                grp[(a if isinstance(a, str) else a[1], key)].append(q)
    # 根层小组合并成 @other
    smallk = [k for k, g in grp.items() if len(g) < MIN_MASS]
    if smallk and len(grp) - len(smallk) >= 1 and sum(len(grp[k]) for k in smallk) >= MIN_MASS:
        other = ("@rootother", "@rootother")
        for k in smallk:
            grp[other].extend(grp.pop(k))
    return grp

def split_qids(qids, name, prefix, preds_left):
    n = len(qids)
    if n <= MAX_MASS:
        emit(prefix, name, n, qids)
        return
    best = None
    for pred in preds_left:
        if not auxvals[pred]:
            continue
        pmap = chain_of(pred)
        av = auxvals[pred]
        covered = sum(1 for q in qids if av.get(q) in pmap)
        if covered < 0.4 * n:
            continue
        grp = aux_groups(pred, qids)
        if len(grp) < 2:
            continue
        mx = max(len(g) for g in grp.values())
        if mx > 0.7 * n:
            continue
        score = (-covered, mx / n)
        if best is None or score < best[0]:
            best = (score, pred, grp)
    if best:
        _, pred, grp = best
        rest = [p for p in preds_left if p != pred]
        for (v, _key), sub in sorted(grp.items(), key=lambda x: -len(x[1])):
            nm = (name + f"·{PNAME[pred]}其他" if str(v).startswith("@")
                  else name + f"·{PNAME[pred]}·{lab(v)}")
            split_qids(sub, nm, f"{prefix}|{pred}|{v}", rest)
        used = set(q for g in grp.values() for q in g)
        na = [q for q in qids if q not in used]
        if na:
            split_qids(na, name + f"·无{PNAME[pred]}值",
                       f"{prefix}|{pred}|noaux", rest)
        return
    k = (n + MAX_MASS - 1) // MAX_MASS
    qids = sorted(qids)
    size = (n + k - 1) // k
    for i in range(k):
        emit(f"{prefix}|shard{i}", f"{name}(分段{i+1}/{k})",
             len(qids[i*size:(i+1)*size]), qids[i*size:(i+1)*size])

cnt0 = collections.Counter(qid_cat.values())
by_cat = collections.defaultdict(list)
for q, c in qid_cat.items():
    by_cat[c].append(q)
oversize = [cid for cid, m in cnt0.items() if m > MAX_MASS]
print(f"[B] 超限 {len(oversize)} 个 {time.time()-t0:.0f}s", flush=True)
for cid in oversize:
    nm = "未归类长尾" if cid == "UNMAPPED" else lab(cid.split("#")[0])
    split_qids(by_cat[cid], nm, cid, list(AUX_PREDS))

# 保险丝:瀑布后仍有超限 → 强制分段(结构性保证)
cnt1 = collections.Counter(qid_cat.values())
for cid, m in cnt1.items():
    if m > MAX_MASS:
        qids = sorted(q for q, c in qid_cat.items() if c == cid)
        k = (m + MAX_MASS - 1) // MAX_MASS
        size = (m + k - 1) // k
        for i in range(k):
            for q in qids[i * size:(i + 1) * size]:
                qid_cat[q] = f"{cid}|fs{i}"
        cats_out.append((f"{cid}|fs0", f"{lab(cid.split('|')[0].split('#')[0])}(强制分段{i+1}/{k})", size))
        print(f"[B] 保险丝: {cid}({m:,}) → 强制分段 x{k}", flush=True)
print(f"[B] 瀑布完成 {time.time()-t0:.0f}s", flush=True)

cnt = collections.Counter(qid_cat.values())
with open(f"{HOME}/cut_categories{SUF}.tsv", "w", encoding="utf-8") as f:
    f.write("cat_id\tlabel\tentities\n")
    # 名称取自 emit 时的记录(分段/瀑布名),基础类目回退 label_of
    emitted = {c: (nm, m) for c, nm, m in cats_out}
    for cid, m in cnt.most_common():
        nm = emitted.get(cid, (None, None))[0]
        if nm is None:
            b = cid.split("#")
            nm = lab(b[0]) + ("(本级直挂)" if cid.endswith("#self")
                              else ("其他" if cid.endswith("#other") else ""))
            if cid == "UNMAPPED":
                nm = "未归类长尾"
        f.write(f"{cid}\t{nm}\t{m}\n")
with open(f"{HOME}/qid_cut_map{SUF}.tsv", "w", encoding="utf-8") as f:
    f.write("qid\tmain_class\tcat_id\n")
    for q, c in qid_main.items():
        f.write(f"{q}\t{c}\t{qid_cat[q]}\n")

ms = sorted(cnt.values(), reverse=True)
n = len(ms)
print(f"[7] 类目数={n:,} 覆盖={sum(ms):,}(应={len(qid_main):,}) max={ms[0]:,}")
over2 = [c for c, m in cnt.items() if m > MAX_MASS]
print(f"剩余超限 {len(over2)}: {over2[:5]}")
band = sum(1 for x in ms if MIN_MASS <= x <= MAX_MASS)
print(f"区间内[{MIN_MASS},{MAX_MASS}] {band:,} ({band/n:.0%}) "
      f"覆盖 {sum(x for x in ms if MIN_MASS <= x <= MAX_MASS):,}")
print(f"用时 {time.time()-t0:.0f}s")
