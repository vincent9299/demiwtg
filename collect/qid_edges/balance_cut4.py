"""均衡切层 v4:v3 树切 + 超限类目谓词瀑布(P106/P171/P140/P131/P136)
+ 无维度兜底分段。以 qid_cat 映射为唯一事实源,保证无超限。
环境:QC_MAX/QC_MIN/QC_SUFFIX/QC_AUX;输入含 qid_edges_ext.tsv
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

auxvals = collections.defaultdict(dict)
try:
    for l in open(f"{HOME}/qid_edges_ext.tsv"):
        q, _, rest = l.rstrip("\n").partition("\t")
        pred, _, val = rest.partition("\t")
        if pred in AUX_PREDS:
            auxvals[pred].setdefault(q, val)
    ext_ok = True
except FileNotFoundError:
    ext_ok = False
aux_value_qids = set()
for pred in AUX_PREDS:
    if AUX_CHAIN[pred] == "P279":
        aux_value_qids |= set(auxvals[pred].values())
print(f"[1b] 辅助值 {sum(len(v) for v in auxvals.values()):,} 条", flush=True)

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
print(f"[2] 树+质量完成 {time.time()-t0:.0f}s", flush=True)

# Phase A:P31/P279 树切(先给全部 qid 一个基础类目)
cat_of_node = {}
def cut(node, depth, root):
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
        cut(c, depth + 1, root)
    self_m = m - sum(mass[c] for c in chs)
    if self_m > 0:
        cat_of_node.setdefault(node, f"{node}#self")

for q in PRIO:
    if q in label or q in mass:
        cut(q, 0, q)

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
print(f"[A] 基础映射完成 {time.time()-t0:.0f}s", flush=True)

# Phase B(v4.3):超限类目 → 选谓词(覆盖优先+有效分裂) → 辅助森林内递归 cut
cnt0 = collections.Counter(qid_cat.values())
by_cat = collections.defaultdict(list)
for q, c in qid_cat.items():
    by_cat[c].append(q)
oversize = [cid for cid, m in cnt0.items()
            if cid != "UNMAPPED" and m > MAX_MASS]
print(f"[B] 超限 {len(oversize)} 个", flush=True)

chain_par = {}
if oversize and ext_ok:
    for pred in AUX_PREDS:
        vals = set(auxvals[pred].values())
        if not vals:
            continue
        cp = AUX_CHAIN[pred]
        if cp == "P279":
            memo = {}
            def _par(v, vals=vals):
                from collections import deque as _dq
                seen, d = {v}, _dq([(v, 0)])
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
            for v in vals:
                memo[v] = _par(v)
            chain_par[pred] = memo
        else:
            chain_self = {}
            for l in open(f"{HOME}/qid_edges_ext.tsv"):
                q, _, rest = l.rstrip("\n").partition("\t")
                p2, _, v2 = rest.partition("\t")
                if p2 == cp and q in vals and q not in chain_self:
                    chain_self[q] = v2
            chain_par[pred] = {v: (chain_self[v] if chain_self.get(v) in vals
                                   and chain_self[v] != v else None) for v in vals}
    print(f"[B] 辅助森林就绪 {time.time()-t0:.0f}s", flush=True)

PNAME = {"P106": "职业", "P171": "物种树", "P140": "宗派",
         "P131": "属地", "P136": "体裁"}

PRED_TAG = {"P106": "职业", "P171": "物种树", "P140": "宗派",
           "P131": "属地", "P136": "体裁"}

GLOBAL_ORDER = {}                    # pred -> 子先父后拓扑序
for _pred in AUX_PREDS:
    _pm = chain_par.get(_pred, {})
    _nodes = set(_pm) | set(v for v in _pm.values() if v)
    _ch = collections.defaultdict(list)
    for _v, _p in _pm.items():
        if _p:
            _ch[_p].append(_v)
    _indeg = {v: len(_ch.get(v, ())) for v in _nodes}
    _st = [v for v in _nodes if _indeg[v] == 0]
    _order = []
    _pending = dict(_indeg)
    _par_of = {v: _pm.get(v) for v in _nodes}
    _kids = collections.defaultdict(list)
    for _v, _p in _pm.items():
        if _p:
            _kids[_p].append(_v)
    while _st:
        v = _st.pop()
        _order.append(v)
        _p = _par_of.get(v)
        if _p is not None:
            _pending[_p] -= 1
            if _pending[_p] == 0:
                _st.append(_p)
    GLOBAL_ORDER[_pred] = _order

def try_pred(pred, qids):
    """返回 (组->qids, 根map) 或 None;组含 @other 合并小组。"""
    _t = time.time()
    av = auxvals.get(pred, {})
    pmap = chain_par.get(pred, {})
    cnt_v = collections.Counter()
    for q in qids:
        v = av.get(q)
        if v:
            cnt_v[v] += 1
    active = set()
    for v in cnt_v:
        cur = v
        while cur is not None and cur not in active:
            active.add(cur)
            cur = pmap.get(cur)
    sub = collections.Counter(cnt_v)
    for node in GLOBAL_ORDER.get(pred, ()):
        if node in active:
            p = pmap.get(node)
            if p is not None:
                sub[p] += sub[node]
    roll = {}
    for v in cnt_v:
        cur, sp = v, {v}
        while sub.get(cur, 0) < MIN_MASS:
            p = pmap.get(cur)
            if not p or p in sp:
                break
            cur = p; sp.add(p)
        roll[v] = cur
    grp = collections.defaultdict(list)
    for q in qids:
        v = av.get(q)
        if v:
            grp[roll[v]].append(q)
    small = [v for v in list(grp) if len(grp[v]) < MIN_MASS]
    if small and (sum(len(grp[v]) for v in small) >= MIN_MASS
                  or len(grp) - len(small) >= 1):
        for v in small:
            grp["@other"] = grp.get("@other", []) + grp.pop(v)
    if not grp:
        return None
    print(f"    try {pred}: {len(grp):,} 组 {time.time()-_t:.1f}s", flush=True)
    small = [v for v in grp if len(grp[v]) < MIN_MASS and v != "@noaux"]
    if len(grp) - len(small) >= 1:
        if sum(len(grp[v]) for v in small) >= MIN_MASS or len(grp) - len(small) >= 1:
            for v in small:
                grp.setdefault("@other", []).extend(grp.pop(v))
    return dict(grp)

def split_qids(qids, name, prefix, preds_left, depth=0):
    """递归瀑布:超限组用剩余谓词继续拆;全败则分段;保证叶子 ≤MAX。"""
    n = len(qids)
    if n <= MAX_MASS:
        emit(prefix, name, n)
        for q in qids:
            qid_cat[q] = prefix
        return
    best = None
    for pred in preds_left:
        grp = try_pred(pred, qids)
        if not grp or len(grp) < 2:
            continue
        mx = max(len(v) for v in grp.values())
        covered = sum(len(v) for v in grp.values())
        # 可用门槛:覆盖至少三成(拦低覆盖脏谓词),覆盖多者优先,再比最大组
        if covered < max(MIN_MASS, 0.3 * n):
            continue
        score = (-covered, mx / max(covered, 1))
        if best is None or score < best[0]:
            best = (score, pred, grp)
    if best:
        _, pred, grp = best
        rest = [p for p in preds_left if p != pred]
        for v, sub in sorted(grp.items(), key=lambda x: -len(x[1])):
            subname = (name + f"·{PRED_TAG[pred]}其他" if v == "@other"
                       else name + f"·{PRED_TAG[pred]}·{lab(v)}")
            print(f"      {'  '*depth}→ {subname} ({len(sub):,})", flush=True)
            split_qids(sub, subname, f"{prefix}|{pred}|{v}", rest, depth + 1)
        got = set()
        for sub in grp.values():
            got.update(sub)
        noaux = [q for q in qids if q not in got]
        if noaux:
            print(f"      {'  '*depth}→ {name}·无值 ({len(noaux):,})", flush=True)
            split_qids(noaux, name + "·无值", f"{prefix}|{pred}|noaux",
                       rest, depth + 1)
        return
    # 全部谓词无效:兜底分段
    k = (n + MAX_MASS - 1) // MAX_MASS
    qids = sorted(qids)
    size = (n + k - 1) // k
    for i in range(k):
        part = qids[i * size:(i + 1) * size]
        emit(f"{prefix}|shard{i}", f"{name}(分段{i+1}/{k})", len(part))
        for q in part:
            qid_cat[q] = f"{prefix}|shard{i}"

cat_emit = []
def emit(cid, name, m):
    cat_emit.append((cid, name, m))

for cid in oversize:
    qids = by_cat[cid]
    name = lab(cid.split("#")[0])
    print(f"[B] 处理 {name} ({len(qids):,})", flush=True)
    usable = []
    for pred in AUX_PREDS:                   # 顶层一次性评估,剪掉低覆盖
        grp = try_pred(pred, qids)
        if not grp or len(grp) < 2:
            continue
        cov = sum(len(v) for v in grp.values())
        if cov >= max(MIN_MASS, 0.3 * len(qids)):
            usable.append(pred)
    print(f"[B] {name} 可用谓词: {usable}", flush=True)
    split_qids(qids, name, cid, usable)
    print(f"[B] {name} 完成 {time.time()-t0:.0f}s", flush=True)

# 落盘:类目表(瀑布结果)+映射
def label_of2(cid, name):
    return name

cntw = collections.Counter(qid_cat.values())
nm_of = {}
for cid, name, m in cat_emit:
    nm_of[cid] = name
def _fallback(cid):
    if cid == "UNMAPPED":
        return "未归类长尾"
    parts = cid.split("|")
    b = lab(parts[0].split("#")[0])
    if len(parts) == 2 and parts[1].startswith("shard"):
        return f"{b}(分段{int(parts[1][5:])+1})"
    if len(parts) == 3 and parts[2] == "noaux":
        return b + f"·无{PRED_TAG.get(parts[1], parts[1])}值"
    if len(parts) >= 3 and parts[2].startswith("@"):
        return b + f"·{PRED_TAG.get(parts[1], parts[1])}其他"
    if cid.endswith("#self"):
        return b + "(本级直挂)"
    if cid.endswith("#other"):
        return b + "·其他"
    return b
with open(f"{HOME}/cut_categories{SUF}.tsv", "w", encoding="utf-8") as f:
    f.write("cat_id\tlabel\tentities\n")
    for cid, m in cntw.most_common():
        f.write(f"{cid}\t{nm_of.get(cid) or _fallback(cid)}\t{m}\n")
with open(f"{HOME}/qid_cut_map{SUF}.tsv", "w", encoding="utf-8") as f:
    f.write("qid\tmain_class\tcat_id\n")
    for q, c in qid_main.items():
        f.write(f"{q}\t{c}\t{qid_cat[q]}\n")

cnt = collections.Counter(qid_cat.values())
ms = sorted(cnt.values(), reverse=True)
n = len(ms)
print(f"[7] 类目数={n:,} 覆盖={sum(ms):,}(应={len(qid_main):,}) max={ms[0]:,}")
over2 = [c for c, m in cnt.items() if m > MAX_MASS]
print(f"剩余超限 {len(over2)}: {over2[:5]}")
band = sum(1 for x in ms if MIN_MASS <= x <= MAX_MASS)
print(f"区间内[{MIN_MASS},{MAX_MASS}] {band:,} ({band/n:.0%}) 覆盖 {sum(x for x in ms if MIN_MASS <= x <= MAX_MASS):,}")
print(f"用时 {time.time()-t0:.0f}s")
