# P31 自有体系分布:qid 主类 → P279 闭包上卷 → 小顶层桶,出报告
# 输入(sgx /home/ubuntu):p31_all.tsv / p279_all.tsv / haveimg_final.txt / p31_buckets.tsv
# 输出:p31_distribution_report.md + p31_bucket_counts.tsv + p31_unmapped_top.tsv
# 用法: python3 p31_rollup.py [--max-anc 500]
import collections, os, sys, time

t0 = time.time()
HOME = os.environ.get("DEMIWTG_P31_HOME", "/home/ubuntu")
MAX_ANC = 500          # 单类祖先集上限(防坏数据炸内存)
REPORT_TOP_CLASSES = 5
UNMAPPED_TOP = 30

# ---------- 桶表(优先级=行序,具体在前泛化在后) ----------
BUCKETS = []           # [(qid, zh名)]
for line in open(f"{HOME}/p31_buckets.tsv", encoding="utf-8"):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    qid, name = line.split("\t")[:2]
    BUCKETS.append((qid, name))
BUCKET_NAME = dict(BUCKETS)
BUCKET_ORDER = {q: i for i, (q, _) in enumerate(BUCKETS)}

# ---------- 1) 有图集 & 主类计数 ----------
have = set()
for l in open(f"{HOME}/haveimg_final.txt"):
    l = l.strip()
    if l.startswith("Q") and l[1:].isdigit():
        have.add(l)

have_cls = collections.Counter()      # 主类 -> 有图实体数
multi = 0                              # 多 P31 实体数
n_have_rows = 0
for l in open(f"{HOME}/p31_all.tsv"):
    q, _, cls = l.rstrip("\n").partition("\t")
    if q not in have:
        continue
    n_have_rows += 1
    ids = cls.split(",")
    if len(ids) > 1:
        multi += 1
    have_cls["Q" + ids[0]] += 1
print(f"[1] have={len(have):,} 有图且有P31={n_have_rows:,} "
      f"(无P31 {len(have)-n_have_rows:,}) 多P31占比={multi/max(n_have_rows,1):.1%} "
      f"主类数={len(have_cls):,}  {time.time()-t0:.0f}s", flush=True)

used = set(have_cls)

# ---------- 2) 迭代闭包:只载 used 及其祖先的 P279 边 ----------
parents = {}                           # qid -> [父qid]
frontier = set(used)
for it in range(12):
    want = frontier
    found = 0
    new = set()
    for l in open(f"{HOME}/p279_all.tsv"):
        q, _, rest = l.rstrip("\n").partition("\t")
        if q in want and q not in parents:
            ps = ["Q" + x for x in rest.split("\t")[0].split(",")]
            parents[q] = ps
            found += 1
            for p in ps:
                if p not in parents:
                    new.add(p)
    frontier = new - set(parents)
    print(f"[2] pass{it}: +{found} 类边, 待扩 {len(frontier):,}  {time.time()-t0:.0f}s", flush=True)
    if not frontier:
        break

# ---------- 3) 标签:p31_labels.tsv(API 补齐)优先,p279_all.tsv 标签列兜底 ----------
labels = {}
need = used | set(parents) | set(BUCKET_NAME)
for l in open(f"{HOME}/p279_all.tsv"):
    parts = l.rstrip("\n").split("\t")
    if len(parts) >= 4 and parts[0] in need and parts[0] not in labels:
        en = "" if parts[2] == "nguage" else parts[2]     # 本趟标签列坏值过滤
        zh = "" if parts[3] == "nguage" else parts[3]
        labels[parts[0]] = (en, zh)
try:
    for l in open(f"{HOME}/p31_labels.tsv", encoding="utf-8"):
        parts = l.rstrip("\n").split("\t")
        if len(parts) >= 3:
            labels[parts[0]] = (parts[1], parts[2])
except FileNotFoundError:
    print("[3] 无 p31_labels.tsv,仅用 p279_all.tsv 标签列(本趟可能为坏值)", flush=True)

def lab(q):
    en, zh = labels.get(q, ("", ""))
    return zh or en or q

# 桶标签校验(防手滑 QID)
print("[3] 桶标签校验:", flush=True)
for q, name in BUCKETS:
    en, zh = labels.get(q, ("", ""))
    print(f"    {q}\t表内名={name}\ten={en or '?'}\tzh={zh or '?'}", flush=True)

# ---------- 4) 人类封闭集:P279 图有跨域脏链(交通基建→人类、
# 宗教建筑→宗教→组织→人群→人类 等)。Q5 的直接/隔代子类中
# 混入大量组织/活动/食物类脏边,人工审定后白名单见 p31_human_set.tsv ----------
HUMAN_SET = {"Q5"}
try:
    for l in open(f"{HOME}/p31_human_set.tsv", encoding="utf-8"):
        q = l.strip().split("\t")[0]
        if q.startswith("Q"):
            HUMAN_SET.add(q)
    print(f"[4] 人类封闭集:Q5 + 审定白名单 {len(HUMAN_SET)-1} 项", flush=True)
