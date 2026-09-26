"""均衡切层 v3:最近桶多源 BFS 向下建树(与 32 桶分布同口径),再递归切层
输出:cut_categories.tsv / qid_cut_map.tsv / 统计"""
import collections, time

t0 = time.time()
HOME = "/home/ubuntu"
MIN_MASS, MAX_MASS = 200, 20000

BUCKETS = []
for line in open(f"{HOME}/p31_buckets.tsv", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#"):
        q, name = line.split("\t")[:2]
        BUCKETS.append((q, name))
BNAME = dict(BUCKETS)
PRIO = [q for q, _ in BUCKETS]
PRIO_IDX = {q: i for i, q in enumerate(PRIO)}
SRC = [q for q in PRIO if q != "Q5"]          # 31 源(Q5 特殊)

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
direct = collections.Counter(); seen = set()
for l in open(f"{HOME}/p31_all.tsv"):
    q, _, cls = l.rstrip("\n").partition("\t")
    if q in have and q not in seen:
        seen.add(q)
        direct["Q" + cls.split(",")[0]] += 1
print(f"[1] 直计 {len(direct):,} 类 {sum(direct.values()):,} 实体 {time.time()-t0:.0f}s", flush=True)

parents = {}
frontier = set(direct)
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
print(f"[2] 闭包 {len(parents):,} {time.time()-t0:.0f}s", flush=True)

# 3) 多源 BFS 向下:label=(dist,prio);tree_parent 记录最优来源。
# Q5 只作白名单源,不接收流入(垃圾环防):对 Q5 的松弛一律跳过。
label = {}
tparent = {}
seeds = list(SRC) + [q for q in HUMAN_SET if q != "Q5"]
for q in SRC:
    label[q] = (0, PRIO_IDX[q])
for q in HUMAN_SET:
    if q != "Q5":
        label.setdefault(q, (0, PRIO_IDX["Q5"]))
        tparent.setdefault(q, "Q5")
dq = collections.deque(q for q in seeds if q in label)
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
print(f"[3] 树覆盖 {len(label):,} 类 {time.time()-t0:.0f}s", flush=True)

# 4) 质量(树边 = tparent)
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
print(f"[4] 质量完成 {time.time()-t0:.0f}s", flush=True)

# 5) 未挂类(不在 label):并入"未归类"链顶树
orphan = [c for c in direct if c not in label]
def up_top(c):
    cur, sp = c, set()
    while cur in parents and cur not in sp:
        sp.add(cur)
        nxt = parents[cur][0]
        if nxt in label:
            return None                      # 链上其实可达桶:跳过
        cur = nxt
    return cur
otops = collections.defaultdict(int)
for c in orphan:
    t = up_top(c)
    otops[t if t else c] += direct[c]
print(f"[5] 未挂类 {len(orphan):,} 个 / {sum(direct[c] for c in orphan):,} 实体", flush=True)

# 6) 递归切
cats = []
cat_of_node = {}
def emit(cid, name, m, d, root):
    cats.append((cid, name, m, d, root))
def cut(node, depth, root, ch_key=None):
    ch = ch_key if ch_key is not None else tch
    m = mass.get(node, 0)
    if m <= MAX_MASS:
        cat_of_node[node] = node
        emit(node, lab(node), m, depth, root)
        return
    chs = [c for c in ch.get(node, ()) if mass.get(c, 0) > 0]
    if not chs:
        cat_of_node[node] = node
        emit(node, lab(node) + "(超限·不可再分)", m, depth, root)
        return
    big = [c for c in chs if mass[c] >= MIN_MASS]
    small = [c for c in chs if mass[c] < MIN_MASS]
    if small:
        oid = f"{node}#other"
        for c in small:
            cat_of_node[c] = oid
        emit(oid, lab(node) + "-其他", sum(mass[c] for c in small), depth + 1, root)
    for c in big:
        cut(c, depth + 1, root)
    self_m = m - sum(mass[c] for c in chs)
    if self_m > 0:
        cat_of_node.setdefault(node, f"{node}#self")
        emit(f"{node}#self", lab(node) + "(本级直挂)", self_m, depth, root)

for q in PRIO:
    if q in label or q in mass:
        cut(q, 0, q)
# 未归类:按链顶切不了(无树),整体一个类目+明细留表
om = sum(direct[c] for c in orphan)
if om:
    emit("UNMAPPED", "未归类长尾(未上卷)", om, 0, "UNMAPPED")
print(f"[6] 类目 {len(cats):,} {time.time()-t0:.0f}s", flush=True)

# 7) 类目归属 + qid 映射
def category_of(c):
    sp = set()
    while True:
        if c in cat_of_node:
            return cat_of_node[c]
        if c in sp or c not in tparent:
            return None
        sp.add(c)
        c = tparent[c]
cat_of_cls = {c: category_of(c) for c in direct}

with open(f"{HOME}/cut_categories.tsv", "w", encoding="utf-8") as f:
    f.write("cat_id\tlabel\tentities\tdepth\troot_bucket\n")
    for cid, name, m, d, rb in sorted(cats, key=lambda x: -x[2]):
        f.write(f"{cid}\t{name}\t{m}\t{d}\t{BNAME.get(rb, '未归类')}\n")
done = set()
with open(f"{HOME}/qid_cut_map.tsv", "w", encoding="utf-8") as f:
    f.write("qid\tmain_class\tcat_id\n")
    for l in open(f"{HOME}/p31_all.tsv"):
        q, _, cls = l.rstrip("\n").partition("\t")
        if q in have and q not in done:
            done.add(q)
            c = "Q" + cls.split(",")[0]
            f.write(f"{q}\t{c}\t{cat_of_cls.get(c) or 'UNMAPPED'}\n")

ms = sorted((c[2] for c in cats), reverse=True)
n = len(ms)
print(f"[7] 类目数={n:,} 覆盖={sum(ms):,}(应≈{sum(direct.values()):,})")
print(f"中位={ms[n//2]:,} p90={ms[int(n*0.1)]:,} max={ms[0]:,}")
over = [(c[1], c[2]) for c in cats if c[2] > MAX_MASS]
print(f"超限 {len(over)}: {over[:6]}")
band = sum(1 for x in ms if MIN_MASS <= x <= MAX_MASS)
print(f"区间内[{MIN_MASS},{MAX_MASS}] 类目 {band:,} ({band/n:.0%}), 覆盖实体 "
      f"{sum(x for x in ms if MIN_MASS <= x <= MAX_MASS):,}")
print(f"用时 {time.time()-t0:.0f}s")
