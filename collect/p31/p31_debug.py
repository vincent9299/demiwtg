"""调试:从指定类出发沿 P279 找到桶 QID 的一条具体路径,定位上卷异常"""
import collections, sys

HOME = "/home/ubuntu"
targets = sys.argv[1:] or ["Q16521", "Q16970", "Q757587"]
probe = {"Q5", "Q8054", "Q16521", "Q811979", "Q486972", "Q11424"}

# 需要的边:从 targets 出发的全闭包(迭代扩,直到不再增长,最多 20 轮)
parents = {}
frontier = set(targets)
for it in range(20):
    want = frontier
    new = set()
    for l in open(f"{HOME}/p279_all.tsv"):
        q, _, rest = l.rstrip("\n").partition("\t")
        if q in want and q not in parents:
            ps = ["Q" + x for x in rest.split("\t")[0].split(",")]
            parents[q] = ps
            new |= {p for p in ps if p not in parents}
    frontier = new - set(parents)
    if not frontier:
        break
print(f"parents loaded: {len(parents):,} (passes {it+1})")

# BFS 找到各 probe 桶的最短路径
for t in targets:
    prev = {t: None}
    dq = collections.deque([t])
    hitpath = {}
    while dq:
        cur = dq.popleft()
        for p in parents.get(cur, ()):
            if p not in prev:
                prev[p] = cur
                if p in probe and p not in hitpath:
                    path = [p]
                    x = cur
                    while x is not None:
                        path.append(x)
                        x = prev[x] if prev[x] is not None else None
                    # 回溯
                    chain = []
                    node = p
                    while node is not None:
                        chain.append(node)
                        node = prev[node]
                    hitpath[p] = " -> ".join(chain)
                dq.append(p)
    print(f"\n== {t} ==")
    for b, path in list(hitpath.items())[:4]:
        print(f"  reaches {b}: {path}")
    if not hitpath:
        print("  reaches none of probes")