except FileNotFoundError:
    print("[4] 无 p31_human_set.tsv,人类桶仅认主类=Q5(保守)", flush=True)

# ---------- 5) 上卷分配:Q5 走封闭集;其余桶=分层 BFS 最短路径优先,
# 同深度按桶优先级;Q5 不作为闭包目标 ----------
MAX_LEVEL = 15
MAX_VISIT = 3000                       # 单类 BFS 访问上限(坏数据防护)
bucket_ent = collections.Counter()     # 桶 -> 有图实体数
bucket_cls = collections.Counter()     # 桶 -> 主类数
bucket_top = collections.defaultdict(list)  # 桶 -> [(主类,数)]
unmapped = collections.Counter()       # 未命中桶的主类

def assign(c):
    """主类 c 应落桶;自身是桶直接命中;Q5 走封闭集;
    其余逐层向外,首命中层内取优先级最高(距离主导,脏长链必输)。"""
    if c in BUCKET_ORDER and c != "Q5":
        return c
    if c in HUMAN_SET:
        return "Q5"
    seen = {c, "Q5"}                   # Q5 为终结点:其父链(杂食动物等)是脏域
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

for c, n in have_cls.most_common():
    hit = assign(c)
    if hit is None:
        unmapped[c] = n
    else:
        bucket_ent[hit] += n
        bucket_cls[hit] += 1
        bucket_top[hit].append((c, n))
print(f"[5] 上卷完成  {time.time()-t0:.0f}s", flush=True)

# ---------- 6) 报告 ----------
have_total = len(have)
with_p31 = n_have_rows
mapped = sum(bucket_ent.values())
lines = []
lines.append("# P31 自有体系分布(有图 QID 集合)\n")
lines.append(f"- 有图 QID(haveimg_final):**{have_total:,}**")
lines.append(f"- 有 P31 主类:{with_p31:,}(无 P31:{have_total-with_p31:,})")
lines.append("- 口径:主类=P31 首个 mainsnak 值(抽取窗会混入 reference 的"
             "numeric-id,故不统计多值占比)")
lines.append(f"- 上卷命中:{mapped:,}({mapped/have_total:.1%});"
             f"未上卷:{with_p31-mapped:,}({(with_p31-mapped)/have_total:.1%})\n")
lines.append("## 顶层桶分布(优先级序=具体→泛化)\n")
lines.append("| 桶 | 有图实体 | 占有图 | 累计 | 类数 | 头部主类(top5) |")
lines.append("|---|---:|---:|---:|---:|---|")
cum = 0
for q, n in bucket_ent.most_common():
    cum += n
    tops = "、".join(f"{lab(c)}({c}) {k:,}" for c, k in
                     sorted(bucket_top[q], key=lambda x: -x[1])[:REPORT_TOP_CLASSES])
    name = f"{BUCKET_NAME[q]}({q})"
    lines.append(f"| {name} | {n:,} | {n/have_total:.1%} | {cum/have_total:.1%} "
                 f"| {bucket_cls[q]:,} | {tops} |")
lines.append(f"| (未上卷) | {with_p31-mapped:,} | {(with_p31-mapped)/have_total:.1%} | — "
             f"| {len(unmapped):,} | 见 p31_unmapped_top.tsv |")
lines.append(f"| (无P31) | {have_total-with_p31:,} | {(have_total-with_p31)/have_total:.1%} | — "
             f"| — | — |")
lines.append("\n## 原始主类 top100(未上卷,参照用)\n")
lines.append("| 主类 | 有图实体 | 占比 |")
lines.append("|---|---:|---:|")
for c, n in have_cls.most_common(100):
    lines.append(f"| {lab(c)}({c}) | {n:,} | {n/have_total:.2%} |")
rep = "\n".join(lines)
open(f"{HOME}/p31_distribution_report.md", "w", encoding="utf-8").write(rep)
with open(f"{HOME}/p31_bucket_counts.tsv", "w", encoding="utf-8") as f:
    f.write("qid\tbucket\tentities\tclasses\n")
    for q, n in bucket_ent.most_common():
        f.write(f"{q}\t{BUCKET_NAME[q]}\t{n}\t{bucket_cls[q]}\n")
with open(f"{HOME}/p31_unmapped_top.tsv", "w", encoding="utf-8") as f:
    for c, n in unmapped.most_common(500):
        en, zh = labels.get(c, ("", ""))
        f.write(f"{c}\t{n}\t{en}\t{zh}\n")
print(f"[6] 报告完成 用时 {time.time()-t0:.0f}s", flush=True)
